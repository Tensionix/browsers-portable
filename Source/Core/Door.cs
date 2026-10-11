using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;

namespace AudionBrowsersPortable.Core;

/// <summary>What a short call of the door answered.</summary>
public sealed record Reply(int Code, string Out, string Err)
{
    public bool Ok => Code == 0;
}

/// <summary>A service line of a run ("::browsers::{...}"): the start, how far the work is, how it ended.</summary>
public sealed class DoorEvent
{
    /// <summary>started, progress, pulse or finished; "" for a line of a kind this window does not know.</summary>
    public string Kind { get; init; } = "";
    /// <summary>The share of the work done, 0..1; -1 when the line says nothing of it.</summary>
    public double Progress { get; init; } = -1;
    /// <summary>Where a long step is right now: a line shown in place of the pulse before it, not added to the journal.</summary>
    public string Pulse { get; init; } = "";
    /// <summary>done, partial, failed or stopped.</summary>
    public string Outcome { get; init; } = "";
    public string Message { get; init; } = "";
    /// <summary>How many browsers an operation that ended could not do.</summary>
    public int Failed { get; init; }
}

/// <summary>The result of a run, as the door wrote it into its --result file.</summary>
public sealed class RunResult
{
    public string Operation { get; set; } = "";
    public string Outcome { get; set; } = "";
    public bool Ok { get; set; }
    public string Message { get; set; } = "";
    public int Failed { get; set; }
    public string Source { get; set; } = "";
    public string Target { get; set; } = "";
    public double Seconds { get; set; }
}

/// <summary>
/// The door of the engine. A button of the window does nothing a console cannot: it runs
/// browsers_cli.cmd with the words a person would type, and that line is written into the journal.
/// The window starts the Python of the project directly, with the very environment the wrapper
/// sets - so a path is quoted once, by the system, and not a second time by the rules of cmd.
/// Nothing about browsers is decided here: which fields an operation has, what their defaults
/// are, which parameters a service gets - the door alone knows (system_core\cli.py).
/// </summary>
public static class Door
{
    public const string Control = "::browsers::";
    public const int ExitDone = 0, ExitFailed = 1, ExitUsage = 2, ExitStopped = 130;

    /// <summary>The door can be called: the wrapper is there, and so are its script and the Python of the project.</summary>
    public static bool Ready => Trouble.Length == 0;

    /// <summary>Why the door cannot be called, in the words of the window; "" when it can.</summary>
    public static string Trouble =>
        !File.Exists(Paths.Door) ? Lang.F("нет файла: {0}", Paths.Door)
        : !File.Exists(Paths.DoorScript) ? Lang.F("нет файла: {0}", Paths.DoorScript)
        : Paths.Python.Length == 0 ? Lang.F("нет Python проекта ({0}): сначала соберите окружение - builder_main.cmd", Path.Combine(Paths.Root, @"runtime\python.exe"))
        : "";

    // What browsers_cli.cmd does before it calls Python, repeated word for word: the door is
    // started as a file - the embedded Python of the project does not look for modules in the
    // current folder.
    static string[] Script => new[] { "-u", Paths.DoorScript };
    static Dictionary<string, string> Env => new() { ["PYTHONUTF8"] = "1", ["PYTHONIOENCODING"] = "utf-8" };

    static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        PropertyNameCaseInsensitive = true,
    };

    // ------------------------------------------------------------ the files of a run

    public static string ParamsFile => Path.Combine(Paths.Session, "params.json");
    public static string ResultFile => Path.Combine(Paths.Session, "result.json");
    /// <summary>The stop signal: the file appears - the operation stops at its next safe point.</summary>
    public static string StopFile => Path.Combine(Paths.Session, "stop.flag");

    // ------------------------------------------------------------ the lines

    /// <summary>The same call as a console would type it: written into the journal at every start.</summary>
    public static string ConsoleLine(IEnumerable<string> args) =>
        string.Join(" ", new[] { Paths.DoorName }.Concat(args.Select(Quote)));

    /// <summary>
    /// A word of the line as a program reads it back: quoted when it holds a space or a sign a
    /// console treats as its own; a quote inside is escaped, and so is every backslash that
    /// stands before a quote - a path ending in a backslash would otherwise swallow the closing
    /// quote and glue the next word to itself.
    /// </summary>
    static string Quote(string value)
    {
        if (value.Length > 0 && value.IndexOfAny(new[] { ' ', '\t', '"', '&', '(', ')', '^', '=', ';', ',', '%', '!', '<', '>', '|' }) < 0) return value;
        var text = new StringBuilder("\"");
        int slashes = 0;
        foreach (var ch in value)
        {
            if (ch == '\\') { slashes++; continue; }
            if (ch == '"') text.Append('\\', slashes * 2 + 1).Append('"');
            else text.Append('\\', slashes).Append(ch);
            slashes = 0;
        }
        return text.Append('\\', slashes * 2).Append('"').ToString();
    }

    /// <summary>The arguments of a run. The values of the fields go by a file: a text with quotes is not for a command line.</summary>
    public static List<string> RunArgs(string operation, string source, string target)
    {
        var args = new List<string> { "run", "--operation", operation };
        if (source.Length > 0) { args.Add("--source"); args.Add(source); }
        if (target.Length > 0) { args.Add("--target"); args.Add(target); }
        args.AddRange(new[] { "--params", ParamsFile, "--result", ResultFile, "--cancel-file", StopFile });
        return args;
    }

    /// <summary>The same run without the files of the window: what a person would type to repeat it.</summary>
    public static string RunLine(string operation, string source, string target, IReadOnlyDictionary<string, string> changed)
    {
        var args = new List<string> { "run", "--operation", operation };
        if (source.Length > 0) { args.Add("--source"); args.Add(Paths.Short(source)); }
        if (target.Length > 0) { args.Add("--target"); args.Add(Paths.Short(target)); }
        foreach (var (name, value) in changed) { args.Add("--set"); args.Add(name + "=" + value); }
        return ConsoleLine(args);
    }

    /// <summary>
    /// The project the door works in when it is not the program itself: the stand of a check
    /// (Source\Tools\dev-run.ps1 -Stand). A release build has no such thing - its project is the
    /// folder it stands in.
    /// </summary>
    public static string Project { get; } =
#if DEBUG
        Environment.GetEnvironmentVariable("AUDION_DEV_PROJECT") ?? "";
#else
        "";
#endif

    public static Launch Make(IEnumerable<string> args)
    {
        var project = Project.Length > 0 ? new[] { "--root", Project } : Array.Empty<string>();
        return new Launch { Exe = Paths.Python, Args = Script.Concat(project).Concat(args).ToList(), Env = Env };
    }

    /// <summary>The files of the last run are cleared away and the values of the new one written; false when that did not work.</summary>
    public static bool Prepare(string valuesJson)
    {
        try
        {
            foreach (var file in new[] { ResultFile, StopFile })
                if (File.Exists(file)) File.Delete(file);
            File.WriteAllText(ParamsFile, valuesJson, new UTF8Encoding(false));
            return true;
        }
        catch (Exception ex)
        {
            Journal.Add(Lang.F("[окно] не запустилось: {0}", ex.Message));
            return false;
        }
    }

    /// <summary>
    /// Asks the running operation to stop: the operation sees the file itself. "" when the file
    /// is written; the reason when it could not be.
    /// </summary>
    public static string AskToStop()
    {
        try { File.WriteAllText(StopFile, DateTime.Now.ToString("O")); return ""; }
        catch (Exception ex) { return ex.Message.Length > 0 ? ex.Message : ex.GetType().Name; }
    }

    /// <summary>The service line of a run read; null when the line is an ordinary one.</summary>
    public static DoorEvent? Read(string line)
    {
        if (!line.StartsWith(Control, StringComparison.Ordinal)) return null;
        try
        {
            using var doc = JsonDocument.Parse(line[Control.Length..]);
            var root = doc.RootElement;
            static string Text(JsonElement owner, string name) => owner.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? "" : "";
            if (root.TryGetProperty("started", out _)) return new DoorEvent { Kind = "started" };
            if (root.TryGetProperty("finished", out var finished))
                return new DoorEvent
                {
                    Kind = "finished", Outcome = Text(finished, "outcome"), Message = Text(finished, "message"),
                    Failed = finished.TryGetProperty("failed", out var count) && count.ValueKind == JsonValueKind.Number ? count.GetInt32() : 0,
                };
            if (root.TryGetProperty("progress", out var share) && share.ValueKind == JsonValueKind.Number)
                return new DoorEvent { Kind = "progress", Progress = share.GetDouble() };
            if (root.TryGetProperty("pulse", out var pulse) && pulse.ValueKind == JsonValueKind.String)
                return new DoorEvent { Kind = "pulse", Pulse = pulse.GetString() ?? "" };
            // A service line of a kind this window does not know yet: not for the journal either way.
            return new DoorEvent { Kind = "" };
        }
        catch { return new DoorEvent { Kind = "" }; }
    }

    /// <summary>The result file of the run that has just ended; null when the door did not get as far as writing it.</summary>
    public static RunResult? ReadResult()
    {
        try
        {
            if (!File.Exists(ResultFile)) return null;
            return JsonSerializer.Deserialize<RunResult>(File.ReadAllText(ResultFile), Json);
        }
        catch { return null; }
    }

    // ------------------------------------------------------------ short calls

    /// <summary>
    /// A call that answers at once - the manifest: the door is run, its output read whole. Never
    /// throws: a door that could not be started answers with the code -1 and the reason.
    /// </summary>
    public static async Task<Reply> Call(IReadOnlyList<string> args, int timeoutMs = 120_000)
    {
        if (!Ready) return new Reply(-1, "", Trouble);
        var launch = Make(args);
        var info = new ProcessStartInfo(launch.Exe)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            RedirectStandardInput = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
            WorkingDirectory = Paths.Root,
        };
        foreach (var arg in launch.Args) info.ArgumentList.Add(arg);
        foreach (var (name, value) in launch.Env) info.Environment[name] = value;
        try
        {
            using var process = Process.Start(info) ?? throw new InvalidOperationException("the process was not created");
            try { process.StandardInput.Close(); } catch { }
            var output = process.StandardOutput.ReadToEndAsync();
            var errors = process.StandardError.ReadToEndAsync();
            var exited = process.WaitForExitAsync();
            if (await Task.WhenAny(exited, Task.Delay(timeoutMs)) != exited)
            {
                try { process.Kill(entireProcessTree: true); } catch { }
                return new Reply(-1, "", Lang.F("дверь не ответила за {0} с", timeoutMs / 1000));
            }
            return new Reply(process.ExitCode, await output, await errors);
        }
        catch (Exception ex)
        {
            return new Reply(-1, "", ex.Message);
        }
    }

    /// <summary>The manifest of the project, or the reason there is none.</summary>
    public static async Task<(Manifest? Manifest, string Trouble)> Describe()
    {
        var reply = await Call(new[] { "describe" });
        if (!reply.Ok) return (null, Lines(reply.Err.Length > 0 ? reply.Err : reply.Out));
        try { return (Manifest.Parse(reply.Out), ""); }
        catch (Exception ex) { return (null, ex.Message); }
    }

    static string Lines(string text) => string.Join(" ", text.Split('\n').Select(line => line.Trim()).Where(line => line.Length > 0));
}
