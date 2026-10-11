using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Input;
using Avalonia.Layout;
using Avalonia.Platform.Storage;
using AudionBrowsersPortable.Core;
using AudionBrowsersPortable.Views;

namespace AudionBrowsersPortable;

/// <summary>
/// The working folders: where the finished builds lie (Source) and where new ones, archives and
/// downloads go (Target). Every start begins with input and output of the program - the last
/// choice is not remembered. A path inside the program is shown from its root; the window keeps
/// the full one. The history of paths and its pins are the file the window on NiceGUI kept
/// (config\path_history.json): what was pinned there is pinned here.
/// The disk is not asked on the thread of the window where a folder may be slow to answer - a
/// flash drive that has gone, a share that is down: the window would stand still for the timeout.
/// </summary>
public partial class MainWindow
{
    /// <summary>The source folder, as a full path.</summary>
    string _source = "";
    /// <summary>The target folder, as a full path.</summary>
    string _target = "";

    PathHistory? _history;

    /// <summary>The folders are being cleared: nothing starts and no path changes until that has ended.</summary>
    bool _clearing;

    /// <summary>When a typed path was last refused, and which one: a run pressed at that very moment is not started.</summary>
    DateTime _pathRefusedAt;
    string _pathRefused = "";

    string DefaultSource => _manifest?.Paths.Input is { Length: > 0 } input ? input : Path.Combine(Paths.Root, "input");
    string DefaultTarget => _manifest?.Paths.Output is { Length: > 0 } output ? output : Path.Combine(Paths.Root, "output");

    /// <summary>The folders of the program that are there to be cleared.</summary>
    string[] WorkingFolders => new[] { DefaultSource, DefaultTarget };

    static Task<bool> FolderExists(string path) => Task.Run(() => { try { return Directory.Exists(path); } catch { return false; } });

    void WireIo()
    {
        PickFolderButton.Click += (_, _) => PickFolder(source: true);
        PickTargetButton.Click += (_, _) => PickFolder(source: false);
        ResetPathsButton.Click += (_, _) => ResetPaths(quiet: false);
        ListFilesButton.Click += (_, _) => ListSource();
        SourceOpenButton.Click += (_, _) => Reveal(_source);
        TargetOpenButton.Click += (_, _) => Reveal(_target);
        SourcePinButton.Click += (_, _) => TogglePin(source: true);
        TargetPinButton.Click += (_, _) => TogglePin(source: false);
        WireHistory(SourceHistoryButton, source: true);
        WireHistory(TargetHistoryButton, source: false);

        // What to clear is asked first, then the question names the paths.
        var clear = new MenuFlyout
        {
            Placement = PlacementMode.BottomEdgeAlignedLeft,
            Items =
            {
                ClearItem("clear_source", "ИСТОЧНИК: удалить содержимое", source: true, target: false),
                ClearItem("clear_target", "НАЗНАЧЕНИЕ: удалить содержимое", source: false, target: true),
                ClearItem("clear_both", "ИСТОЧНИК и НАЗНАЧЕНИЕ: удалить содержимое обоих", source: true, target: true),
            },
        };
        // The items of a menu are not in the tree of the window, so the switch of the language
        // does not reach them: they take the language in use each time the menu opens.
        clear.Opening += (_, _) =>
        {
            foreach (var item in clear.Items.OfType<MenuItem>()) item.Header = Lang.T(item.Tag as string ?? "");
        };
        // The press that opened this menu took the focus off a path box. A path typed there and
        // refused at that very press (a typo, a drive that is gone) put the box back to the path
        // before it - and that folder must not be what the menu then clears.
        clear.Opened += (_, _) =>
        {
            if (!PathJustRefused()) return;
            clear.Hide();
            Say(Lang.F("очистка отменена - введённый путь не принят: {0}", _pathRefused), refused: true);
        };
        ClearFoldersButton.Flyout = _clearMenu = clear;

        // A path typed by hand is taken when the box is left or Enter is pressed.
        SourceBox.LostFocus += (_, _) => TakeTyped(source: true);
        TargetBox.LostFocus += (_, _) => TakeTyped(source: false);
        SourceBox.KeyDown += (_, e) => { if (e.Key == Key.Enter) { TakeTyped(source: true); e.Handled = true; } };
        TargetBox.KeyDown += (_, e) => { if (e.Key == Key.Enter) { TakeTyped(source: false); e.Handled = true; } };

        // A folder dropped on the window becomes the source.
        DragDrop.SetAllowDrop(this, true);
        AddHandler(DragDrop.DragOverEvent, (_, e) =>
        {
            e.DragEffects = !Busy && e.DataTransfer.Contains(DataFormat.File) ? DragDropEffects.Link : DragDropEffects.None;
            e.Handled = true;
        });
        AddHandler(DragDrop.DropEvent, (_, e) =>
        {
            if (Busy) return;
            var path = (e.DataTransfer.TryGetFiles() ?? Array.Empty<IStorageItem>()).Select(item => item.TryGetLocalPath()).FirstOrDefault(item => item is not null);
            if (path is null) return;
            e.Handled = true;
            Dropped(path);
        });
    }

    /// <summary>What was dropped on the window: a folder is the source; a file means the folder it lies in.</summary>
    async void Dropped(string path)
    {
        var folder = await Task.Run(() =>
        {
            try { return Directory.Exists(path) ? path : File.Exists(path) ? Path.GetDirectoryName(path) ?? "" : ""; }
            catch { return ""; }
        });
        if (Busy) return;
        if (folder.Length == 0) { Say(Lang.F("источника нет на диске: {0}", path), refused: true); return; }
        Choose(source: true, folder);
    }

    MenuFlyout? _clearMenu;

    MenuItem ClearItem(string id, string text, bool source, bool target)
    {
        var item = new PressableMenuItem { Header = Lang.T(text), Tag = text };
        Avalonia.Automation.AutomationProperties.SetAutomationId(item, id);
        item.Click += (_, _) =>
        {
            // A press that came without a pointer leaves the menu open: closed here either way.
            _clearMenu?.Hide();
            ClearFolders(source, target);
        };
        return item;
    }

    /// <summary>
    /// An item of a menu that a program can press as a person does. Avalonia tells UI Automation
    /// that an item of a menu is there and gives it nothing to do with it: a screen reader could
    /// read "delete the contents" and not choose it, and a check of the built program could not
    /// press it (Source\Tools\release-walk.ps1).
    /// </summary>
    sealed class PressableMenuItem : MenuItem
    {
        protected override Type StyleKeyOverride => typeof(MenuItem);

        protected override Avalonia.Automation.Peers.AutomationPeer OnCreateAutomationPeer() => new Peer(this);

        sealed class Peer : Avalonia.Automation.Peers.MenuItemAutomationPeer, Avalonia.Automation.Provider.IInvokeProvider
        {
            public Peer(MenuItem owner) : base(owner) { }

            public void Invoke() => Owner.RaiseEvent(new Avalonia.Interactivity.RoutedEventArgs(MenuItem.ClickEvent));
        }
    }

    /// <summary>
    /// A typed path was refused a moment ago - by the very press that is being handled now, which
    /// took the focus off the box. Asked once: the answer is used up.
    /// </summary>
    bool PathJustRefused()
    {
        if (DateTime.UtcNow - _pathRefusedAt >= TimeSpan.FromSeconds(2)) return false;
        _pathRefusedAt = default;
        return true;
    }

    /// <summary>The folders every project has and the history of paths: made at the start, as the window on NiceGUI made them.</summary>
    void EnsureFolders()
    {
        if (_manifest is null) return;
        foreach (var folder in new[] { _manifest.Paths.Input, _manifest.Paths.Output, _manifest.Paths.Logs, _manifest.Paths.Report })
        {
            if (string.IsNullOrEmpty(folder)) continue;
            try { Directory.CreateDirectory(folder); }
            catch (Exception ex) { Journal.Add(Lang.F("[окно] папка не создана: {0} - {1}", folder, ex.Message)); }
        }
        var file = Path.Combine(Paths.ProjectConfig, "path_history.json");
#if DEBUG
        // A check keeps a history of its own: the paths it tries are not the owner's.
        if (Environment.GetEnvironmentVariable("AUDION_DEV_HISTORY") is { Length: > 0 } own) file = own;
#endif
        _history = new PathHistory(file);
        _history.EnsureInitial(DefaultSource, DefaultTarget);
    }

    // ------------------------------------------------------------ the two paths

    void SetSource(string path)
    {
        _source = Paths.Full(path);
        ShowPath(SourceBox, _source);
        ShowPins();
    }

    void SetTarget(string path)
    {
        _target = Paths.Full(path);
        ShowPath(TargetBox, _target);
        ShowPins();
    }

    /// <summary>A path in its box, scrolled to its end: of a long path the last folder says more than the drive.</summary>
    static void ShowPath(TextBox box, string full)
    {
        box.Text = Paths.Short(full);
        box.CaretIndex = box.Text?.Length ?? 0;
    }

    /// <summary>A path a person chose - picked, typed, dropped or taken from the history: it is set and remembered.</summary>
    void Choose(bool source, string path)
    {
        if (source) SetSource(path); else SetTarget(path);
        _history?.Remember(source, source ? _source : _target);
        ShowPins();
        Say(Lang.F(source ? "источник: {0}" : "назначение: {0}", Paths.Short(source ? _source : _target)), refused: false, passing: true);
    }

    /// <summary>Both paths back to input and output of the program; the history is emptied, the pins stay.</summary>
    void ResetPaths(bool quiet)
    {
        if (Busy) { Say("идёт операция: пути не меняются", refused: true); return; }
        SetSource(DefaultSource);
        SetTarget(DefaultTarget);
        if (quiet) return;
        int pins = _history?.ClearKeepPins() ?? 0;
        ShowPins();
        Say(Lang.F("источник и назначение - input и output программы · закреплений осталось {0}", pins), refused: false, passing: true);
    }

    /// <summary>
    /// What was typed into a path box. A source that is not a folder on the disk is not taken: the
    /// box goes back, and the refusal is remembered for a moment - the press that took the focus
    /// off the box may have been a press of a run button, and that run must not go to the path
    /// that stood there before. The disk is asked here, at once: the person is waiting for the
    /// answer to what was just typed.
    /// </summary>
    void TakeTyped(bool source)
    {
        var box = source ? SourceBox : TargetBox;
        var typed = Paths.Full(box.Text ?? "");
        var held = source ? _source : _target;
        if (typed.Length == 0 || string.Equals(typed, held, StringComparison.OrdinalIgnoreCase)) { ShowPath(box, held); return; }
        void Refuse(string why)
        {
            ShowPath(box, held);
            _pathRefused = typed;
            _pathRefusedAt = DateTime.UtcNow;
            Say(why, refused: true);
        }
        if (Busy) { Refuse(Lang.T("идёт операция: пути не меняются")); return; }
        if (source && !Directory.Exists(typed)) { Refuse(Lang.F("источника нет на диске: {0}", typed)); return; }
        Choose(source, typed);
    }

    async void PickFolder(bool source)
    {
        if (Busy) { Say("идёт операция: пути не меняются", refused: true); return; }
        var held = source ? _source : _target;
        IStorageFolder? from = null;
        try
        {
            var start = await FolderExists(held) ? held : Path.GetDirectoryName(held);
            if (!string.IsNullOrEmpty(start) && await FolderExists(start)) from = await StorageProvider.TryGetFolderFromPathAsync(start);
        }
        catch { }
        var picked = await StorageProvider.OpenFolderPickerAsync(new FolderPickerOpenOptions
        {
            Title = Lang.T(source ? "Папка-источник" : "Папка назначения"), AllowMultiple = false, SuggestedStartLocation = from,
        });
        // A run that started while the picker stood open keeps the folders it began with.
        if (Busy) return;
        if (picked.Count == 0 || picked[0].TryGetLocalPath() is not { } path) { Say("выбор отменён", refused: false, passing: true); return; }
        Choose(source, path);
    }

    // ------------------------------------------------------------ the history and the pins

    void WireHistory(Button button, bool source)
    {
        var flyout = new Flyout { Placement = PlacementMode.BottomEdgeAlignedRight };
        // Built when it opens: the file may have been changed by another window of the program.
        flyout.Opening += (_, _) => flyout.Content = HistoryList(source, flyout);
        button.Flyout = flyout;
    }

    Control HistoryList(bool source, Flyout flyout)
    {
        var rows = new StackPanel { MinWidth = 420, MaxWidth = 760 };
        var entries = _history?.Entries(source) ?? new List<HistoryEntry>();
        var held = source ? _source : _target;
        var shown = new List<(Button Row, string Path)>();
        foreach (var entry in entries)
        {
            var face = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 7 };
            var pin = Icons.Sign("pin");
            pin.Opacity = entry.Pinned ? 1 : 0;
            if (entry.Pinned) pin.Classes.Add("amber");
            face.Children.Add(pin);
            face.Children.Add(new TextBlock { Text = Paths.Short(entry.Path), TextTrimming = Avalonia.Media.TextTrimming.CharacterEllipsis });
            var row = new Button { Classes = { "histrow" }, Content = face };
            row.Classes.Set("current", string.Equals(entry.Path, held, StringComparison.OrdinalIgnoreCase));
            ToolTip.SetTip(row, entry.Path);
            var path = entry.Path;
            row.Click += async (_, _) =>
            {
                flyout.Hide();
                if (Busy) { Say("идёт операция: пути не меняются", refused: true); return; }
                if (source && !await FolderExists(path)) { Say(Lang.F("источника нет на диске: {0}", path), refused: true); return; }
                if (Busy) return;
                Choose(source, path);
            };
            rows.Children.Add(row);
            shown.Add((row, path));
        }
        if (entries.Count == 0) rows.Children.Add(new TextBlock { Classes = { "note" }, Text = Lang.T("история пуста"), Margin = new Thickness(8, 4) });
        // Which of them are still on the disk is found out off the thread of the window - up to a
        // hundred paths, some on drives that no longer answer - and painted when the answer comes.
        _ = MarkGone(shown);
        return new ScrollViewer { MaxHeight = 420, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, Content = rows };
    }

    static async Task MarkGone(List<(Button Row, string Path)> shown)
    {
        var gone = await Task.Run(() => shown.Select(item => { try { return !Directory.Exists(item.Path); } catch { return true; } }).ToList());
        for (int i = 0; i < shown.Count; i++)
        {
            if (!gone[i]) continue;
            shown[i].Row.Classes.Set("gone", true);
            ToolTip.SetTip(shown[i].Row, Lang.F("нет на диске: {0}", shown[i].Path));
        }
    }

    void TogglePin(bool source)
    {
        if (_history is null) return;
        var path = source ? _source : _target;
        bool pinned = !_history.IsPinned(source, path);
        _history.SetPinned(source, path, pinned);
        ShowPins();
        Say(Lang.T(pinned ? "путь закреплён" : "закрепление снято"), refused: false, passing: true);
    }

    /// <summary>The pin of a line is lit while its path is pinned.</summary>
    void ShowPins()
    {
        if (_history is null) return;
        SourcePinButton.Classes.Set("on", _history.IsPinned(true, _source));
        TargetPinButton.Classes.Set("on", _history.IsPinned(false, _target));
    }

    // ------------------------------------------------------------ what lies in the source

    /// <summary>
    /// What lies in the source, into the journal: its folders and files of the first level, by the
    /// alphabet. A build of a browser is tens of thousands of files - the folders of the builds
    /// are what there is to see.
    /// </summary>
    async void ListSource()
    {
        var source = _source;
        ListFilesButton.IsEnabled = false;
        try
        {
            var found = await Task.Run(() =>
            {
                var info = new DirectoryInfo(source);
                if (!info.Exists) return ((List<string> Folders, List<string> Files)?)null;
                return (info.EnumerateDirectories().Select(item => item.Name).OrderBy(name => name, StringComparer.CurrentCultureIgnoreCase).ToList(),
                        info.EnumerateFiles().Select(item => item.Name).OrderBy(name => name, StringComparer.CurrentCultureIgnoreCase).ToList());
            });
            if (found is not { } lists) { Say(Lang.F("источника нет на диске: {0}", source), refused: true); return; }
            var (folders, files) = lists;
            var lines = new List<string> { Lang.F("[окно] в источнике {0}: папок {1}, файлов {2}", Paths.Short(source), folders.Count, files.Count) };
            lines.AddRange(folders.Select((name, index) => $"{index + 1,4}  [{name}]"));
            lines.AddRange(files.Select((name, index) => $"{folders.Count + index + 1,4}  {name}"));
            Journal.List(lines);
            Say(Lang.F("в источнике: папок {0}, файлов {1}", folders.Count, files.Count), refused: false, passing: true);
        }
        catch (Exception ex) { Say(Lang.F("источник не прочитан: {0}", ex.Message), refused: true); }
        finally { ListFilesButton.IsEnabled = true; }
    }

    // ------------------------------------------------------------ clearing

    /// <summary>
    /// Clearing. Everything is taken before the questions. A folder outside the program is asked
    /// about on its own, whichever line names it - it may hold builds with somebody's profile, and
    /// another copy may not exist. The guards refuse the root of a drive, the program and its own
    /// folders. While the clearing goes nothing else starts, no path changes and the window does
    /// not close. What is deleted goes to the Recycle Bin where the drive has one - the question
    /// says which it is - and Windows itself warns of what the Bin cannot take.
    /// </summary>
    async void ClearFolders(bool source, bool target)
    {
        if (_runner.Running) { Say("идёт другая операция", refused: true); return; }
        if (_clearing) { Say("очистка уже идёт", refused: true); return; }
        var from = _source;
        var to = _target;
        bool same = string.Equals(from, to, StringComparison.OrdinalIgnoreCase);
        var paths = new List<(bool Source, string Path)>();
        if (source) paths.Add((true, from));
        if (target && !(source && same)) paths.Add((false, to));
        var working = WorkingFolders;
        foreach (var (_, path) in paths)
            if (FolderCleaner.Refusal(path, working) is { Length: > 0 } refusal) { Say(refusal, refused: true); return; }

        string Fate(string path) => FolderCleaner.HasBin(path)
            ? "Удалённое уйдёт в Корзину; о том, чего Корзина не вместит, Windows спросит отдельно."
            : "У этого диска нет Корзины: удалённое не вернуть.";

        // Outside the program: neither its input nor its output, nor a folder inside them.
        var outside = paths.Where(item => !working.Any(folder => IsWithin(item.Path, folder))).ToList();
        if (outside.Count > 0)
        {
            var warning = new List<string> { "Будут удалены все файлы и вложенные папки - сборки вместе с их профилями: закладками, паролями, расширениями." };
            foreach (var (_, path) in outside) { warning.Add(""); warning.Add(path); warning.Add(Fate(path)); }
            var sure = await Dialog.Ask(this, "Очистить папку вне программы?", warning, "УДАЛИТЬ", Tone.Danger);
            if (sure is null) return;
        }
        var lines = new List<string>();
        foreach (var (isSource, path) in paths) lines.Add(Lang.F(isSource ? "ИСТОЧНИК: {0}" : "НАЗНАЧЕНИЕ: {0}", path));
        lines.Add("");
        lines.Add("Сами папки остаются.");
        foreach (var fate in paths.Select(item => Fate(item.Path)).Distinct()) lines.Add(fate);
        var go = await Dialog.Ask(this, "Удалить содержимое?", lines, "УДАЛИТЬ", Tone.Danger);
        if (go is null) return;
        // An operation started while the questions stood works with these very folders; a path
        // changed meanwhile is no longer the one the questions named.
        if (Busy || !IsVisible) { Say("идёт другая операция", refused: true); return; }
        if (!string.Equals(from, _source, StringComparison.OrdinalIgnoreCase) || !string.Equals(to, _target, StringComparison.OrdinalIgnoreCase))
        {
            Say("пути изменились, пока стоял вопрос: ничего не удалено", refused: true);
            return;
        }

        _clearing = true;
        ApplyBusy();
        var steady = _steady;
        Say("очищаю…");
        // The line goes back to what stood there before the clearing, not to "clearing".
        _steady = steady;
        var window = TryGetPlatformHandle()?.Handle ?? IntPtr.Zero;
        // What is said at the end is what was done: a folder that was not there was not cleared,
        // and a clearing that broke half-way has already taken something.
        int cleared = 0, absent = 0;
        try
        {
            foreach (var (isSource, path) in paths)
            {
                var done = await Task.Run(() => FolderCleaner.Clear(path, working, window));
                if (done.Kind == "missing")
                {
                    absent++;
                    Journal.Add(Lang.F(isSource ? "[окно] ИСТОЧНИКА нет на диске, очищать нечего: {0}" : "[окно] НАЗНАЧЕНИЯ нет на диске, очищать нечего: {0}", done.Path));
                    continue;
                }
                cleared++;
                Journal.Add(Lang.F(isSource ? "[окно] очищен ИСТОЧНИК: {0} · удалено {1}" : "[окно] очищено НАЗНАЧЕНИЕ: {0} · удалено {1}", done.Path, done.Removed));
            }
            if (absent == 0) Say("очищено", refused: false, passing: true);
            else if (cleared == 0) Say("папки нет на диске: очищать нечего", refused: true);
            else Say("очищено не всё: одной из папок нет на диске - что именно, сказано в журнале", refused: true);
        }
        // A refusal said in the status line goes to the journal by itself (Say).
        catch (OperationCanceledException)
        {
            Say("очистка остановлена в вопросе Windows: удалено не всё", refused: true);
        }
        catch (InvalidOperationException ex)
        {
            // A guard refused at the last moment: of this path nothing was touched.
            Say(ex.Message, refused: true);
        }
        catch (Exception ex)
        {
            Say(Lang.F("очистка не вышла: {0} - часть содержимого могла уже уйти", ex.Message), refused: true);
        }
        finally
        {
            _clearing = false;
            ApplyBusy();
            if (_closeAfterClearing) Close();
        }
    }

    static bool IsWithin(string path, string folder)
    {
        try
        {
            var full = Path.GetFullPath(path).TrimEnd('\\', '/');
            var home = Path.GetFullPath(folder).TrimEnd('\\', '/');
            return string.Equals(full, home, StringComparison.OrdinalIgnoreCase) || full.StartsWith(home + "\\", StringComparison.OrdinalIgnoreCase);
        }
        catch { return false; }
    }
}
