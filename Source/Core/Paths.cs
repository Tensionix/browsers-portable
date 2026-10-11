using System;
using System.Diagnostics;
using System.IO;

namespace AudionBrowsersPortable.Core;

/// <summary>
/// Where things live in the project:
///   Start.exe                 the launcher at the root
///   App\                      the window and its read-only data (config\i18n.json)
///   browsers_cli.cmd          the console door of the engine: what every button of the window runs
///   system_core\, runtime\    the engine (Python) - the business of the door, not of the window
///   config\                   tool_manifest.yaml (the engine's), version.json, path_history.json
///                             (the file the window on NiceGUI kept), window.json
///   input\, output\           the working folders the window starts with
///   logs\, report\            the journal of every operation and its reports; the journal of the window
/// </summary>
public static class Paths
{
    /// <summary>The program folder: binaries and the data that ships with them.</summary>
    public static string App { get; } = AppContext.BaseDirectory.TrimEnd('\\', '/');

    /// <summary>The name of the door the window is the face of.</summary>
    public const string DoorName = "browsers_cli.cmd";

    /// <summary>
    /// The project root: the folder that holds the console door, found by walking up from the
    /// program - one level from App\, several from Source\bin\Debug\net10.0. A root named on the
    /// command line (--root) replaces it for this start.
    /// </summary>
    public static string Root { get; private set; } = FindRoot();

    /// <summary>The project of this start, named on the command line.</summary>
    public static void Use(string root)
    {
        try { if (Directory.Exists(root)) Root = Path.GetFullPath(root).TrimEnd('\\', '/'); } catch { }
    }

    /// <summary>Started from App\ under the root: the released layout, not a build run from bin\.</summary>
    public static bool Released =>
        string.Equals(Path.GetDirectoryName(App), Root, StringComparison.OrdinalIgnoreCase) &&
        string.Equals(Path.GetFileName(App), "App", StringComparison.OrdinalIgnoreCase);

    static string FindRoot()
    {
        for (var dir = new DirectoryInfo(App); dir is not null; dir = dir.Parent)
            if (File.Exists(Path.Combine(dir.FullName, DoorName))) return dir.FullName;
        return App;
    }

    /// <summary>The console door of the engine.</summary>
    public static string Door => Path.Combine(Root, DoorName);

    /// <summary>What the door starts: the file its wrapper hands to Python.</summary>
    public static string DoorScript => Path.Combine(Root, "system_core", "cli.py");

    /// <summary>
    /// The Python of the project, looked for where the door looks for it; "" when the environment
    /// has not been built yet.
    /// </summary>
    public static string Python
    {
        get
        {
            foreach (var name in new[] { @"runtime\python.exe", @"runtime\python\python.exe" })
            {
                var file = Path.Combine(Root, name);
                if (File.Exists(file)) return file;
            }
            return "";
        }
    }

    /// <summary>config\ of the project: the version, the history of paths.</summary>
    public static string ProjectConfig => Path.Combine(Root, "config");

    /// <summary>
    /// config\ of the window. A build run from bin\ keeps its own beside itself: a check must not
    /// rewrite the settings of the copy the owner works in.
    /// </summary>
    public static string Config
    {
        get
        {
            var dir = Path.Combine(Released ? Root : App, "config");
            Directory.CreateDirectory(dir);
            return dir;
        }
    }

    public static string Logs
    {
        get
        {
            var dir = Path.Combine(Released ? Root : App, "logs");
            Directory.CreateDirectory(dir);
            return dir;
        }
    }

    static string? _session;

    /// <summary>
    /// The folder of this window's life, in the temporary folder of the user: the values of a
    /// run, its result and the stop signal. One per window, removed when the window closes.
    /// </summary>
    public static string Session
    {
        get
        {
            _session ??= Path.Combine(Path.GetTempPath(), $"browsers_window_{Process.GetCurrentProcess().Id}");
            // Made anew when it is gone: a cleaner of temporary files may take it while the window
            // is open, and a window that could then neither start nor stop anything would have to
            // be restarted to work again.
            Directory.CreateDirectory(_session);
            return _session;
        }
    }

    /// <summary>The session folder goes with the window; what cannot be removed is left to the system.</summary>
    public static void DropSession()
    {
        try { if (_session is not null && Directory.Exists(_session)) Directory.Delete(_session, recursive: true); } catch { }
    }

    public static string Version
    {
        get
        {
            try
            {
                using var doc = System.Text.Json.JsonDocument.Parse(File.ReadAllText(Path.Combine(ProjectConfig, "version.json")));
                return doc.RootElement.TryGetProperty("version", out var v) ? v.GetString() ?? "" : "";
            }
            catch { return ""; }
        }
    }

    /// <summary>A path inside the project shown from its root, any other as it is.</summary>
    public static string Short(string path)
    {
        if (string.IsNullOrEmpty(path)) return "";
        try
        {
            var full = Path.GetFullPath(path);
            if (string.Equals(full, Root, StringComparison.OrdinalIgnoreCase)) return ".";
            if (full.StartsWith(Root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) return full[(Root.Length + 1)..];
            return full;
        }
        catch { return path; }
    }

    /// <summary>
    /// What a person typed or picked as a full path: a variable of the environment (%TEMP%) is
    /// read first, a relative path is counted from the project root, and a backslash at the end
    /// is taken off - "E:\My Builds\" in quotes glues the next word of a command line to the path.
    /// </summary>
    public static string Full(string path)
    {
        var text = (path ?? "").Trim().Trim('"').Trim();
        if (text.Length == 0) return "";
        try
        {
            text = Environment.ExpandEnvironmentVariables(text);
            var full = Path.GetFullPath(Path.IsPathRooted(text) ? text : Path.Combine(Root, text));
            return full.Length > (Path.GetPathRoot(full)?.Length ?? 0) ? full.TrimEnd('\\', '/') : full;
        }
        catch { return text; }
    }
}
