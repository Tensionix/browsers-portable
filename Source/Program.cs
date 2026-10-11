using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using Avalonia;

namespace AudionBrowsersPortable;

class Program
{
    /// <summary>
    /// --self-check or --self-check=FILE: the window builds every section of the manifest without
    /// being shown, writes what it built into the file and exits with a code (MainWindow.Check.cs).
    /// Null - an ordinary start.
    /// </summary>
    public static string? SelfCheckFile { get; private set; }

    // Initialization code. Don't use any Avalonia, third-party APIs or any
    // SynchronizationContext-reliant code before AppMain is called: things aren't initialized
    // yet and stuff might break.
    [STAThread]
    public static int Main(string[] args)
    {
        // Before anything that can fail: an end of the program at its very start leaves logs\gui-crash-*.log too.
        Core.CrashGuard.Early();
        // --root "<folder>" - the project of this start, named the way the door takes it. Without
        // it the project is the folder the window stands in.
        for (int i = 0; i < args.Length; i++)
        {
            var arg = args[i];
            if (string.Equals(arg, "--self-check", StringComparison.OrdinalIgnoreCase)) SelfCheckFile = "";
            else if (arg.StartsWith("--self-check=", StringComparison.OrdinalIgnoreCase)) SelfCheckFile = arg[(arg.IndexOf('=') + 1)..].Trim().Trim('"');

            string? named = null;
            if (string.Equals(arg, "-Root", StringComparison.OrdinalIgnoreCase) || string.Equals(arg, "--root", StringComparison.OrdinalIgnoreCase))
                named = i + 1 < args.Length ? args[i + 1] : "";
            else if (arg.StartsWith("--root=", StringComparison.OrdinalIgnoreCase) || arg.StartsWith("-Root:", StringComparison.OrdinalIgnoreCase))
                named = arg[(arg.IndexOfAny(new[] { '=', ':' }) + 1)..];
            if (named is not null) Core.Paths.Use(named.Trim().Trim('"'));
        }
        if (SelfCheckFile is { Length: 0 }) SelfCheckFile = Path.Combine(Core.Paths.Logs, "gui-self-check.json");

#if !DEBUG
        // One window per project folder: a second start brings the first one forward and leaves.
        // A check (a Debug build, the self-check) is not held back by the window the owner works in.
        // The folder is told by where it really is: opened through a letter made by subst or
        // through a link, it is the same project, and two windows of it would run two operations
        // over the same builds.
        Mutex? once = null;
        if (SelfCheckFile is null)
        {
            var folder = Core.FolderCleaner.Real(Core.Paths.Root).ToLowerInvariant();
            once = new Mutex(true, "AudionBrowsersPortable-" + Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(folder)))[..24], out bool first);
            if (!first)
            {
                BringForward();
                return 0;
            }
        }
#endif
        int code = BuildAvaloniaApp().StartWithClassicDesktopLifetime(args);
#if !DEBUG
        GC.KeepAlive(once);
#endif
        return code;
    }

    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr window);

    /// <summary>The window of the copy that is already open: restored when minimised, put in front.</summary>
    static void BringForward()
    {
        try
        {
            var me = Process.GetCurrentProcess();
            var mine = Core.FolderCleaner.Real(me.MainModule?.FileName ?? "");
            foreach (var other in Process.GetProcessesByName(me.ProcessName).Where(process => process.Id != me.Id))
            {
                string path;
                try { path = Core.FolderCleaner.Real(other.MainModule?.FileName ?? ""); } catch { continue; }
                if (!string.Equals(path, mine, StringComparison.OrdinalIgnoreCase) || other.MainWindowHandle == IntPtr.Zero) continue;
                if (IsIconic(other.MainWindowHandle)) ShowWindow(other.MainWindowHandle, 9);
                SetForegroundWindow(other.MainWindowHandle);
                return;
            }
        }
        catch { }
    }

    // Avalonia configuration, don't remove; also used by visual designer.
    public static AppBuilder BuildAvaloniaApp()
        => AppBuilder.Configure<App>()
            .UsePlatformDetect()
            .WithInterFont()
            // JetBrains Mono (SIL OFL, Assets\Fonts\JetBrainsMono) ships inside the program like Inter:
            // the font of the window and of the journal, with Cyrillic, nothing to fetch or install.
            .ConfigureFonts(fonts => fonts.AddFontCollection(new Avalonia.Media.Fonts.EmbeddedFontCollection(
                new Uri("fonts:JetBrainsMono"), new Uri("avares://AudionBrowsersPortable/Assets/Fonts/JetBrainsMono"))))
            // Drop-downs and tooltips are drawn inside the window, not as separate native windows:
            // a picture of the window shows them too.
            .With(new Win32PlatformOptions { OverlayPopups = true })
            .LogToTrace();
}
