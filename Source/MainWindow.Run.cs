using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Documents;
using Avalonia.Input;
using Avalonia.Threading;
using AudionBrowsersPortable.Core;
using AudionBrowsersPortable.Views;

namespace AudionBrowsersPortable;

/// <summary>
/// A run: one operation at a time, started through the door with what the form and the working
/// folders hold at the moment of the press. Its course is read from the service lines of the
/// door, its end from the file the door writes. STOP asks the operation to stop - it stops itself
/// where nothing is left half done; only an operation that does not answer is ended.
/// </summary>
public partial class MainWindow
{
    string _runName = "";
    DateTime _started;
    readonly DispatcherTimer _clock = new() { Interval = TimeSpan.FromSeconds(1) };

    /// <summary>What the door said of the end of the run in its service line.</summary>
    DoorEvent? _finish;

    bool _stopAsked, _killed;
    DispatcherTimer? _killTimer;
    /// <summary>
    /// How long an operation asked to stop is waited for before it is ended. Long on purpose: an
    /// operation ended hard leaves a Defender exclusion behind, and a download or an unpacking in
    /// flight takes its time to reach the point where it looks for the signal.
    /// </summary>
    const int StopWaitSeconds = 120;

    void WireRun()
    {
        _runner.Output += OnOutput;
        _runner.RunningChanged += _ => ApplyBusy();
        _runner.Finished += OnFinished;
        StopButton.Click += (_, _) => StopRun();
        _clock.Tick += (_, _) => ShowClock();

        // Ctrl+Enter runs the operation of the one form in sight.
        AddHandler(KeyDownEvent, (_, e) =>
        {
            if (e.Key != Key.Enter || e.KeyModifiers != KeyModifiers.Control) return;
            e.Handled = true;
            // A link or a path being typed is taken first: its box gives up the focus.
            Shell.Focus();
            if (CardOperation() is { } operation) Press(operation);
            else Say("в разделе несколько операций: нажмите кнопку нужной", refused: false, passing: true);
        }, Avalonia.Interactivity.RoutingStrategies.Tunnel);
    }

    /// <summary>Something works with the working folders - an operation, or their clearing: nothing else may start, and the paths stay.</summary>
    bool Busy => _runner.Running || _clearing;

    /// <summary>One thing at a time: while it goes, nothing else starts and the folders it began with stay.</summary>
    void ApplyBusy()
    {
        bool free = !Busy && _manifest is not null && Door.Ready;
        foreach (var button in _runButtons) button.IsEnabled = free;
        foreach (var button in _topRunButtons) button.IsEnabled = free;
        foreach (var control in new Control[] { SourceBox, TargetBox, PickFolderButton, PickTargetButton, ResetPathsButton, ClearFoldersButton })
            control.IsEnabled = !Busy;
        StopButton.IsVisible = _runner.Running;
        StopButton.IsEnabled = _runner.Running && !_stopAsked;
        if (!_runner.Running) Title = AppTitle;
    }

    // ------------------------------------------------------------ the start

    /// <summary>
    /// A press of a button that runs an operation. Everything the run needs is taken from the
    /// window here, before any question: the answer may come a minute later, and what is started
    /// is what was asked about.
    /// </summary>
    async void Press(Node? node)
    {
        if (node is null || !node.Runs) return;
        if (_runner.Running) { Say("идёт другая операция", refused: true); return; }
        if (_clearing) { Say("идёт очистка папок: дождитесь её конца", refused: true); return; }
        if (_manifest is null || Door.Trouble is { Length: > 0 })
        {
            // Why is said at this press too: a file of the program may have gone since the start,
            // and the journal of the start is long gone from the panel.
            if (Door.Trouble is { Length: > 0 } trouble) Journal.Add(Lang.T("[окно] ") + trouble, LogTone.Bad);
            Say("запуск невозможен: причина в журнале", refused: true);
            return;
        }
        // A path typed into Source or Target and refused at this very press - the press took the
        // focus off the box - must not turn into a run on the path that stood there before.
        if (PathJustRefused())
        {
            Say(Lang.F("запуск отменён - введённый путь не принят: {0}", _pathRefused), refused: true);
            return;
        }

        var source = _source;
        var target = _target;
        foreach (var field in node.FormFields.Where(field => field.Kind == "checkboxes" && field.MinSelected > 0))
        {
            if (_values.Picked(field).Count >= field.MinSelected) continue;
            Say(Lang.F("«{0}»: отметьте не меньше {1}", field.Name, field.MinSelected), refused: true);
            return;
        }
        var values = _values.ForDoor(node);
        var changed = _values.ChangedOf(node);

        if (node.Dangerous)
        {
            var lines = new List<string>();
            if (node.Tip.Length > 0) lines.Add(node.Tip);
            else if (node.About.Length > 0) lines.Add(node.About);
            var go = await Dialog.Ask(this, Lang.F("Выполнить «{0}»?", PathName(node)), lines, "ВЫПОЛНИТЬ", Tone.Danger);
            if (go is null) return;
            // A second press while the question stood, a clearing begun, or the window on its way out.
            if (Busy || !IsVisible) return;
        }
        Begin(node, source, target, values, changed);
    }

    /// <summary>The name of an operation with its section: "Сертификат → Установить". Names repeat across sections.</summary>
    static string PathName(Node node) => node.Parent is { } section ? section.Name + " → " + node.Name : node.Name;

    void Begin(Node node, string source, string target, string values, Dictionary<string, string> changed)
    {
        _runName = PathName(node);
        _started = DateTime.Now;
        _finish = null;
        _stopAsked = _killed = false;

        // The panel of the journal begins anew with every run; the files in logs keep everything.
        ClearLogPanel();
        Journal.Add($"===== {_runName} =====");
        // The same run as a console would type it.
        Journal.Add(Lang.T("[окно] ") + Door.RunLine(node.Id, source, target, changed));

        if (!Door.Prepare(values)) { OnFinished(-1); return; }
        // A failed start has told the journal and raised Finished itself.
        if (!_runner.Start(Door.Make(Door.RunArgs(node.Id, source, target)))) return;

        SetState("run");
        Say(_runName);
        Title = AppTitle + " — " + _runName;
        // Until the operation says how far it is, the bar only shows that it goes.
        ProgressBar.IsVisible = true;
        ProgressBar.IsIndeterminate = true;
        CourseText.Text = "";
        ShowClock();
        _clock.Start();
    }

    // ------------------------------------------------------------ the course

    void OnOutput(IReadOnlyList<string> lines)
    {
        var plain = new List<string>(lines.Count);
        // The pulse that stands after these lines: the last one said, unless an ordinary line came after it.
        string? pulse = null;
        foreach (var line in lines)
        {
            if (Door.Read(line) is not { } said) { plain.Add(line); pulse = null; continue; }
            switch (said.Kind)
            {
                case "progress":
                    ShowProgress(said.Progress);
                    break;
                case "pulse":
                    pulse = said.Pulse;
                    break;
                case "finished":
                    _finish = said;
                    break;
            }
        }
        Journal.Show(plain);
        if (pulse is not null) ShowPulse(pulse);
        else if (plain.Count > 0) HidePulse();
    }

    /// <summary>
    /// The pulse of a long step: where a download is right now, megabytes and speed. One line
    /// under the journal, rewritten in place - ten lines of the journal for one file said less
    /// (the owner, 10.10.2026, of those ten lines: "can this be done in one line, with a pulse?").
    /// The dot before it breathes while the step goes. The line is a state and is not kept: the
    /// next ordinary line of the operation takes it away, and so does the end of the run.
    /// </summary>
    void ShowPulse(string text)
    {
        // The panel follows the pulse while its reader is at the end, as it follows new lines.
        bool follow = !PulseRow.IsVisible && LogScroll.Offset.Y + LogScroll.Viewport.Height >= LogScroll.Extent.Height - 40;
        // What follows a bar in the door's line is a measure, and each kind has its colour, so
        // the numbers that change are found at a glance (the owner, of the first pulse: "what is
        // downloaded: forest green. What is to be downloaded, by size: burn orange. The speed:
        // blue" - and then the green brighter). A measure of two parts - "done / of all" - is
        // green, then orange; any other measure is blue. The bar is the door's, plain ASCII; the window parts the measures
        // with a quiet dot at mid-height ("a dot centred vertically is better").
        var quiet = App.Ink("LogMuted");
        var parts = text.Split(" | ");
        var line = PulseText.Inlines ??= new InlineCollection();
        line.Clear();
        line.Add(new Run(parts[0]) { Foreground = App.Ink("LogPlain") });
        for (int i = 1; i < parts.Length; i++)
        {
            line.Add(new Run(" · ") { Foreground = quiet });
            if (parts[i].Split(" / ", 2) is [var done, var all])
            {
                // The green of the progress bar under the window: what is done is one colour in
                // both places. Forest green, the first choice, was too dark on the dark ground
                // (the owner: "make the green brighter in the size downloaded"). Where the
                // green of the bar is too light to be read as text - the light theme - the
                // green of the journal's own good lines, which there is forest green.
                line.Add(new Run(done) { Foreground = ReadableInJournal("ProgressInk", "LogGood") });
                line.Add(new Run(" / ") { Foreground = quiet });
                line.Add(new Run(all) { Foreground = App.Ink("BurnInk") });
            }
            else line.Add(new Run(parts[i]) { Foreground = App.Ink("Quasar") });
        }
        if (PulseRow.IsVisible) return;
        PulseRow.IsVisible = true;
        PulseDot.Classes.Set("live", true);
        if (follow) Dispatcher.UIThread.Post(LogScroll.ScrollToEnd, DispatcherPriority.Background);
    }

    void HidePulse()
    {
        if (!PulseRow.IsVisible) return;
        PulseRow.IsVisible = false;
        // The dot breathes by an animation of its style: off the class, it does not tick unseen.
        PulseDot.Classes.Set("live", false);
        PulseText.Inlines?.Clear();
    }

    void ShowProgress(double share)
    {
        // A share of zero is "started", not a measure: most operations say 0 and then 1.
        bool measured = share > 0;
        ProgressBar.IsIndeterminate = !measured;
        if (measured) ProgressBar.Value = Math.Max(2, Math.Min(100, share * 100));
        CourseText.Text = measured ? $"{Math.Round(share * 100)}%" : "";
    }

    void ShowClock() => ClockText.Text = Clock(DateTime.Now - _started);

    static string Clock(TimeSpan span) => span.TotalHours >= 1 ? span.ToString(@"h\:mm\:ss") : span.ToString(@"mm\:ss");

    // ------------------------------------------------------------ STOP

    /// <summary>
    /// STOP. The operation is asked by the signal file: it sees the file itself at its next safe
    /// point - a build already published stays, the Defender exclusion is taken back. An
    /// operation that has not ended in two minutes is ended from here.
    /// </summary>
    void StopRun(bool closing = false)
    {
        if (!_runner.Running || _stopAsked) return;
        _stopAsked = true;
        StopButton.IsEnabled = false;
        Journal.Add(Lang.T(closing ? "[окно] остановлено закрытием окна: операция останавливается сама"
            : "[окно] остановлено кнопкой СТОП: операция останавливается сама"));
        Say(Lang.F("{0}: останавливается…", _runName));
        if (Door.AskToStop() is { Length: > 0 } trouble)
        {
            // Nothing to ask the operation with: it cannot be told to stop, only ended.
            HardStop(Lang.F("[окно] сигнал остановки не записан ({0}): операция завершена принудительно", trouble));
            return;
        }
        _killTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(StopWaitSeconds) };
        _killTimer.Tick += (_, _) => HardStop(Lang.F("[окно] операция не остановилась за {0} с: завершена принудительно", StopWaitSeconds));
        _killTimer.Start();
    }

    void HardStop(string why)
    {
        _killTimer?.Stop();
        if (!_runner.Running) return;
        _killed = true;
        Journal.Add(why);
        // An operation that has said its end took its exclusion back before saying it: only the
        // process did not leave. The warning is for one cut short in the middle.
        if (_finish is null)
            Journal.Add(Lang.T("[окно] исключение Defender могло остаться: его снимает «Обслуживание → Defender: убрать исключение»"));
        _runner.Kill();
    }

    // ------------------------------------------------------------ the end of a run

    void OnFinished(int code)
    {
        _clock.Stop();
        _killTimer?.Stop();
        ShowClock();
        var took = Clock(DateTime.Now - _started);
        // A run that did not start wrote no outcome: what lies in the file is the last run's.
        var result = code == -1 ? null : Door.ReadResult();
        if (code == -1) _finish = null;

        // What the door said comes first, whatever the window did meanwhile: an operation that
        // finished its work and was ended only because its process would not leave is "done", not
        // "stopped", and one that broke after STOP was pressed is "failed". A door that did not
        // get to say anything is read by what the window did to it, then by its exit code.
        string outcome = _finish is { Outcome.Length: > 0 } said ? said.Outcome
            : result is { Outcome.Length: > 0 } ? result.Outcome
            : _killed || code == Door.ExitStopped ? "stopped"
            : code == -1 ? "nostart"
            : code == Door.ExitUsage ? "refused"
            : code == Door.ExitDone ? "done" : "failed";
        int failed = _finish?.Failed ?? result?.Failed ?? 0;
        string verdict = outcome switch
        {
            "done" => "готово",
            "partial" => Lang.F("готово не полностью - не вышло: {0}", failed),
            "stopped" => "остановлено",
            "refused" => "отказ до начала",
            "nostart" => "не запустилось",
            "failed" => "ошибка",
            // An outcome this window does not know yet is not "done".
            _ => "ошибка",
        };
        // A run that ended in the middle of a download leaves no pulse behind.
        HidePulse();
        Journal.Add($"===== {_runName}: {Lang.T(verdict)} · {took}");
        // Why it failed or where it stopped, in the words of the operation: the journal above
        // holds the whole story. The line is the window's own - it says them after the verdict -
        // so it is written in the tone of the verdict.
        var message = _finish?.Message is { Length: > 0 } told ? told : result?.Message ?? "";
        if (message.Length > 0 && outcome == "failed") Journal.Add(Lang.T("[окно] ") + message, LogTone.Bad);
        else if (message.Length > 0 && outcome == "stopped") Journal.Add(Lang.T("[окно] ") + message, LogTone.Warn);

        ProgressBar.IsVisible = false;
        CourseText.Text = "";
        SetState(outcome switch { "done" => "done", "partial" => "partial", "stopped" => "stopped", _ => "failed" });
        Say(outcome switch
        {
            "done" or "stopped" => _runName,
            "partial" => Lang.F("{0}: не вышло {1} - какие и почему, сказано в журнале", _runName, failed),
            "refused" => Lang.F("{0}: отказ до начала - причина в журнале", _runName),
            "nostart" => Lang.F("{0}: не запустилось - причина в журнале", _runName),
            _ => Lang.F("{0}: ошибка - подробности в журнале", _runName),
        });

        if (_closeAfterRun) Close();
    }

    // ------------------------------------------------------------ the end of the window

    bool _closeAfterRun, _closeAsking, _closeAfterClearing;

    /// <summary>
    /// The window closes. A running operation is asked about - closing stops it, and that has to
    /// be a decision - and on a "yes" it is stopped the way STOP stops it; the window closes when
    /// the operation has ended. A clearing of the folders is waited out: the window closes when
    /// the journal has said what was cleared. Windows shutting down is not asked: there is nobody
    /// to wait for an answer.
    /// </summary>
    void OnClosing(object? sender, WindowClosingEventArgs e)
    {
        if (_clearing && e.CloseReason == WindowCloseReason.WindowClosing)
        {
            e.Cancel = true;
            _closeAfterClearing = true;
            Say("окно закроется, когда закончится очистка", refused: false, passing: true);
            return;
        }
        if (_runner.Running && e.CloseReason == WindowCloseReason.WindowClosing)
        {
            e.Cancel = true;
            if (!_closeAfterRun) AskToClose();
            return;
        }
        if (_runner.Running)
        {
            // The system is going down: the operation is asked and given a few seconds. It is not
            // ended from here - it goes on without the window, or the system ends it.
            Door.AskToStop();
            _runner.WaitForExit(5000);
        }
        SaveWindow();
        // The files of a run that is still going are its own: the folder is left to it.
        if (!_runner.Running) Paths.DropSession();
    }

    async void AskToClose()
    {
        if (_closeAsking) return;
        _closeAsking = true;
        try
        {
            var go = await Dialog.Ask(this, "Закрыть окно и остановить операцию?",
                new[]
                {
                    Lang.F("Идёт «{0}».", _runName),
                    "Операция остановится сама в ближайшей безопасной точке: готовые сборки останутся, исключение Defender будет снято.",
                    "Окно закроется, когда операция остановится.",
                }, "ОСТАНОВИТЬ", Tone.Danger);
            if (go is null) return;
            if (!_runner.Running) { Close(); return; }
            _closeAfterRun = true;
            StopRun(closing: true);
        }
        finally { _closeAsking = false; }
    }
}
