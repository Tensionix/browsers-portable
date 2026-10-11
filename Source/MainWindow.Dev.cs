#if DEBUG
using System;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Media.Imaging;
using AudionBrowsersPortable.Core;

namespace AudionBrowsersPortable;

/// <summary>
/// A way to check the window without a mouse - a real pointer clicks into whatever window is in
/// front, and that may be the owner's. A Debug build alone reads AUDION_DEV_SCRIPT: steps
/// separated by ";", run one after another once the window has read the manifest:
///   tab=ID | part=OPERATION | press=OPERATION | set=FIELD=VALUE | reset | source=PATH |
///   target=PATH | choose=source:PATH | pin=source | paths=reset | history=source |
///   clear=source|target|both | run=OPERATION | stop | answer=yes | answer=yes,yes |
///   say=OPERATION | pulse=TEXT | hover=ID,ID | off | off=no | manifest | lang=en | theme=light |
///   size=1400x860 | log=wide | log=normal | list |
///   flood=LINES:BATCH | wait=3 | idle | shot=file.png | dump | crash=ui | close
/// "idle" waits until no operation runs. "say" writes what a press would start, without starting
/// it. "press" raises the click of the button of an operation - what a person's press does.
/// A step that runs an operation or clears a folder works only on a stand: a project folder of
/// the check's own, named by AUDION_DEV_PROJECT (dev-run.ps1 -Stand). In the program itself such
/// a step is refused, whatever the script says: input, output and logs there are the owner's.
/// A release build has none of this.
/// </summary>
public partial class MainWindow
{
    bool _devDone;

    /// <summary>Before the window does anything: the keyboard is left to whoever sits at the machine.</summary>
    /// <summary>
    /// A window that runs a script of steps is not shown to whoever sits at the machine: it
    /// stands far off the screen, out of the taskbar, and never takes the keyboard. Its pictures
    /// are drawn by Avalonia from the window itself ("shot"), so nothing is lost by that.
    /// </summary>
    void DevSetup()
    {
        if (string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("AUDION_DEV_SCRIPT"))) return;
        ShowActivated = false;
        ShowInTaskbar = false;
        WindowStartupLocation = WindowStartupLocation.Manual;
        Position = new PixelPoint(-32000, -32000);
    }

    Node? DevNode(string id)
    {
        var node = _manifest?.Find(id);
        if (node is null) Journal.Add("[dev] no such node: " + id);
        return node;
    }

    /// <summary>Why a step that changes something may not go; "" when it may.</summary>
    string DevRefusal(Node? node = null)
    {
        if (Door.Project.Length == 0) return "refused: a check runs operations and clears folders only on a stand (dev-run.ps1 -Stand)";
        // On a stand the maintenance works on the folders of the stand. Any other dangerous
        // operation changes Windows itself - its certificates, its Defender - stand or no stand.
        if (node is { Dangerous: true } && _manifest?.Maintenance.Contains(node) != true)
            return "refused: a check never runs an operation that changes Windows: " + node.Id;
        return "";
    }

    async void RunDevScript()
    {
        if (_devDone) return;
        _devDone = true;
        var script = Environment.GetEnvironmentVariable("AUDION_DEV_SCRIPT");
        if (string.IsNullOrWhiteSpace(script)) return;
        await Task.Delay(600);
        foreach (var raw in script.Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
        {
            var parts = raw.Split('=', 2);
            var value = parts.Length > 1 ? parts[1] : "";
            try
            {
                switch (parts[0])
                {
                    case "tab":
                        if (DevNode(value) is { } tab) SelectTab(tab);
                        break;
                    // A part of the open section, as a press on its switch: named by the first operation of its block.
                    case "part":
                        if (PartRow.Children.OfType<Avalonia.Controls.Primitives.ToggleButton>()
                                .FirstOrDefault(item => Avalonia.Automation.AutomationProperties.GetAutomationId(item) == "part_" + value) is { } part)
                        {
                            part.RaiseEvent(new Avalonia.Interactivity.RoutedEventArgs(Button.ClickEvent));
                            await Task.Delay(200);
                        }
                        else Journal.Add("[dev] no such part in sight: " + value);
                        break;
                    case "set" when value.Split('=', 2) is [var id, var text]:
                        if (_manifest?.All().SelectMany(node => node.Fields).FirstOrDefault(field => field.Id == id) is { } field)
                            _values.Set(field, text.Replace("|", Values.ListSeparator.ToString()));
                        else Journal.Add("[dev] no such field: " + id);
                        break;
                    case "reset":
                        if (_manifest is not null) _values.Reset(_manifest.All().SelectMany(node => node.Fields));
                        break;
                    case "source": SetSource(value); break;
                    case "target": SetTarget(value); break;
                    // A path chosen as a person chooses it: set and remembered in the history.
                    case "choose" when value.Split(':', 2) is [var role, var chosen]: Choose(role == "source", chosen); break;
                    case "pin": TogglePin(value == "source"); break;
                    case "paths": ResetPaths(quiet: false); break;
                    case "history":
                        foreach (var entry in _history?.Entries(value == "source") ?? new())
                            Journal.Add($"[dev] history {value}: {(entry.Pinned ? "pinned " : "")}{entry.Path} x{entry.Count}");
                        break;
                    case "clear":
                        if (DevRefusal() is { Length: > 0 } notHere) { Journal.Add("[dev] " + notHere); break; }
                        ClearFolders(value is "source" or "both", value is "target" or "both");
                        break;
                    // Why a path may not be cleared, as the guard answers - nothing is deleted, nothing is asked.
                    case "refusal":
                        Journal.Add($"[dev] refusal {value}: {(FolderCleaner.Refusal(Paths.Full(value), WorkingFolders) is { Length: > 0 } why ? why : "(may be cleared)")}; bin {FolderCleaner.HasBin(Paths.Full(value))}");
                        break;
                    // A path typed into a box and the box left, as a person does it.
                    case "type" when value.Split(':', 2) is [var box, var typed]:
                        (box == "source" ? SourceBox : TargetBox).Text = typed;
                        TakeTyped(box == "source");
                        break;
                    case "run":
                        if (DevNode(value) is not { } operation) break;
                        if (DevRefusal(operation) is { Length: > 0 } refusal) { Journal.Add("[dev] " + refusal); break; }
                        Press(operation);
                        break;
                    // The click of the button of an operation, as a person's press raises it.
                    case "press":
                    {
                        if (DevNode(value) is not { } pressed) break;
                        if (DevRefusal(pressed) is { Length: > 0 } no) { Journal.Add("[dev] " + no); break; }
                        var button = _runButtons.Concat(_topRunButtons).FirstOrDefault(item => ReferenceEquals(item.Tag, pressed));
                        if (button is null) Journal.Add("[dev] no button in sight for: " + value);
                        else if (!button.IsEnabled) Journal.Add("[dev] the button is off: " + value);
                        else button.RaiseEvent(new Avalonia.Interactivity.RoutedEventArgs(Button.ClickEvent));
                        break;
                    }
                    // STOP as a person presses it: the button itself, and only while it is there to be pressed.
                    case "stop":
                        if (!StopButton.IsVisible || !StopButton.IsEnabled) Journal.Add($"[dev] STOP cannot be pressed: visible {StopButton.IsVisible}, enabled {StopButton.IsEnabled}");
                        else StopButton.RaiseEvent(new Avalonia.Interactivity.RoutedEventArgs(Button.ClickEvent));
                        break;
                    // The answer to the next question of the window: "yes" or "no".
                    case "answer": Views.Dialog.DevAnswer = value; break;
                    // What a press would start, into the journal - without starting it.
                    case "say":
                        if (DevNode(value) is { } asked)
                        {
                            Journal.Add("[dev] console: " + Door.RunLine(asked.Id, _source, _target, _values.ChangedOf(asked)));
                            Journal.Add("[dev] params: " + _values.ForDoor(asked).Replace("\r", "").Replace("\n", " "));
                        }
                        break;
                    // The pulse of a long step, as the door says it: a text shows it, an empty one is an ordinary line that takes it away.
                    case "pulse":
                        OnOutput(new[] { value.Length > 0 ? Door.Control + System.Text.Json.JsonSerializer.Serialize(new { pulse = value }) : "[OK] the step is over" });
                        break;
                    // The look of a button under the pointer, without a pointer: the buttons named by their
                    // automation ids, parted by commas, are shown so; an empty value takes it off them all.
                    case "hover":
                    {
                        var named = value.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
                        foreach (var item in Avalonia.VisualTree.VisualExtensions.GetVisualDescendants(this).OfType<Button>())
                            ((IPseudoClasses)item.Classes).Set(":pointerover", named.Contains(Avalonia.Automation.AutomationProperties.GetAutomationId(item)));
                        break;
                    }
                    // The manifest asked for again, as the window asks when it comes back to the front without one.
                    case "manifest": await ReadManifest(); break;
                    // The buttons that run something, switched off as a run switches them off - without a run.
                    case "off":
                        foreach (var item in _runButtons.Concat(_topRunButtons)) item.IsEnabled = value == "no";
                        break;
                    case "lang": if (Lang.Code != value) SwitchLanguage(); break;
                    case "theme": SetTheme(value); break;
                    case "size" when value.Split('x') is [var w, var h]: Width = double.Parse(w); Height = double.Parse(h); break;
                    case "log": SetWideLog(value == "wide"); break;
                    case "list": ListSource(); break;
                    // What a line of the journal costs: LINES lines in batches of BATCH, the layouts of the pages counted.
                    case "flood" when value.Split(':') is [var total, var batch]:
                    {
                        int all = int.Parse(total), each = int.Parse(batch), laid = Controls.LogPage.Laid, batches = 0;
                        var clock = System.Diagnostics.Stopwatch.StartNew();
                        for (int done = 0; done < all; done += each)
                        {
                            Journal.Show(Enumerable.Range(done, Math.Min(each, all - done)).Select(i => $"[INFO] flood line {i} of {all}: a line as long as an ordinary line of a build").ToList());
                            batches++;
                            await Task.Delay(1);
                        }
                        Journal.Add($"[dev] flood {all} lines in {batches} batches: {clock.ElapsedMilliseconds} ms, layouts {Controls.LogPage.Laid - laid}, pages {LogPages.Children.Count}");
                        break;
                    }
                    case "wait": await Task.Delay(TimeSpan.FromSeconds(double.Parse(value, System.Globalization.CultureInfo.InvariantCulture))); break;
                    case "idle":
                        await Task.Delay(400);
                        for (int i = 0; i < 7200 && _runner.Running; i++) await Task.Delay(500);
                        await Task.Delay(300);
                        break;
                    // The window drawn into a file by Avalonia itself: nothing is put on top of the owner's windows.
                    case "shot":
                    {
                        await Task.Delay(400);
                        var scale = RenderScaling;
                        var size = new PixelSize(Math.Max(1, (int)(Bounds.Width * scale)), Math.Max(1, (int)(Bounds.Height * scale)));
                        using var bitmap = new RenderTargetBitmap(size, new Vector(96 * scale, 96 * scale));
                        bitmap.Render(this);
                        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(value))!);
#pragma warning disable CS0618
                        bitmap.Save(value);
#pragma warning restore CS0618
                        Journal.Add($"[dev] shot {value} {size.Width}x{size.Height} scale {scale}");
                        break;
                    }
                    // What the window holds now, in words: the section, the cards and their sizes.
                    case "dump":
                        Journal.Add($"[dev] tab {_tab?.Id}; cards: {Cards.Children.Count}, {Cards.Bounds.Width:0}x{Cards.Bounds.Height:0} in {CardScroll.Viewport.Width:0}x{CardScroll.Viewport.Height:0}, to scroll {Math.Max(0, CardScroll.Extent.Height - CardScroll.Viewport.Height):0}; "
                            + $"tabs: {TabRow.Children.Count}, {TabRow.Bounds.Width:0}x{TabRow.Bounds.Height:0}; run buttons: {_runButtons.Count}+{_topRunButtons.Count}; source {_source}; target {_target}; "
                            + $"zones {Zones.Bounds.Width:0}: left {Zones.ColumnDefinitions[0].ActualWidth:0}, side {Zones.ColumnDefinitions[2].ActualWidth:0}; status: {StatusText.Text}; note: {(CardsNote.IsVisible ? CardsNote.Text : "")}");
                        break;
                    // An error nobody catches, thrown on purpose - the guard must keep the window and write logs\gui-crash-*.log.
                    case "crash":
                        Avalonia.Threading.Dispatcher.UIThread.Post(() => throw new InvalidOperationException("dev: an error thrown on purpose in the thread of the window"));
                        break;
                    case "close": Close(); break;
                    default: Journal.Add("[dev] unknown step: " + raw); break;
                }
            }
            catch (Exception ex) { Journal.Add($"[dev] {raw}: {ex.Message}"); }
            await Task.Delay(250);
        }
        Journal.Add("[dev] script done");
    }
}
#endif
