using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Text;
using System.Threading;
using Avalonia.Threading;

namespace AudionBrowsersPortable.Core;

/// <summary>One start of the door: the program and its arguments, one by one.</summary>
public sealed class Launch
{
    public string Exe { get; init; } = "";

    /// <summary>
    /// The arguments as a list: the system quotes each one by the rules of the program that reads
    /// them. A path with a space, a quote or a percent sign is the business of nobody here.
    /// </summary>
    public List<string> Args { get; init; } = new();

    /// <summary>Variables of the child alone: never the window's own.</summary>
    public Dictionary<string, string> Env { get; init; } = new();
}

/// <summary>
/// Runs the door as a child process, one run at a time: its lines come to the window in batches.
/// The door, failing, does not take the window with it. Stopping is the door's own business - it
/// is asked by a signal file and stops at a point where nothing is left half done
/// (MainWindow.Run); <see cref="Kill"/> is what is left when it does not answer.
/// The runner of Audion DocFlow. The door is not put into a Windows job that would end it with
/// the window: an operation ended hard leaves its Defender exclusion behind, and one that outlives
/// a fallen window finishes its build and takes the exclusion back itself.
/// </summary>
public sealed class Runner
{
    Process? _current;

    // A process speaks on its reader threads; the window hears it in batches, at most ten times
    // a second. A line per file from tens of thousands of files once froze the window of Disk
    // Tools (24.09.2026).
    readonly object _gate = new();
    readonly List<string> _batch = new();
    int _flushPosted;
    long _lastFlush;

    public bool Running { get; private set; }

    /// <summary>The lines of the door, in batches, on the window's thread.</summary>
    public event Action<IReadOnlyList<string>>? Output;
    public event Action<bool>? RunningChanged;
    /// <summary>The run ended: its exit code (-1 when it did not start).</summary>
    public event Action<int>? Finished;

    /// <summary>Starts a run. False when one is going on already, or the process could not be made.</summary>
    public bool Start(Launch launch)
    {
        if (Running) return false;
        var info = new ProcessStartInfo(launch.Exe)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            // A command that asks (pause, input) with nobody to answer must end, not wait for ever.
            RedirectStandardInput = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
            WorkingDirectory = Paths.Root,
        };
        foreach (var arg in launch.Args) info.ArgumentList.Add(arg);
        foreach (var (name, value) in launch.Env) info.Environment[name] = value;

        Process process;
        try
        {
            process = Process.Start(info) ?? throw new InvalidOperationException("the process was not created");
            // A failure to close the input of the child is not a failure to start it.
            try { process.StandardInput.Close(); } catch { }
        }
        catch (Exception ex)
        {
            Journal.Add(Lang.F("[окно] не запустилось: {0}", ex.Message));
            Finished?.Invoke(-1);
            return false;
        }

        _current = process;
        SetRunning(true);

        // The end of each stream of the child - its reader hands over a null line once it has
        // read everything - and the end of the run as the window counts it: a line that comes
        // after the verdict is a line of nobody's run.
        var outputEnd = new System.Threading.Tasks.TaskCompletionSource(System.Threading.Tasks.TaskCreationOptions.RunContinuationsAsynchronously);
        var errorEnd = new System.Threading.Tasks.TaskCompletionSource(System.Threading.Tasks.TaskCreationOptions.RunContinuationsAsynchronously);
        bool over = false;

        void OnLine(string? line, System.Threading.Tasks.TaskCompletionSource end)
        {
            if (line is null) { end.TrySetResult(); return; }
            lock (_gate)
            {
                if (over) return;
                _batch.Add(line);
            }
            // One flush waits in the window's queue at a time, and not sooner than 100 ms after
            // the last: whatever arrives meanwhile rides along.
            if (Interlocked.Exchange(ref _flushPosted, 1) != 0) return;
            if (DateTime.UtcNow.Ticks - Interlocked.Read(ref _lastFlush) >= TimeSpan.TicksPerMillisecond * 100)
                Dispatcher.UIThread.Post(Flush, DispatcherPriority.Background);
            // Too soon after the last flush: the line waits out the 100 ms, not the next line - or a
            // command that said what it does and then worked for a minute would show its words
            // only after the minute.
            else
                System.Threading.Tasks.Task.Delay(100).ContinueWith(_ => Dispatcher.UIThread.Post(Flush, DispatcherPriority.Background));
        }
        process.OutputDataReceived += (_, e) => OnLine(e.Data, outputEnd);
        process.ErrorDataReceived += (_, e) => OnLine(e.Data, errorEnd);
        // Asked for before the events are switched on: a process that has ended by then raises
        // Exited at that very moment, and a handler added afterwards would never hear of it.
        process.Exited += (_, _) =>
        {
            // Every line is in once both streams have ended. A stream is held open by whatever
            // the door started and left behind: WaitForExit() with no limit waited for that too,
            // and the window stayed "running" for as long as that process lived, with nothing a
            // stop could find to end (10.10.2026). The last lines are given three seconds; then
            // the verdict.
            try { System.Threading.Tasks.Task.WhenAll(outputEnd.Task, errorEnd.Task).Wait(EndOfOutput); } catch { }
            Dispatcher.UIThread.Post(() =>
            {
                lock (_gate) over = true;
                Flush();
                Done(process);
            }, DispatcherPriority.Background);
        };
        process.EnableRaisingEvents = true;
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        return true;
    }

    /// <summary>How long the last lines of a process are waited for after it has ended.</summary>
    static readonly TimeSpan EndOfOutput = TimeSpan.FromSeconds(3);

    void Flush()
    {
        string[] lines;
        lock (_gate)
        {
            lines = _batch.ToArray();
            _batch.Clear();
            Interlocked.Exchange(ref _lastFlush, DateTime.UtcNow.Ticks);
            Interlocked.Exchange(ref _flushPosted, 0);
        }
        if (lines.Length == 0) return;
        Output?.Invoke(lines);
    }

    void Done(Process process)
    {
        int code;
        try { code = process.ExitCode; } catch { code = -1; }
        process.Dispose();
        _current = null;
        SetRunning(false);
        Finished?.Invoke(code);
    }

    /// <summary>The last resort: ends the door with everything it started.</summary>
    public void Kill()
    {
        if (_current is not { } process) return;
        try { process.Kill(entireProcessTree: true); } catch { }
    }

    /// <summary>Waits for the run to end; false when it is still going after the time given.</summary>
    public bool WaitForExit(int milliseconds)
    {
        if (_current is not { } process) return true;
        try { return process.WaitForExit(milliseconds); } catch { return true; }
    }

    void SetRunning(bool running)
    {
        Running = running;
        RunningChanged?.Invoke(running);
    }
}
