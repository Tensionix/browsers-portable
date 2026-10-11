"""The clean-up operations of the program: its own input and output, and its workspace.

The file keeps the name it had in the template the program grew from; the sample operations of
that template and the command bar of the former window are gone with that window (2.0.0).
"""
from __future__ import annotations

from pathlib import Path
import os
import shutil

from system_core.core.jobs import JobContext
from system_core.core.paths import expand_percent
from system_core.services.long_paths import long_path, plain_path


def _project_path(project_root: Path, value: object, default: str) -> Path:
    text = str(value if value not in {None, ""} else default).strip().strip('"')
    path = Path(expand_percent(text)).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _input_dir(context: JobContext) -> Path:
    return _project_path(context.paths.root, context.operation.parameters.get("gui_input_dir"), "input")


def _output_dir(context: JobContext) -> Path:
    return _project_path(context.paths.root, context.operation.parameters.get("gui_output_dir"), "output")


def _is_inside(child: Path, parent: Path) -> bool:
    """True iff `child` (after resolve) is inside `parent` (after resolve).

    Uses os.path.commonpath which is stricter than `parent in child.parents`
    when `child == parent` and works correctly across symlinks because both
    sides are already resolved.
    """
    try:
        child_resolved = str(child.resolve())
        parent_resolved = str(parent.resolve())
    except OSError:
        return False
    try:
        common = os.path.commonpath([child_resolved, parent_resolved])
    except ValueError:
        # Different drives on Windows, etc.
        return False
    return common == parent_resolved


def _assert_managed_folder(context: JobContext, folder: Path, label: str) -> None:
    """Refuse a folder that is not the program's own `label` folder, or a folder inside it.

    "Inside the project root" was the whole check once, and the root lies inside itself: with
    Source set to the project - `--source .` at the console door - "Clear I/O" emptied the
    program. Nothing is created or deleted here.
    """
    root = context.paths.root.resolve()
    if folder.is_symlink():
        raise RuntimeError(f"{label} is a symbolic link. Cleanup blocked for safety.")
    home = (root / label).resolve()
    if not _is_inside(folder.resolve(), root):
        raise RuntimeError(f"{label} path is outside project root. Cleanup blocked.")
    if not _is_inside(folder.resolve(), home):
        raise RuntimeError(
            f"{label} path is not the program's own {label} folder ({home}): {folder.resolve()}. "
            f"Cleanup blocked: this operation empties only input and output of the program.")


def _clean_managed_folder(context: JobContext, folder: Path, label: str) -> dict[str, object]:
    _assert_managed_folder(context, folder, label)
    folder.mkdir(parents=True, exist_ok=True)
    folder_resolved = folder.resolve()

    removed = 0
    skipped: list[str] = []
    for item in folder.iterdir():
        try:
            if item.is_symlink():
                item.unlink()
            elif item.is_dir():
                if not _is_inside(item, folder_resolved):
                    skipped.append(f"{item.name} (escapes {label})")
                    continue
                shutil.rmtree(item)
            else:
                item.unlink()
            removed += 1
            context.log(f"Removed from {label}: {item.name}")
        except OSError as exc:
            skipped.append(f"{item.name} ({exc})")

    return {"folder": label, "removed_items": removed, "skipped_items": skipped}


def cleanup_input_output(context: JobContext) -> dict[str, object]:
    context.log("Cleaning managed input/output folders.")
    # Both folders are judged before either is touched: a wrong Target must not be found out
    # after the input is already empty.
    _assert_managed_folder(context, _input_dir(context), "input")
    _assert_managed_folder(context, _output_dir(context), "output")
    input_result = _clean_managed_folder(context, _input_dir(context), "input")
    context.progress(0.5)
    if context.cancelled():
        context.log("Input/output cleanup cancelled by user after input cleanup.")
        return {"cancelled": True, "input": input_result}

    output_result = _clean_managed_folder(context, _output_dir(context), "output")
    context.progress(1.0)
    total_removed = int(input_result["removed_items"]) + int(output_result["removed_items"])
    total_skipped = len(input_result["skipped_items"]) + len(output_result["skipped_items"])
    context.log(f"Input/output cleanup complete. Removed: {total_removed}, skipped: {total_skipped}")
    return {"input": input_result, "output": output_result, "removed_items": total_removed, "skipped_items": total_skipped}


def cleanup_workspace(context: JobContext) -> dict[str, object]:
    """Delete the working material: the workspace of the program, and what the builder keeps
    beside the builds in the Target - `Portable\\_tmp` and `Portable\\_archives`, by those two
    names and no other.

    Safety rules for the workspace:
    - workspace must resolve to a path inside the project root.
    - Symlinks inside workspace are unlinked, never traversed. shutil.rmtree
      with follow_symlinks=False is not enough on Windows because Path.rmtree
      via shutil treats junctions inconsistently — we handle symlinks first
      and explicitly.
    - Non-symlink subdirectories are checked again before removal: their
      resolved path must still be inside workspace. This catches the case
      where workspace itself contains a directory whose resolve() escapes
      (e.g. a junction or reparse point set up between the outer check and
      iteration).
    """
    workspace = context.paths.workspace
    root = context.paths.root.resolve()

    # Reject workspaces that are themselves symlinks pointing outside root.
    if workspace.is_symlink():
        raise RuntimeError("Workspace is a symbolic link. Cleanup blocked for safety.")

    workspace_resolved = workspace.resolve()
    if not _is_inside(workspace_resolved, root):
        raise RuntimeError("Workspace path is outside project root. Cleanup blocked.")

    workspace.mkdir(parents=True, exist_ok=True)

    removed = 0
    skipped: list[str] = []

    for item in workspace.iterdir():
        # The notes of the Defender guard are not working material: among them is the list of
        # exclusions a run did not take back, which "Defender: remove exclusion" reads.
        if item.name == DEFENDER_GUARD_FOLDER:
            continue
        # Symlinks: unlink the link itself, do NOT follow.
        if item.is_symlink():
            try:
                item.unlink()
                removed += 1
                context.log(f"Removed symlink: {item.name}")
            except OSError as exc:
                skipped.append(f"{item.name} (symlink, {exc})")
            continue

        # Re-verify directory containment before recursive delete.
        if item.is_dir():
            if not _is_inside(item, workspace_resolved):
                skipped.append(f"{item.name} (escapes workspace after resolve)")
                context.log(f"Skipped (escapes workspace): {item.name}")
                continue
            shutil.rmtree(item)
            removed += 1
            continue

        # Regular file.
        item.unlink()
        removed += 1

    # The large part of the working material is not in the workspace of the program at all: the
    # builder keeps its downloads and what it unpacks beside the builds, in the Target. The
    # button promised to delete them and emptied the one folder that never held them.
    freed = 0
    def writable_then_again(function, item_path, _error) -> None:
        # Read-only files are the usual reason a tree does not go.
        os.chmod(item_path, 0o700)
        function(item_path)

    for folder in _published_work_folders(context):
        plain = plain_path(folder)
        if not folder.exists():
            continue
        if plain.is_symlink() or os.path.isjunction(plain) or not folder.is_dir():
            skipped.append(f"{plain} (not a folder of its own)")
            context.log(f"Skipped (a link or not a folder): {plain}")
            continue
        size = sum(item.stat().st_size for item in folder.rglob("*") if item.is_file())
        try:
            shutil.rmtree(folder, onerror=writable_then_again)
        except OSError as exc:
            skipped.append(f"{plain} ({exc})")
            context.log(f"Skipped: {plain} ({exc})")
            continue
        removed += 1
        freed += size
        context.log(f"Removed from the Target: {plain.parent.name}\\{plain.name} ({size / 1024 / 1024:,.0f} MB)")

    context.log(f"Workspace cleanup complete. Removed: {removed}, skipped: {len(skipped)}")
    if skipped:
        context.log("Skipped items: " + ", ".join(skipped))
    context.progress(1.0)
    return {"removed_items": removed, "skipped_items": skipped, "freed_bytes": freed}


# The folder the Defender guard of the builder keeps its notes in, inside the workspace.
DEFENDER_GUARD_FOLDER = ".defender_guard"


def _published_work_folders(context: JobContext) -> list[Path]:
    """What the builder keeps beside the builds it publishes: its downloads and its work folder.

    In the long form: what was unpacked there may lie deeper than Windows opens by default.
    """
    portable = _output_dir(context) / "Portable"
    return [long_path(portable / "_tmp"), long_path(portable / "_archives")]
