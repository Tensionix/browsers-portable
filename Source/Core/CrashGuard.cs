using System;
using System.IO;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Text;
using System.Threading.Tasks;
using Avalonia.Threading;

namespace AudionBrowsersPortable.Core;

/// <summary>
/// The guard of the window. An error nobody caught in the handler of a button would close the
/// program without a word: the journal would end in the middle of a line and nothing would say
/// why. Here such an error is written whole into logs\gui-crash-TIME.log, told by one line of
/// the journal, and the window stays.
///
/// A storm is not held back for ever: an error that comes back at every redraw would freeze the
/// window behind a wall of files. After <see cref="Limit"/> errors within a minute the program is
/// let go - the last file tells why.
///
/// An error of a background task nobody waited for closes nothing, so it is no crash: it goes,
/// a few lines each, into one file of the day, logs\gui-background-DATE.log, and the journal
/// says nothing about it.
/// </summary>
public static class CrashGuard
{
    const int Limit = 20;
    static readonly object Gate = new();
    static int _count;
    static DateTime _since = DateTime.MinValue;

    /// <summary>What is already on the disk: the dispatcher hands the same error over once more as the failed task of its operation.</summary>
    static readonly ConditionalWeakTable<Exception, object> Written = new();

    static bool _early, _late;

    /// <summary>
    /// The first lines of Main: what concerns the whole program. A start that breaks before the
    /// window is there - a setting that cannot be read, a field filled in the wrong order - is
    /// written down like any other end of the program.
    /// </summary>
    public static void Early()
    {
        if (_early) return;
        _early = true;
        TaskScheduler.UnobservedTaskException += (_, e) =>
        {
            try
            {
                e.SetObserved();
                if (e.Exception.Flatten().InnerExceptions.Any(inner => Written.TryGetValue(inner, out _))) return;
                Note(e.Exception);
            }
            catch { }
        };
        // Any thread but the window's own: the program cannot be kept, but why it went is on the disk.
        AppDomain.CurrentDomain.UnhandledException += (_, e) =>
        {
            try { Write("an error nobody caught - the program closes", e.ExceptionObject as Exception); }
            catch { }
        };
    }

    /// <summary>When Avalonia is up, before the window is made: the thread of the window.</summary>
    public static void Install()
    {
        Early();
        if (_late) return;
        _late = true;
        Dispatcher.UIThread.UnhandledException += (_, e) =>
        {
            // The guard itself must never throw: nobody catches a guard.
            try
            {
                bool storm = Storm();
                var file = Write(storm ? "errors keep coming in the thread of the window - the program closes" : "an error in the thread of the window", e.Exception);
                if (storm) return;
                e.Handled = true;
                Say(e.Exception, file);
            }
            catch { }
        };
    }

    static bool Storm()
    {
        lock (Gate)
        {
            var now = DateTime.UtcNow;
            if ((now - _since).TotalSeconds > 60)
            {
                _since = now;
                _count = 0;
            }
            return ++_count > Limit;
        }
    }

    /// <summary>The whole error into a file of its own; the name of the file, or "" when even that did not work.</summary>
    static string Write(string what, Exception? error)
    {
        try
        {
            if (error is not null) Written.AddOrUpdate(error, Gate);
            var now = DateTime.Now;
            var file = Path.Combine(Paths.Logs, $"gui-crash-{now:yyyyMMdd-HHmmss-fff}.log");
            var text = new StringBuilder()
                .AppendLine($"{now:yyyy-MM-dd HH:mm:ss}  Audion Browsers Portable {Paths.Version}")
                .AppendLine(what)
                .AppendLine()
                .AppendLine(error?.ToString() ?? "(no exception object)");
            File.WriteAllText(file, text.ToString(), Encoding.UTF8);
            return file;
        }
        catch { return ""; }
    }

    /// <summary>An error of a background task: appended to the file of the day, each inner error with its place.</summary>
    static void Note(AggregateException error)
    {
        var now = DateTime.Now;
        var text = new StringBuilder().AppendLine($"{now:HH:mm:ss}  Audion Browsers Portable {Paths.Version}: a background task failed and nobody waited for it");
        foreach (var inner in error.Flatten().InnerExceptions) text.AppendLine(inner.ToString());
        text.AppendLine();
        lock (Gate) File.AppendAllText(Path.Combine(Paths.Logs, $"gui-background-{now:yyyyMMdd}.log"), text.ToString(), Encoding.UTF8);
    }

    static void Say(Exception error, string file)
    {
        // The journal may be the very thing that failed: a line that cannot be written is not worth a second error.
        try
        {
            var where = file.Length > 0 ? Path.Combine("logs", Path.GetFileName(file)) : "logs";
            Journal.Add(Lang.F("[окно] действие не вышло: {0} - подробности в {1}", error.Message, where));
        }
        catch { }
    }
}
