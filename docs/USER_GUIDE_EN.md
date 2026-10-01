# Audion Browsers Portable - user guide

**Contents**

- [How the window works](#how-the-window-works)
- [First run](#first-run)
- [Download Official Files](#download-official-files)
- [Which browsers the DLL builder supports, and why](#which-browsers-the-dll-builder-supports-and-why)
- [What is inside a build](#what-is-inside-a-build)
- [Updating](#updating)
- [The certificates](#the-certificates)
- [Build settings](#build-settings)
- [Worth knowing](#worth-knowing)

This program makes portable browsers: ones that live in a folder, start from
anywhere, and are never installed into Windows. Bookmarks, passwords and tabs
stay inside that folder, so it travels on a flash drive or goes to someone else
as it is.

## How the window works

Five tabs across the top: `INSTALL`, `DOWNLOADS`, `UPDATE`, `CERTIFICATE`, `SERVICE`. That is
the whole menu: press a tab and its commands, with their settings, are right
underneath — there is nowhere to descend into. Above the tabs sits a service
strip with the folder cleanups, which belong to the program as a whole rather
than to any one tab.

Every choice is made with buttons. The chosen one is washed with blue, the rest
stay outlined. Browsers are outlined each in its own colour, so the row is read
by name rather than by position.

Captions are short. The explanation — what will happen, what a wrong answer
costs — appears in the tooltip when the pointer rests on the control.

## First run

1. Tab `SERVICE` → `7-ZIP`. Nothing can be unpacked without it.
2. Tab `INSTALL` → pick the browsers with the buttons and press `BUILD`.

Each browser is 120–240 MB of download and about half a gigabyte on disk. The
builds appear in the Target folder (`output\Portable`); start one with
`<Browser> Portable.cmd` in its root.

If one browser fails, the others are still built — the failures are listed at the
end.

## Download Official Files

Open `DOWNLOADS`, choose browsers and architecture, then press `DOWNLOAD`.
The original files are saved under `Browser Downloads` in the Target folder.
This command needs no 7-Zip and never runs or unpacks a package or replaces DLLs.

- **Cent Browser**: portable self-extracting EXE, x86/x64.
- **LibreWolf**: portable ZIP, x64/ARM64.
- **Vivaldi**: installer, x86/x64/ARM64. Open it yourself and choose
  Advanced → Install Standalone ([official instructions](https://help.vivaldi.com/desktop/install-update/standalone-version-of-vivaldi/)).
- **DuckDuckGo**: regular Windows installer, x64. This is an ordinary installation.

Existing verified downloads are reused after a SHA256 check. DuckDuckGo is
downloaded to a temporary file on every run: an equal hash keeps the saved copy,
and a different hash replaces it with the current installer under the original
file name. Errors are listed per browser and do not stop the other downloads.
Zen is omitted because its current official Windows release has no portable
package (checked 2026-10-01).

## Which browsers the DLL builder supports, and why

Those without a self-updating portable build of their own:

- **Google Chrome** — no portable version exists;
- **Yandex Browser** — no portable version, and the Russian CA certificates are
  already built into it;
- **Brave** — the portable builds are third-party and do not update;
- **Chromium-Gost** — speaks the GOST encryption that government portals need;
- **Ungoogled Chromium** — Chromium without Google's services.

The DLL builder excludes **Vivaldi**, **Cent Browser** and **Opera** — each has
its own portable install that updates itself. Vivaldi and Cent Browser are
available from the separate `DOWNLOADS` tab.
**Thorium** was tried and removed: 32-bit builds only, and store extensions do
not install in it.

## What is inside a build

| Folder or file | What it is |
| --- | --- |
| `App` | The browser itself. Replaced wholesale on update. |
| `Data` | Your profile: bookmarks, passwords, tabs, extensions. |
| `Cache` | Cache. Safe to delete. |
| `Certificates` | The certificates and two files — install and remove. |
| `<Browser> Portable.cmd` | Starts the browser. |
| `Portable-Build.json` | Which versions are inside. |

## Updating

The `UPDATE` tab.

`CHECK` shows what has been published next to the version of your build. Nothing
is downloaded.

`UPDATE` replaces only the browser inside the build and leaves `Data` and `Cache`
alone, so the profile stays. A build whose browser and wrapper are both current
is skipped without downloading.

**The update happens where the build lies.** Point Source at its folder — a flash
drive, a network share, wherever it lives — and it is updated in place. Nothing
has to be copied, and the Target folder is not used here: that one is for new
builds.

With no build in Source, the program looks in `output\Portable` — at what it made
itself.

`UPDATE DLL ONLY` refreshes the selected library in existing builds. Select the
browsers, library and architecture (`Auto` reads each browser executable).
Source accepts a build or a parent folder containing several builds. No browser
installer is downloaded and the whole `App` is not replaced. The same library
keeps its INI; switching libraries creates the standard INI. If a file is in use,
changes are rolled back: close the browser and retry.

`Advanced` accepts optional HTTP(S) URLs of full Chrome/Yandex installers. Blank
uses the official source; a custom file is fetched afresh during build/update.
A custom Chrome version is known after unpacking. `CHECK` reports an unknown
version and `UPDATE` does not skip it based on Google's stable release API.

`Disable Yandex updater` is on by default during build and update. It removes
only `service_update.exe` inside Yandex's `App`, including an already-current
build. Other browsers and profile files are untouched. Off keeps the updater in
a newly assembled `App`; restoring a previously removed file requires a rebuild.

## The certificates

Russian state sites are signed by an authority Windows does not trust out of the
box, so such a site opens with a security warning. The steps are separate so that
nothing happens by itself.

**While building** — the checkbox `State site certificates`. It puts the files
and two shortcuts, install and revoke, into the build. It installs nothing.

**Tab `CERTIFICATE` → `ADD TO BUILD`** saves the two certificate files and
install/revoke commands under `Certificates` in selected existing builds.
Point Source at a build or its parent folder. Chrome is selected by default.
Browser/profile files and Windows trust remain unchanged.

**Tab `CERTIFICATE` → `INSTALL`** — adds them to your own Windows account's
store, which every Chromium browser reads. No administrator rights; other users
of the machine see no change.

**`REVOKE`** — removes exactly those two certificates by fingerprint; anything
else stays.

**`CHECK`** shows whether they are installed and changes nothing.

Yandex Browser needs none of this: the certificates are already in it.

## Build settings

**Portability.** The default library is [Proxy library](https://gitflic.ru/project/neyrostalker/proksi-biblioteka) (x86/x64), with every optional switch in `App/version.ini` set to `0`. Alternatives are [Chrome++ (DeftKing)](https://github.com/DeftKing/chrome_plus) and [Vivaldi++](https://github.com/ca-x/vivaldi_plus), both supporting x86/x64/ARM64. A failed release lookup or download falls back to a verified archive of the selected library in the project. The generated launcher starts in `App` so relative `Data` and `Cache` paths stay inside the build. Registry cleanup applies only to Chrome++.

**Wrapper architecture.** Leave it at `AUTO`. The program reads whether the
browser is 32- or 64-bit and uses a matching DLL from the selected library. This is not a
formality: a 32-bit browser with a 64-bit wrapper starts as if nothing were
wrong, but its profile goes to the system and the build stops being portable.

**Clean registry with Chrome++.** Applies only to Chrome++ (DeftKing). Off by default. With it on, the browser wipes its
own registry branch when it exits. That branch is shared with an installed copy
of the same browser, so turn it on only where that browser is not installed.

**Pack into an archive.** Turn it on when the builds are to be handed over: one
file instead of a folder. The format sits next to it — `ZIP` opens anywhere, `7Z`
is smaller but needs 7-Zip on the other side.

**Keep working files** (under `Advanced`). Downloads and unpacked installers stay
in `workspace` — useful when a build failed and the reason has to be found.

## Worth knowing

The first start of any build takes longer — the browser is laying out its profile.

A portable build and an installed browser of the same name run side by side
without disturbing each other.
