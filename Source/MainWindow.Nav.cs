using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Layout;
using Avalonia.Media;
using Avalonia.Threading;
using AudionBrowsersPortable.Core;

namespace AudionBrowsersPortable;

/// <summary>
/// The sections of the window and the blocks under them. One rule for every section, taken from the
/// manifest - no section and no operation is known here by its name:
///   - a group of the manifest is a section: a large button of the row under the header, with
///     its sign and a line of what the section is for;
///   - the operations of a section are all in sight at once, in the logical blocks the manifest
///     names (`block`): a block is a card with its name;
///   - an operation is a button with its sign, its name and, under the name, what it does;
///   - an operation with fields brings its form into its card, block by block; an operation the
///     manifest places beside another stands in that card as a second button and reads the
///     same fields;
///   - operations without fields that share a block stand in one card, button beside button;
///   - the maintenance of the manifest is a row of small buttons in the header.
/// </summary>
public partial class MainWindow
{
    /// <summary>The section that is open.</summary>
    Node? _tab;
    /// <summary>The buttons that run something: all of them wait while a run goes.</summary>
    readonly List<Button> _runButtons = new();
    /// <summary>The buttons of the maintenance: they outlive a change of section.</summary>
    readonly List<Button> _topRunButtons = new();
    /// <summary>"Back to the defaults" of the cards built last, with the fields each one answers for.</summary>
    readonly List<(Button Button, List<Field> Fields)> _resetButtons = new();

    int CountOperations() => _manifest?.Operations().Count() ?? 0;

    static string Upper(string text) => text.ToUpper(CultureInfo.CurrentCulture);

    /// <summary>The name of a node and what it does: the tooltip of its tab or button.</summary>
    static string TipOf(Node node)
    {
        var lines = new List<string> { node.Name };
        if (node.Tip.Length > 0) lines.Add(node.Tip);
        else if (node.About.Length > 0) lines.Add(node.About);
        return string.Join("\n", lines);
    }

    /// <summary>The sign a node names; a sign the set does not have is noted for the journal and the self-check.</summary>
    Avalonia.Controls.Shapes.Path? SignOf(Node node, string fallback = "")
    {
        if (node.Icon.Length > 0 && !Icons.Has(node.Icon)) _noSign.Add($"{node.Id}: {node.Icon}");
        return Icons.Face(node.Icon) ?? Icons.Face(fallback);
    }

    /// <summary>What a large button shows: its sign and its name in a line, a quiet line of what it does under them.</summary>
    static Control Face(Avalonia.Controls.Shapes.Path? sign, string name, string about)
    {
        var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 7, HorizontalAlignment = HorizontalAlignment.Center };
        if (sign is not null) head.Children.Add(sign);
        head.Children.Add(new TextBlock { Classes = { "bigname" }, Text = Upper(name) });
        var face = new StackPanel { VerticalAlignment = VerticalAlignment.Center, Children = { head } };
        if (about.Length > 0) face.Children.Add(new TextBlock { Classes = { "bigabout" }, Text = about });
        return face;
    }

    // ------------------------------------------------------------ the sections

    void BuildTabs()
    {
        if (_manifest is null) return;
        TabRow.Children.Clear();
        TabRow.ColumnDefinitions.Clear();
        TopButtons.Children.Clear();
        _topRunButtons.Clear();
        var sections = _manifest.Nodes.Where(node => !node.Direct).ToList();
        foreach (var node in _manifest.Nodes)
        {
            // An operation of the root that needs nothing but a press is a button, not a section.
            if (node.Direct) { TopButtons.Children.Add(TopButton(node)); continue; }
            var tab = new ToggleButton
            {
                Classes = { "switch", "bigtab" }, Tag = node.Id,
                Content = Face(SignOf(node), node.Name, node.About),
            };
            // A name a check of the built program finds the section by (UI Automation): the id of the manifest.
            Avalonia.Automation.AutomationProperties.SetAutomationId(tab, "tab_" + node.Id);
            ToolTip.SetTip(tab, TipOf(node));
            tab.Click += (_, _) => SelectTab(node);
            // The sections share the width of the window equally, with a gap between them.
            if (TabRow.ColumnDefinitions.Count > 0) TabRow.ColumnDefinitions.Add(new ColumnDefinition(new GridLength(6)));
            TabRow.ColumnDefinitions.Add(new ColumnDefinition(GridLength.Star));
            Grid.SetColumn(tab, TabRow.ColumnDefinitions.Count - 1);
            TabRow.Children.Add(tab);
        }
        foreach (var node in _manifest.Maintenance.Where(node => node.Runs && !node.Workbench))
            TopButtons.Children.Add(TopButton(node));

        var open = sections.FirstOrDefault(node => node.Id == _tab?.Id) ?? sections.FirstOrDefault();
        if (open is not null) SelectTab(open);
        ApplyBusy();
    }

    /// <summary>An operation of the program itself, as a small button of the header with its sign and name.</summary>
    Button TopButton(Node node)
    {
        var face = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
        if (SignOf(node, node.Dangerous ? "eraser" : "play") is { } sign) face.Children.Add(sign);
        face.Children.Add(new TextBlock { Text = Upper(node.Name) });
        var button = new Button { Tag = node, Classes = { "small", "word" }, Content = face };
        button.Classes.Set("burn", node.Dangerous);
        Avalonia.Automation.AutomationProperties.SetAutomationId(button, "run_" + node.Id);
        ToolTip.SetTip(button, TipOf(node));
        button.Click += (_, _) => Press(node);
        _topRunButtons.Add(button);
        _withButton.Add(node.Id);
        return button;
    }

    void SelectTab(Node node)
    {
        _tab = node;
        foreach (var child in TabRow.Children)
            if (child is ToggleButton tab) tab.IsChecked = (string?)tab.Tag == node.Id;
        BuildCards();
    }

    // ------------------------------------------------------------ the parts of a section

    /// <summary>The part of a section that is shown, by the id of the section; kept until the window closes.</summary>
    readonly Dictionary<string, string> _partOf = new();

    /// <summary>The block a card stands for: the block the manifest names, or the operation itself.</summary>
    static string BlockKey(Node host) => host.Block.Length > 0 ? host.Block : host.Id;

    /// <summary>
    /// The parts of a section, in the order of the manifest: its blocks, when more than one of
    /// them brings a form - two forms one under the other are a page to scroll, so each block
    /// becomes a part, and one part is in sight at a time. A section with one form, or with
    /// buttons alone, has no parts: everything of it is in sight at once.
    /// </summary>
    List<(string Key, Node First)> PartsOf(Node section)
    {
        var blocks = new List<(string Key, Node First)>();
        foreach (var (host, _) in section.Cards())
            if (host.Children.Count == 0 && blocks.All(block => block.Key != BlockKey(host))) blocks.Add((BlockKey(host), host));
        int forms = section.Cards().Count(card => card.Host.Children.Count == 0 && card.Host.FormFields.Any());
        return forms > 1 ? blocks : new List<(string, Node)>();
    }

    /// <summary>The part of the section in sight: the one chosen while it still exists, else the first; "" when the section has no parts.</summary>
    string PartOf(Node section, List<(string Key, Node First)> parts)
    {
        if (parts.Count == 0) return "";
        return _partOf.TryGetValue(section.Id, out var kept) && parts.Any(part => part.Key == kept) ? kept : parts[0].Key;
    }

    /// <summary>The row of parts over the cards: switches of equal width, each with the sign of its block's first operation.</summary>
    void BuildParts(Node section, List<(string Key, Node First)> parts, string shown)
    {
        PartRow.Children.Clear();
        PartRow.ColumnDefinitions.Clear();
        PartRow.IsVisible = parts.Count > 0;
        foreach (var (key, first) in parts)
        {
            var face = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 7, HorizontalAlignment = HorizontalAlignment.Center };
            if (SignOf(first) is { } sign) face.Children.Add(sign);
            face.Children.Add(new TextBlock { Classes = { "bigname" }, Text = Upper(first.BlockName.Length > 0 ? first.BlockName : first.Name) });
            var part = new ToggleButton { Classes = { "switch", "part" }, Content = face, Tag = key, IsChecked = key == shown };
            Avalonia.Automation.AutomationProperties.SetAutomationId(part, "part_" + first.Id);
            ToolTip.SetTip(part, TipOf(first));
            part.Click += (_, _) =>
            {
                _partOf[section.Id] = key;
                // Rebuilt after the press has been handled: the switch pressed is one of those removed.
                Dispatcher.UIThread.Post(() =>
                {
                    BuildCards();
                    if (Lang.Code != "ru") Lang.Walk(Cards);
                });
            };
            if (PartRow.ColumnDefinitions.Count > 0) PartRow.ColumnDefinitions.Add(new ColumnDefinition(new GridLength(6)));
            PartRow.ColumnDefinitions.Add(new ColumnDefinition(GridLength.Star));
            Grid.SetColumn(part, PartRow.ColumnDefinitions.Count - 1);
            PartRow.Children.Add(part);
        }
    }

    // ------------------------------------------------------------ the cards

    void BuildCards()
    {
        Cards.Children.Clear();
        CardsNote.IsVisible = false;
        _form.Forget();
        _runButtons.Clear();
        _resetButtons.Clear();
        CardScroll.Offset = default;
        PartRow.IsVisible = false;
        if (_tab is not { } section) return;

        var parts = PartsOf(section);
        var shown = PartOf(section, parts);
        BuildParts(section, parts, shown);

        // The cards of the section, block by block in the order the manifest first names each
        // block: an operation with a form is a card of its own; operations without fields that
        // share a block stand in one card. Of a section with parts - the cards of the part in sight.
        var plain = new Dictionary<string, WrapPanel>();
        foreach (var (host, guests) in section.Cards())
        {
            if (host.Children.Count > 0)
            {
                // A section inside a section: the manifest has none, and the window has no place for one.
                _nested.Add(host.Id);
                continue;
            }
            if (shown.Length > 0 && BlockKey(host) != shown) continue;
            // The row of parts already names the block in sight: its card does not say it again.
            bool named = shown.Length == 0;
            if (host.FormFields.Any()) { Cards.Children.Add(Card(host, guests, named)); continue; }
            var key = BlockKey(host);
            if (!plain.TryGetValue(key, out var row))
            {
                plain[key] = row = new WrapPanel { Orientation = Orientation.Horizontal };
                var body = new StackPanel();
                if (named) body.Children.Add(new TextBlock { Classes = { "grp", "cardname" }, Text = Upper(host.BlockName.Length > 0 ? host.BlockName : section.Name) });
                body.Children.Add(row);
                Cards.Children.Add(new Border { Classes = { "opcard" }, Child = body });
            }
            row.Children.Add(RunButton(host));
        }
        // Said once, not at every visit of the section.
        var unknown = _form.Unknown.Where(_unknownKinds.Add).ToList();
        var empty = _form.NoOptions.Where(_noOptions.Add).ToList();
        var signless = _form.NoSign.Concat(_noSign).Where(_noSignSaid.Add).ToList();
        _form.Unknown.Clear();
        _form.NoOptions.Clear();
        _form.NoSign.Clear();
        if (unknown.Count > 0) Journal.Add(Lang.F("[окно] поля неизвестного вида не показаны: {0}", string.Join("; ", unknown)));
        if (empty.Count > 0) Journal.Add(Lang.F("[окно] у поля нет вариантов: {0}", string.Join("; ", empty)));
        if (signless.Count > 0) Journal.Add(Lang.F("[окно] в наборе значков окна нет: {0}", string.Join("; ", signless)));
        ApplyBusy();
        ShowChanged();
    }

    /// <summary>
    /// The card of an operation with fields. Its head is one line: the name of its block on the
    /// left; at the right end "back to the defaults", then the buttons that run - the guests,
    /// and last, at the very edge, the card's own. Under the head, its form block by block.
    /// </summary>
    Control Card(Node node, List<Node> guests, bool named = true)
    {
        var fields = node.FormFields.ToList();
        var head = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto") };
        if (named && node.BlockName.Length > 0) head.Children.Add(new TextBlock { Classes = { "grp", "cardname", "inhead" }, Text = Upper(node.BlockName) });
        var buttons = new StackPanel { Orientation = Orientation.Horizontal };
        buttons.Children.Add(ResetButton(fields));
        // The guests stand to the left of the card's own button, in the order of the manifest.
        foreach (var guest in guests) buttons.Children.Add(RunButton(guest, ofForm: true));
        buttons.Children.Add(RunButton(node, ofForm: true));
        Grid.SetColumn(buttons, 1);
        head.Children.Add(buttons);

        var body = new StackPanel { Children = { head } };
        body.Children.Add(_form.Build(node));
        // What this card drew, for the self-check: a guest is counted against the card it shares.
        foreach (var operation in guests.Prepend(node)) _builtOf[operation.Id] = _form.Built;
        return new Border { Classes = { "opcard" }, Child = body };
    }

    /// <summary>
    /// The button that runs an operation. In a card that holds buttons alone it is the content of
    /// the card: its sign, its name and, under the name, what it does. In the head of a form it
    /// is the name alone on the accent, and what it does is in the tooltip - the form under it
    /// already says what the operation is about. A dangerous one - it changes what Windows
    /// trusts or what Defender skips - is Burn Orange and is asked about.
    /// </summary>
    Button RunButton(Node node, bool ofForm = false)
    {
        var button = new Button { Tag = node };
        if (ofForm)
        {
            button.Classes.Add("act");
            if (node.Dangerous) button.Classes.Add("actburn");
            button.Content = Upper(node.Name);
            // The sign is still looked up: a name the set does not have is to fail the self-check here too.
            SignOf(node);
        }
        else
        {
            button.Classes.Add(node.Dangerous ? "warn" : "run");
            button.Classes.Add("go");
            button.Content = Face(SignOf(node, "play"), node.Name, node.About);
        }
        Avalonia.Automation.AutomationProperties.SetAutomationId(button, "run_" + node.Id);
        // On a button without its line of words the tooltip carries them all: the short line, then the long one.
        ToolTip.SetTip(button, ofForm && node.About.Length > 0 && node.Tip.Length > 0 && node.Tip != node.About
            ? node.Name + "\n" + node.About + "\n\n" + node.Tip
            : TipOf(node));
        // What such a button does is said nowhere else: its tooltip does not keep one waiting.
        // Set here, after the tip: the shell gives every control with a tip its own delay at that
        // moment (App.axaml.cs), and a delay written in a style lost to it.
        if (ofForm) ToolTip.SetShowDelay(button, 400);
        button.Click += (_, _) => Press(node);
        _runButtons.Add(button);
        _withButton.Add(node.Id);
        return button;
    }

    /// <summary>"Back to the defaults": live only while some field of its card is not at its default.</summary>
    Button ResetButton(List<Field> fields)
    {
        var button = new Button { Classes = { "tool", "io" }, Content = Icons.Sign("undo-2"), VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(0, 0, 2, 0) };
        ToolTip.SetTip(button, Lang.T("К умолчаниям: вернуть настройки этой карточки к значениям манифеста."));
        button.Click += (_, _) => _values.Reset(fields);
        _resetButtons.Add((button, fields));
        return button;
    }

    /// <summary>After any value changed: which cards have something to give back.</summary>
    void ShowChanged()
    {
        foreach (var (button, fields) in _resetButtons)
            button.IsEnabled = fields.Any(_values.IsChanged);
    }

    /// <summary>The operation Ctrl+Enter runs: the one card with a form in sight; none when the section shows several or none.</summary>
    Node? CardOperation()
    {
        if (_tab is not { } section) return null;
        var shown = PartOf(section, PartsOf(section));
        var forms = section.Cards().Where(card => card.Host.Runs && card.Host.FormFields.Any() && (shown.Length == 0 || BlockKey(card.Host) == shown)).ToList();
        return forms.Count == 1 ? forms[0].Host : null;
    }
}
