using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;

namespace AudionBrowsersPortable.Core;

public enum LogTone { Plain, Muted, Head, Good, Warn, Bad }

/// <summary>
/// A line for the panel. Told - a line an operation said: it is shown in the colours its writer
/// gave it and in no other (Ansi.cs), and its tone is not looked at. Otherwise a line of the
/// window, in the tone of what it says.
/// </summary>
public readonly record struct LogLine(string Text, LogTone Tone, bool Told);

/// <summary>
/// The journal of the window. Two kinds of lines come to its panel. What the window says itself -
/// a run started, how it ended, what could not be done - also goes to the file of the day
/// (logs\gui-DATE.log) with its time. What an operation says is shown as the door printed it and
/// is not written again: every operation keeps its own journal in logs\.
///
/// Only the lines of the window are coloured by what they say. A line of an operation is never
/// read for words - "[OK]" green, "[WARN]" amber: the window before this one showed the colours
/// the engine sent and added none, and the owner's rule for every window of a console is the
/// same ("we carry over what the services put into the line and make up nothing", 10.10.2026).
/// </summary>
public static class Journal
{
    /// <summary>Lines for the panel, in the order they came; raised on the thread that wrote them.</summary>
    public static event Action<IReadOnlyList<LogLine>>? Lines;

    static string LogFile => Path.Combine(Paths.Logs, $"gui-{DateTime.Now:yyyyMMdd}.log");

    /// <summary>A line of the window: to the panel and to the file of the day.</summary>
    public static void Add(string line) => AddMany(new[] { line });

    /// <summary>A line of the window in a tone named by the one who writes it: the words of an operation, said by the window.</summary>
    public static void Add(string line, LogTone tone) => AddMany(new[] { line }, tone);

    public static void AddMany(IReadOnlyList<string> lines, LogTone? tone = null)
    {
        if (lines.Count == 0) return;
        var shown = new List<LogLine>(lines.Count);
        var file = new StringBuilder();
        var stamp = DateTime.Now.ToString("HH:mm:ss");
        foreach (var raw in lines)
        {
            var line = Lang.T(raw);
            shown.Add(new LogLine(line, tone ?? Tone(line), false));
            file.Append(stamp).Append("  ").AppendLine(line);
        }
        try { File.AppendAllText(LogFile, file.ToString(), Encoding.UTF8); } catch { }
        Lines?.Invoke(shown);
    }

    /// <summary>Lines of an operation: to the panel alone, as the door printed them.</summary>
    public static void Show(IReadOnlyList<string> lines)
    {
        if (lines.Count == 0) return;
        Lines?.Invoke(lines.Select(line => new LogLine(line, LogTone.Plain, true)).ToList());
    }

    /// <summary>
    /// What the window was asked to show and does not keep - the names in the source: to the
    /// panel alone. The first line is the window's own word of what follows; the names are
    /// written in the colour of the journal's text - a name is not read for words either.
    /// </summary>
    public static void List(IReadOnlyList<string> lines)
    {
        if (lines.Count == 0) return;
        Lines?.Invoke(lines.Select((line, index) => new LogLine(line, index == 0 ? LogTone.Muted : LogTone.Plain, false)).ToList());
    }

    // The verdict of a run, as the window writes it: "===== Собрать: готово · 01:19".
    static readonly Regex HeadGood = new(": (готово|done)( · |$)", RegexOptions.Compiled);
    static readonly Regex HeadWarn = new(": (остановлено|stopped|готово не полностью|done in part)( · |:| - |$)", RegexOptions.Compiled);
    static readonly Regex HeadBad = new(": (ошибка|failed|отказ до начала|rejected before the start|не запустилось|did not start)( · |$)", RegexOptions.Compiled);

    // What the window says of itself. Red is only for what really broke, or the journal
    // frightens for no reason.
    static readonly Regex Bad = new(@"не запустилось|did not start|нет файла|no such file", RegexOptions.Compiled);
    static readonly Regex Warn = new(@"остановлено|stopped by", RegexOptions.Compiled);
    // Where the program is and what a check did: read when something went wrong, quiet otherwise.
    static readonly Regex Muted = new(@"^\[dev\]|^(программа|дверь|program|door)\s*:", RegexOptions.Compiled);

    /// <summary>What colour a line of the window is written in.</summary>
    public static LogTone Tone(string line)
    {
        var t = line.TrimEnd();
        if (t.Trim().Length == 0) return LogTone.Plain;
        if (t.StartsWith("====="))
        {
            if (HeadWarn.IsMatch(t)) return LogTone.Warn;
            if (HeadGood.IsMatch(t)) return LogTone.Good;
            if (HeadBad.IsMatch(t)) return LogTone.Bad;
            return LogTone.Head;
        }
        if (Bad.IsMatch(t)) return LogTone.Bad;
        if (Warn.IsMatch(t)) return LogTone.Warn;
        if (Muted.IsMatch(t)) return LogTone.Muted;
        if (t.StartsWith("[окно]") || t.StartsWith("[window]")) return LogTone.Muted;
        return LogTone.Plain;
    }

    public static string ToneKey(LogTone tone) => tone switch
    {
        LogTone.Muted => "LogMuted", LogTone.Head => "LogHead", LogTone.Good => "LogGood",
        LogTone.Warn => "LogWarn", LogTone.Bad => "LogBad", _ => "LogPlain",
    };
}
