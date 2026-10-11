using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;

namespace AudionBrowsersPortable.Core;

/// <summary>What clearing a path of the working folders did.</summary>
public sealed record Cleared(string Path, string Kind, int Removed);

/// <summary>
/// Clearing the working folders: the contents of a folder go, the folder stays. What may never
/// be cleared:
///   - the root of a drive, the program itself and a folder that holds it;
///   - a folder inside the program other than its input and output - runtime, App, system_core
///     and config are the program, and one question must not be able to break it;
///   - what a link points at: a link to a folder is taken off, not walked through.
/// What is deleted goes to the Recycle Bin, by Windows itself (SHFileOperation). Windows is told
/// to warn when it cannot: a build with a profile is gigabytes, the Bin may be too small or
/// switched off, and "it went to the Bin" must not be said of what was destroyed. A flash drive
/// and a network share have no Bin at all (<see cref="HasBin"/>) - the question says so before.
/// </summary>
public static class FolderCleaner
{
    /// <summary>
    /// The drive of the path keeps a Recycle Bin: a fixed local disk. A flash drive and a network
    /// share have none - what is deleted there is gone, and the question before it must say so.
    /// </summary>
    public static bool HasBin(string path)
    {
        try
        {
            var root = Path.GetPathRoot(Path.GetFullPath(path));
            if (string.IsNullOrEmpty(root) || root.StartsWith(@"\\", StringComparison.Ordinal)) return false;
            return new DriveInfo(root).DriveType == DriveType.Fixed;
        }
        catch { return false; }
    }

    static bool Within(string path, string folder) =>
        string.Equals(path, folder, StringComparison.OrdinalIgnoreCase) || path.StartsWith(folder + "\\", StringComparison.OrdinalIgnoreCase);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern Microsoft.Win32.SafeHandles.SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern uint GetFinalPathNameByHandleW(Microsoft.Win32.SafeHandles.SafeFileHandle handle, System.Text.StringBuilder path, uint size, uint flags);

    /// <summary>
    /// The folder a path really reaches, as Windows itself resolves it: a link or a junction
    /// anywhere on the way, a drive letter made by subst or mapped to a share, a short 8.3 name.
    /// The guards compare these, not the spelling: the program opened as X:\ and a Source typed
    /// as E:\...\runtime are the same folder, and by their letters they were strangers. What does
    /// not exist yet is resolved up to the last folder that does. A path Windows will not open
    /// comes back as it was spelled.
    /// </summary>
    public static string Real(string path)
    {
        string full;
        try { full = Path.GetFullPath(path); } catch { return path; }
        var tail = new Stack<string>();
        var probe = full;
        while (true)
        {
            // The root keeps its slash: "E:" alone is the current folder of the drive, not its root.
            bool root = string.Equals(Path.GetPathRoot(probe), probe, StringComparison.OrdinalIgnoreCase);
            if (!root) probe = probe.TrimEnd('\\', '/');
            try
            {
                if (Directory.Exists(probe) || File.Exists(probe))
                {
                    using var handle = CreateFileW(probe, 0, 7, IntPtr.Zero, 3 /* OPEN_EXISTING */, 0x02000000 /* FILE_FLAG_BACKUP_SEMANTICS */, IntPtr.Zero);
                    if (handle.IsInvalid) break;
                    var buffer = new System.Text.StringBuilder(2048);
                    uint length = GetFinalPathNameByHandleW(handle, buffer, (uint)buffer.Capacity, 0);
                    if (length == 0 || length >= buffer.Capacity) break;
                    var real = buffer.ToString();
                    if (real.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase)) real = @"\\" + real[8..];
                    else if (real.StartsWith(@"\\?\", StringComparison.Ordinal)) real = real[4..];
                    foreach (var name in tail) real = Path.Combine(real, name);
                    return Path.GetPathRoot(real) is { } drive && string.Equals(drive, real, StringComparison.OrdinalIgnoreCase) ? real : real.TrimEnd('\\', '/');
                }
            }
            catch { break; }
            if (root) break;
            var parent = Path.GetDirectoryName(probe);
            if (string.IsNullOrEmpty(parent)) break;
            tail.Push(Path.GetFileName(probe));
            probe = parent;
        }
        return string.Equals(Path.GetPathRoot(full), full, StringComparison.OrdinalIgnoreCase) ? full : full.TrimEnd('\\', '/');
    }

    /// <summary>The path lies inside the program, and is not one of the folders it works in.</summary>
    public static bool IsOfProgram(string path, IEnumerable<string> working)
    {
        var real = Real(path);
        if (!Within(real, Real(Paths.Root))) return false;
        return !working.Any(folder => Within(real, Real(folder)));
    }

    /// <summary>
    /// Why a path may not be cleared; "" when it may. <paramref name="working"/> are the folders
    /// of the program that are there to be cleared: its input and its output. Judged by the folder
    /// the path really reaches (<see cref="Real"/>).
    /// </summary>
    public static string Refusal(string path, IEnumerable<string> working)
    {
        string full;
        try { full = Path.GetFullPath(path); } catch { return Lang.F("путь не разобран: {0}", path); }
        // A path that is itself a link is not gone through and not taken off: the person asked for
        // the contents of a folder, and what the link leads to is named so it can be chosen itself.
        try
        {
            var info = new DirectoryInfo(full.TrimEnd('\\', '/'));
            if (info.Exists && IsLink(info))
                return Lang.F("это ссылка на другую папку ({0}): по ссылкам очистка не ходит - укажите саму папку", info.LinkTarget ?? "?");
        }
        catch { }
        var real = Real(full);
        var program = Real(Paths.Root);
        if (Path.GetPathRoot(real) is { } drive && string.Equals(drive.TrimEnd('\\', '/'), real.TrimEnd('\\', '/'), StringComparison.OrdinalIgnoreCase))
            return Lang.F("корень диска не очищается: {0}", full);
        if (string.Equals(real, program, StringComparison.OrdinalIgnoreCase))
            return Lang.F("корень проекта не очищается: {0}", full);
        // The program must not be cleared from above either: a folder that holds it.
        if ((program + "\\").StartsWith(real + "\\", StringComparison.OrdinalIgnoreCase))
            return Lang.F("в этой папке лежит сам проект, она не очищается: {0}", full);
        if (IsOfProgram(full, working))
            return Lang.F("это папка самой программы, она не очищается: {0}", full);
        return "";
    }

    /// <summary>
    /// A link to another place - a symbolic link or a junction. A file kept in the cloud carries
    /// a reparse point too and is no link: it has no target, and it goes to the Bin like any file.
    /// </summary>
    static bool IsLink(FileSystemInfo item)
    {
        try { return item.Attributes.HasFlag(FileAttributes.ReparsePoint) && item.LinkTarget is not null; }
        catch { return false; }
    }

    /// <summary>
    /// Clears a path. Kind: "missing" - nothing there; "folder" - its contents are gone. Throws
    /// when the guards refuse (a path that is itself a link among them), when Windows could not
    /// delete, and <see cref="OperationCanceledException"/> when the person said no to Windows'
    /// own question. <paramref name="window"/> is the window Windows' dialogs belong to.
    /// </summary>
    public static Cleared Clear(string path, IEnumerable<string> working, IntPtr window)
    {
        if (Refusal(path, working) is { Length: > 0 } refusal) throw new InvalidOperationException(refusal);
        var full = Path.GetFullPath(path);
        if (File.Exists(full)) throw new InvalidOperationException(Lang.F("это файл, а не папка: {0}", full));
        if (!Directory.Exists(full)) return new Cleared(full, "missing", 0);
        var folder = new DirectoryInfo(full);
        var children = folder.EnumerateFileSystemInfos().OrderBy(item => item.Name, StringComparer.OrdinalIgnoreCase).ToList();
        var plain = new List<string>();
        foreach (var child in children)
        {
            if (!IsLink(child)) { plain.Add(child.FullName); continue; }
            if (child is DirectoryInfo) Directory.Delete(child.FullName, false);
            else File.Delete(child.FullName);
        }
        if (plain.Count > 0) ToBin(plain, window);
        return new Cleared(full, "folder", children.Count);
    }

    // ------------------------------------------------------------ Windows

    const uint FoDelete = 3;
    const ushort FofNoConfirmation = 0x0010, FofAllowUndo = 0x0040, FofWantNukeWarning = 0x4000;

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct FileOperation
    {
        public IntPtr Window;
        public uint Function;
        public IntPtr From;
        public IntPtr To;
        public ushort Flags;
        [MarshalAs(UnmanagedType.Bool)] public bool Aborted;
        public IntPtr NameMappings;
        public IntPtr ProgressTitle;
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    static extern int SHFileOperationW(ref FileOperation operation);

    /// <summary>
    /// The items to the Recycle Bin in one operation of the shell: one progress window, no
    /// question for what the Bin takes, and Windows' own warning for what it cannot take.
    /// </summary>
    static void ToBin(List<string> items, IntPtr window)
    {
        // The list of the shell: every path ends with a zero, the list with one more.
        var from = Marshal.StringToHGlobalUni(string.Join('\0', items) + "\0\0");
        try
        {
            var operation = new FileOperation
            {
                Window = window, Function = FoDelete, From = from,
                Flags = (ushort)(FofAllowUndo | FofNoConfirmation | FofWantNukeWarning),
            };
            int code = SHFileOperationW(ref operation);
            if (operation.Aborted || code == 0x4C7 /* ERROR_CANCELLED */ || code == 0x75 /* DE_OPCANCELLED */)
                throw new OperationCanceledException();
            if (code != 0) throw new IOException(Lang.F("Windows не удалил содержимое (код {0})", "0x" + code.ToString("X")));
        }
        finally { Marshal.FreeHGlobal(from); }
    }
}
