using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Documents;
using Avalonia.Media;
using Avalonia.Threading;
using AudionBrowsersPortable.Core;
using AudionBrowsersPortable.Views;

namespace AudionBrowsersPortable;

/// <summary>
/// The window of Audion Browsers Portable: the sections of the manifest across the top, the
/// operations of a section with their forms on the left, the working folders and the journal on
/// the right. It builds and updates nothing itself - every button runs an operation of the
/// manifest through the console door (browsers_cli.cmd), and the window shows what the operation
/// says.
///
/// The parts: this file - the shell (the top, the journal, the status line, the end);
/// MainWindow.Nav.cs - the sections and the cards; MainWindow.Io.cs - the working folders;
/// MainWindow.Run.cs - a run, its course, its end and STOP.
/// </summary>
public partial class MainWindow : Window
{
    const string AppTitle = "Audion Browsers Portable";

    readonly Settings _settings;
    readonly Runner _runner = new();
    readonly Values _values = new();
    readonly Form _form;

    /// <summary>The manifest as the door told it at the start; null until it has, or when it could not.</summary>
    Manifest? _manifest;

    /// <summary>The width of the right column as the markup gives it: the one a person has not changed.</summary>
    readonly double _sideOfMarkup;

    public MainWindow() : this(Settings.Load()) { }

    public MainWindow(Settings settings)
    {
        _settings = settings;
        _form = new Form(_values);
        InitializeComponent();
#if DEBUG
        DevSetup();
#endif

        if (settings.Width >= MinWidth && settings.Height >= MinHeight) { Width = settings.Width; Height = settings.Height; }
        _sideOfMarkup = Zones.ColumnDefinitions[2].Width.Value;
        if (settings.Side >= Side.MinWidth) Zones.ColumnDefinitions[2].Width = new GridLength(settings.Side);
        Zones.SizeChanged += (_, _) => FitSide();
        Classes.Set("vault", settings.Look == "vault");
        TintRunButtons();
        VersionText.Text = "v" + Paths.Version;
        Title = AppTitle;

        WireTop();
        WireIo();
        WireJournal();
        WireRun();

        Journal.Lines += OnJournal;
        SafeFile.Problem += Say;
        Lang.Follow(StatusText);
        _values.Changed += _ => ShowChanged();

        SetState("idle");

        Opened += (_, _) => Start();
        // A window left without a manifest asks again each time it comes back to the front: the
        // person went to build the environment (builder_main.cmd) and returned to this window.
        Activated += (_, _) => { if (_startDone && _manifest is null) _ = ReadManifest(); };
        Closing += OnClosing;
    }

    /// <summary>
    /// The right column never takes the room of the left zone. Its width is kept in pixels, and
    /// one chosen in a maximised window is wider than the window it comes back to: the forms
    /// were left with nothing, and the splitter to bring them back was off the screen. Held at
    /// the start and at every change of the size.
    /// </summary>
    void FitSide()
    {
        var column = Zones.ColumnDefinitions[2];
        if (!column.Width.IsAbsolute || Zones.Bounds.Width <= 0) return;
        double room = Zones.Bounds.Width - Zones.ColumnDefinitions[1].Width.Value - Zones.ColumnDefinitions[0].MinWidth;
        room = Math.Max(room, column.MinWidth);
        if (column.Width.Value > room) column.Width = new GridLength(room);
    }

    bool _startDone, _reading, _doorSaid;
    /// <summary>Why there is no manifest, as it was last said: the same answer is not written twice.</summary>
    string _noManifest = "";

    /// <summary>
    /// The first lines of the journal: where the program is and what it runs. Then the manifest,
    /// read by the door - the window draws nothing of its own.
    /// </summary>
    async void Start()
    {
        Lang.Walk(this);
        Journal.Add(Lang.F("программа: {0}", Paths.Root));
        foreach (var problem in SafeFile.Pending) Say(problem);
        SafeFile.Pending.Clear();
        await ReadManifest();
        _startDone = true;
#if DEBUG
        RunDevScript();
#endif
    }

    /// <summary>
    /// The manifest, asked of the door. At the start, and again whenever the window comes to the
    /// front without one: a window opened before the environment was built, or while a file of
    /// the program was being replaced, must not stay dead until it is closed and opened again.
    /// </summary>
    async System.Threading.Tasks.Task ReadManifest()
    {
        if (_reading || _manifest is not null) return;
        _reading = true;
        try
        {
            if (Door.Trouble is { Length: > 0 } trouble) { NoManifest(trouble); return; }
            // Said the first time the door can be asked at all, not at every asking.
            if (!_doorSaid)
            {
                _doorSaid = true;
                Journal.Add(Lang.F("дверь: {0}", Paths.Door));
                Say("читаю манифест…");
            }
            var (manifest, why) = await Door.Describe();
            if (manifest is null) { NoManifest(Lang.F("манифест не прочитан: {0}", why)); return; }
            _manifest = manifest;
            _noManifest = "";
            EnsureFolders();
            ResetPaths(quiet: true);
            BuildTabs();
            // No box of the form holds the focus at the start: a key pressed by chance types nothing.
            Shell.Focus();
            Say(Lang.F("готово к работе · операций {0}", CountOperations()));
        }
        finally { _reading = false; }
    }

    /// <summary>The window without a manifest: nothing to run, and the left zone and the journal say why.</summary>
    void NoManifest(string why)
    {
        Cards.Children.Clear();
        CardsNote.Text = why;
        CardsNote.IsVisible = true;
        if (why == _noManifest) return;
        _noManifest = why;
        Journal.Add(Lang.T("[окно] ") + why, LogTone.Bad);
        Say(Lang.T("запуск невозможен: причина в журнале"), refused: true);
    }

    // ------------------------------------------------------------ the top: language and theme

    void WireTop()
    {
        // Two switches, one of them on: the language of the window is seen, not guessed from a button that names the other one.
        LangRu.Click += (_, _) => SetLanguage("ru");
        LangEn.Click += (_, _) => SetLanguage("en");
        MarkLanguage();

        ThemeVault.Click += (_, _) => SetTheme("vault");
        ThemeDark.Click += (_, _) => SetTheme("dark");
        ThemeCode.Click += (_, _) => SetTheme("codedark");
        ThemeLight.Click += (_, _) => SetTheme("light");
        MarkTheme();
    }

    void MarkLanguage()
    {
        LangRu.IsChecked = Lang.Code == "ru";
        LangEn.IsChecked = Lang.Code != "ru";
    }

    void SetLanguage(string next)
    {
        // A press on the language that is on must not switch it off.
        if (Lang.Code == next) { MarkLanguage(); return; }
        Lang.Set(next);
        _settings.Lang = next;
        _settings.Save();
        MarkLanguage();
        // What the manifest gives in two languages is built again; what the markup holds is walked.
        if (_manifest is not null) BuildTabs();
        ShowState();
        Lang.Walk(this);
    }

    void SwitchLanguage() => SetLanguage(Lang.Code == "ru" ? "en" : "ru");

    void SetTheme(string look)
    {
        _settings.Look = look;
        _settings.Save();
        if (Application.Current is { } app) app.RequestedThemeVariant = App.Variant(look);
        Classes.Set("vault", look == "vault");
        MarkTheme();
        TintRunButtons();
        // The lines of the journal took their brushes from the old theme.
        Dispatcher.UIThread.Post(Recolour, DispatcherPriority.Background);
        // Once more when the new theme has settled: the fill is made of its brushes.
        Dispatcher.UIThread.Post(TintRunButtons, DispatcherPriority.Background);
    }

    /// <summary>
    /// The fill of the buttons that run the operation of a form: the accent of the theme, and
    /// for a dangerous one Burn Orange - each darkened until white type reads on it. The dark
    /// themes keep a bright accent for rims and thin marks; as the ground of a whole button it
    /// glared, and the owner had it darkened (10.10.2026). The accent of the light theme is deep
    /// enough already and stays as it is. No colour is written here: the fill is made of the
    /// theme's own, so it changes with the theme.
    /// </summary>
    void TintRunButtons()
    {
        // Resources of the program, not of this window: a question of the window is a window of
        // its own, and the fills of the states are made of these two (ButtonTints.cs).
        if (Application.Current is not { } app) return;
        app.Resources["ActFill"] = ForWhiteType(App.Ink("Quasar"));
        app.Resources["ActBurnFill"] = ForWhiteType(App.Ink("BurnInk"));
        // The rims of buttons and chips and the fills of their states are made of the theme too.
        ButtonTints.Tint();
    }

    /// <summary>How light a colour is, 0..1, as the contrast of two colours is counted by.</summary>
    static double Lightness(Color c)
    {
        static double Channel(byte value)
        {
            var share = value / 255.0;
            return share <= 0.03928 ? share / 12.92 : Math.Pow((share + 0.055) / 1.055, 2.4);
        }
        return 0.2126 * Channel(c.R) + 0.7152 * Channel(c.G) + 0.0722 * Channel(c.B);
    }

    /// <summary>The colour of a brush, darkened in small steps until white on it has the contrast of body text (4.5 to 1).</summary>
    static IBrush ForWhiteType(IBrush brush)
    {
        if (brush is not ISolidColorBrush { Color: var colour }) return brush;
        for (int step = 0; step < 60 && 1.05 / (Lightness(colour) + 0.05) < 4.5; step++)
            colour = Color.FromRgb((byte)(colour.R * 0.97), (byte)(colour.G * 0.97), (byte)(colour.B * 0.97));
        return new SolidColorBrush(colour);
    }

    /// <summary>
    /// The first of the brushes named that reads as text on the ground of the journal (3.5 to 1);
    /// the last of them when none does. A colour the shell keeps for a rim is deep enough for
    /// text in one theme and too faint in another.
    /// </summary>
    static IBrush ReadableInJournal(params string[] keys)
    {
        double ground = App.Ink("ListBg") is ISolidColorBrush { Color: var back } ? Lightness(back) : 0;
        foreach (var key in keys)
        {
            if (App.Ink(key) is not ISolidColorBrush { Color: var ink }) continue;
            double light = Lightness(ink);
            if ((Math.Max(light, ground) + 0.05) / (Math.Min(light, ground) + 0.05) >= 3.5) return App.Ink(key);
        }
        return App.Ink(keys[^1]);
    }

    void MarkTheme()
    {
        ThemeVault.IsChecked = _settings.Look == "vault";
        ThemeDark.IsChecked = _settings.Look == "dark";
        ThemeCode.IsChecked = _settings.Look == "codedark";
        ThemeLight.IsChecked = _settings.Look == "light";
        ThemeButton.IsChecked = false;
    }

    // ------------------------------------------------------------ the status line

    /// <summary>idle, run, done, partial, failed or stopped.</summary>
    string _state = "idle";

    static readonly string[] States = { "idle", "run", "done", "partial", "failed", "stopped" };

    void SetState(string state)
    {
        _state = state;
        ShowState();
    }

    void ShowState()
    {
        foreach (var known in States)
        {
            StateDot.Classes.Set(known, known == _state && known != "idle");
            StateText.Classes.Set(known, known == _state && known != "idle");
        }
        StateText.Text = Lang.T(StateName(_state));
    }

    static string StateName(string state) => state switch
    {
        "run" => "ВЫПОЛНЯЕТСЯ",
        "done" => "ГОТОВО",
        "partial" => "ГОТОВО НЕ ПОЛНОСТЬЮ",
        "failed" => "ОШИБКА",
        "stopped" => "ОСТАНОВЛЕНО",
        _ => "ОЖИДАНИЕ",
    };

    DispatcherTimer? _sayTimer;
    string _steady = "";

    /// <summary>
    /// A word of the window in the status line. A short one - a refusal, "the choice was
    /// cancelled" - stays a few seconds and gives the line back to what stood there; a refusal is
    /// amber and goes to the journal too.
    /// </summary>
    void Say(string text) => Say(text, refused: false);

    void Say(string text, bool refused, bool passing = false)
    {
        _sayTimer?.Stop();
        StatusText.Classes.Set("refused", refused);
        StatusText.Text = text;
        if (refused) Journal.Add(Lang.T("[окно] ") + Lang.T(text));
        if (!refused && !passing) { _steady = text; return; }
        _sayTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(refused ? 6 : 3) };
        _sayTimer.Tick += (_, _) =>
        {
            _sayTimer?.Stop();
            StatusText.Classes.Set("refused", false);
            StatusText.Text = _steady;
        };
        _sayTimer.Start();
    }

    /// <summary>
    /// A folder, opened in Explorer. Whether it is there is asked off the thread of the window:
    /// a flash drive that has gone or a share that is down answers after a timeout.
    /// </summary>
    async void Open(string path)
    {
        bool there = await System.Threading.Tasks.Task.Run(() => { try { return Directory.Exists(path) || File.Exists(path); } catch { return false; } });
        if (!there) { Say(Lang.F("нет на диске: {0}", Paths.Short(path)), refused: true); return; }
        try { Process.Start(new ProcessStartInfo(path) { UseShellExecute = true }); }
        catch (Exception ex) { Journal.Add(Lang.F("[окно] не запустилось: {0}", ex.Message)); }
    }

    /// <summary>Source or Target shown in Explorer.</summary>
    void Reveal(string path) => Open(path);

    // ------------------------------------------------------------ the journal

    void WireJournal()
    {
        LogsButton.Click += (_, _) => Open(_manifest?.Paths.Logs is { Length: > 0 } logs ? logs : Path.Combine(Paths.Root, "logs"));
        ReportsButton.Click += (_, _) => Open(_manifest?.Paths.Report is { Length: > 0 } report ? report : Path.Combine(Paths.Root, "report"));
        ConfigButton.Click += (_, _) => Open(Paths.ProjectConfig);

        ClearLogButton.Click += (_, _) => { ClearLogPanel(); Say("панель журнала очищена", refused: false, passing: true); };
        CopyLogButton.Click += async (_, _) =>
        {
            if (Clipboard is { } clipboard) await Avalonia.Input.Platform.ClipboardExtensions.SetTextAsync(clipboard, _logAll.ToString());
            Say("журнал в буфере обмена", refused: false, passing: true);
        };
        WideLogButton.Click += (_, _) => SetWideLog(!_wideLog);
    }

    bool _wideLog;

    /// <summary>The journal over the whole right column - the working folders give it their place - and back.</summary>
    void SetWideLog(bool wide)
    {
        _wideLog = wide;
        IoFrame.IsVisible = !wide;
        if (WideLogButton.Content is Avalonia.Controls.Shapes.Path sign)
        {
            sign.Tag = wide ? "minimize-2" : "maximize-2";
            sign.Data = Icons.Glyph(wide ? "minimize-2" : "maximize-2");
        }
    }

    /// <summary>Everything the panel was told since it was last cleared: what the copy button gives.</summary>
    readonly StringBuilder _logAll = new();
    const int LogAllLimit = 16_000_000;

    // The panel keeps the last lines and not all of them; the copy button and the logs keep the
    // rest. It is pages of text one under another (LogPages): a text is laid out whole at every
    // line added to it, so new lines go into the last page while it has room, a full page is never
    // touched again, and the oldest pages leave whole. A selection with the mouse stays inside a
    // page; the copy button gives everything. The journal of Audion Build Orchestrator.
    const int LogLimit = 5000;
    const int PageLines = 100;
    /// <summary>The lines each page holds, in the order of the pages.</summary>
    readonly List<int> _pageLines = new();
    /// <summary>The runs each page holds: with the oldest pages their runs leave the list below.</summary>
    readonly List<int> _pageRuns = new();
    /// <summary>How every run of the panel is written: a Run has nowhere to keep it, and a change of the theme needs it.</summary>
    readonly List<(Run Run, Hue Hue)> _logRuns = new();
    int _logShown;
    bool _scrollPosted;
    /// <summary>The colours of the operation that runs: what a line opened is in force on the next.</summary>
    readonly Ansi _ansi = new();

    void ClearLogPanel()
    {
        LogPages.Children.Clear();
        _pageLines.Clear();
        _pageRuns.Clear();
        _logRuns.Clear();
        _logAll.Clear();
        _logShown = 0;
        if (!_runner.Running) _ansi.Reset();
    }

    /// <summary>
    /// Lines into the panel. A line an operation said is written in the colours its writer gave
    /// it and in no other (Core\Ansi.cs); a line of the window itself - what was run, how it
    /// ended, what the window could not do - in the tone of what it says (Journal.Tone).
    /// </summary>
    void OnJournal(IReadOnlyList<LogLine> lines)
    {
        if (!Dispatcher.UIThread.CheckAccess()) { Dispatcher.UIThread.Post(() => OnJournal(lines)); return; }
        if (lines.Count == 0) return;
        // The panel follows the new lines while its reader is at the end, and stays where it is
        // when he has gone up to read: asked before the lines are added.
        bool follow = _scrollPosted || LogScroll.Offset.Y + LogScroll.Viewport.Height >= LogScroll.Extent.Height - 40;

        var read = new List<(string Text, Hue Hue)>[lines.Count];
        for (int i = 0; i < lines.Count; i++)
        {
            string plain = lines[i].Text;
            if (lines[i].Told) read[i] = _ansi.Read(lines[i].Text, out plain);
            else
            {
                read[i] = new List<(string, Hue)>();
                if (plain.Length > 0) read[i].Add((plain, new Hue(Journal.ToneKey(lines[i].Tone), null, false, false)));
            }
            _logAll.Append(plain).Append("\r\n");
        }
        if (_logAll.Length > LogAllLimit)
        {
            int cut = LogAllLimit / 4;
            while (cut < _logAll.Length && _logAll[cut - 1] != '\n') cut++;
            _logAll.Remove(0, cut);
        }

        // Of a batch larger than the panel only its end is shown; the copy has all of it.
        int at = Math.Max(0, lines.Count - LogLimit);
        _logShown += lines.Count - at;
        var pages = LogPages.Children;
        while (at < lines.Count)
        {
            if (_pageLines.Count == 0 || _pageLines[^1] >= PageLines)
            {
                pages.Add(new Controls.LogPage { TextWrapping = TextWrapping.Wrap });
                _pageLines.Add(0);
                _pageRuns.Add(0);
            }
            int held = _pageLines[^1], take = Math.Min(PageLines - held, lines.Count - at);
            // The lines of a page are parted by line ends, with none after the last: the next page
            // begins right under it. Pieces that stand together and are written the same way are
            // one run.
            var added = new List<Inline>();
            var piece = new StringBuilder();
            var kind = new Hue("LogPlain", null, false, false);
            void Close()
            {
                if (piece.Length == 0) return;
                var run = new Run(piece.ToString()) { Foreground = Ansi.Brush(kind) };
                if (kind.Bold) run.FontWeight = FontWeight.Bold;
                if (kind.Underline) run.TextDecorations = TextDecorations.Underline;
                added.Add(run);
                _logRuns.Add((run, kind));
                piece.Clear();
            }
            for (int i = 0; i < take; i++)
            {
                if (held + i > 0) piece.Append('\n');
                foreach (var (text, hue) in read[at + i])
                {
                    if (hue != kind) Close();
                    kind = hue;
                    piece.Append(text);
                }
            }
            Close();
            var page = (Controls.LogPage)pages[^1];
            (page.Inlines ??= new InlineCollection()).AddRange(added);
            _pageLines[^1] = held + take;
            _pageRuns[^1] += added.Count;
            at += take;
        }

        int gone = 0, lost = 0;
        while (gone < _pageLines.Count - 1 && _logShown - _pageLines[gone] >= LogLimit)
        {
            _logShown -= _pageLines[gone];
            lost += _pageRuns[gone++];
        }
        if (gone > 0)
        {
            pages.RemoveRange(0, gone);
            _pageLines.RemoveRange(0, gone);
            _pageRuns.RemoveRange(0, gone);
            _logRuns.RemoveRange(0, lost);
        }

        if (!follow || _scrollPosted) return;
        _scrollPosted = true;
        Dispatcher.UIThread.Post(() =>
        {
            _scrollPosted = false;
            LogScroll.ScrollToEnd();
        }, DispatcherPriority.Background);
    }

    /// <summary>After a theme change the lines already written keep their meaning and take the new colours.</summary>
    void Recolour()
    {
        foreach (var (run, hue) in _logRuns) run.Foreground = Ansi.Brush(hue);
    }

    // ------------------------------------------------------------ the end

    void SaveWindow()
    {
        if (WindowState == WindowState.Normal)
        {
            _settings.Width = Width;
            _settings.Height = Height;
        }
        // Only a width the person chose is kept. The width of the markup is written as 0, "by
        // default": a number saved at every close would hold the window at the former default
        // after the markup has changed it.
        double side = Zones.ColumnDefinitions[2].ActualWidth;
        _settings.Side = Math.Abs(side - _sideOfMarkup) < 0.5 ? 0 : side;
        _settings.Save();
    }
}
