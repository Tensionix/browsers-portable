using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Input;
using Avalonia.Layout;
using Avalonia.Media;
using AudionBrowsersPortable.Core;
using Shapes = Avalonia.Controls.Shapes;

namespace AudionBrowsersPortable.Views;

/// <summary>
/// The form of an operation, drawn by the keys of the manifest, in the logical blocks the window
/// on NiceGUI had: Source, Options, Format, Output, Run. A block is a framed panel with its name;
/// inside it every field stands with its control and, under or beside it, the few words of the
/// manifest that say what it is for - except where that goes without saying (ZIP or 7Z). Blocks
/// stand two in a row; a block that holds the browsers or a link takes the whole row. The rarely
/// needed fields stand under the name "Advanced" and a rule - in sight, never folded. Choices are
/// rows of switches, never a drop-down list; a browser is a button with its logo and the colour
/// of that logo.
/// The form keeps nothing itself: every control reads and writes <see cref="Values"/> by the id
/// of its field, so the same field shown in two operations is one value.
/// </summary>
public sealed class Form
{
    readonly Values _values;

    /// <summary>What repaints the controls of a field, by the id of the field: a form built now, gone at the next build.</summary>
    readonly Dictionary<string, List<Action>> _cells = new();

    /// <summary>Kinds of field met while building that the form cannot draw.</summary>
    public List<string> Unknown { get; } = new();

    /// <summary>Fields whose provider gave no options, with the reason.</summary>
    public List<string> NoOptions { get; } = new();

    /// <summary>Options that name a sign the set of the window does not have, as "field=value: sign".</summary>
    public List<string> NoSign { get; } = new();

    /// <summary>The ids of the fields that got a control in the form built last: what the self-check counts, card by card.</summary>
    public HashSet<string> Built { get; private set; } = new();

    public Form(Values values)
    {
        _values = values;
        _values.Changed += id =>
        {
            if (!_cells.TryGetValue(id, out var repaint)) return;
            foreach (var action in repaint.ToArray()) action();
        };
    }

    /// <summary>A new build of the left zone begins: the controls of the last one are forgotten.</summary>
    public void Forget()
    {
        _cells.Clear();
        Built = new HashSet<string>();
    }

    /// <summary>The operation whose form is being built: the same field stands in several cards, and a control is named by both.</summary>
    string _card = "";

    /// <summary>
    /// A name a check of the built program finds a control by (UI Automation):
    /// field_OPERATION_FIELD, and field_OPERATION_FIELD_VALUE for one of a choice.
    /// </summary>
    T Named<T>(T control, Field field, string? value = null) where T : Control
    {
        Avalonia.Automation.AutomationProperties.SetAutomationId(control, $"field_{_card}_{field.Id}" + (value is null ? "" : "_" + value));
        return control;
    }

    void Watch(Field field, Action repaint)
    {
        if (!_cells.TryGetValue(field.Id, out var list)) _cells[field.Id] = list = new List<Action>();
        list.Add(repaint);
        repaint();
    }

    static string Upper(string text) => text.ToUpper(CultureInfo.CurrentCulture);

    // ------------------------------------------------------------ blocks

    /// <summary>
    /// The form of an operation: its everyday fields in their blocks, then the name "Advanced"
    /// with a rule across the form and the rarely needed fields under it. Nothing is folded: the
    /// window before this one hid those fields behind a press, and so did this one at first; the
    /// owner had them unfolded for good and the name with the rule kept (10.10.2026).
    /// </summary>
    public Control Build(Node node)
    {
        Built = new HashSet<string>();
        _card = node.Id;
        var host = new StackPanel { Margin = new Thickness(0, 4, 0, 0) };
        var fields = node.FormFields.ToList();
        var everyday = fields.Where(field => !field.Advanced).ToList();
        var advanced = fields.Where(field => field.Advanced).ToList();
        host.Children.Add(Blocks(everyday));
        if (advanced.Count == 0) return host;

        var title = new TextBlock { Classes = { "grouptitle" }, Text = Upper(Lang.T("Дополнительно")), VerticalAlignment = VerticalAlignment.Center };
        var rule = new Border { Classes = { "frule" } };
        Grid.SetColumn(rule, 1);
        var line = new Grid { Classes = { "fpart" }, ColumnDefinitions = new ColumnDefinitions("Auto,*"), Children = { title, rule } };
        ToolTip.SetTip(title, Lang.T("Редко нужные поля: без них операция работает как обычно."));
        host.Children.Add(line);
        host.Children.Add(Blocks(advanced));
        return host;
    }

    /// <summary>
    /// The fields by their blocks, in the order the manifest first names each block. A block with
    /// a group of flags or a text - the browsers, a link - takes the whole width. The others
    /// stand in two columns, each going into the shorter one, so a block of four flags has the
    /// two blocks of choices beside it and no flag is stretched into a banner across the form.
    /// A field the manifest places beside another of its block (`beside`) gets no line of its
    /// own: it stands at the right end of the line of that field.
    /// </summary>
    Control Blocks(List<Field> fields)
    {
        var host = new StackPanel();
        StackPanel? left = null, right = null;
        double leftHeight = 0, rightHeight = 0;
        foreach (var group in fields.GroupBy(field => field.Group))
        {
            var known = new List<Field>();
            foreach (var field in group)
            {
                if (FieldKinds.Known.Contains(field.Kind)) known.Add(field);
                else Unknown.Add($"{field.Id}: {field.Kind}");
            }
            if (known.Count == 0) continue;
            // A guest whose host is not in this block - under "Advanced", say, or not drawn - stands on a line of its own.
            var hosts = known.Where(field => field.Beside.Length == 0).Select(field => field.Id).ToHashSet();
            var guests = known.Where(field => hosts.Contains(field.Beside)).ToLookup(field => field.Beside);
            known = known.Where(field => !hosts.Contains(field.Beside)).ToList();
            var body = new StackPanel();
            // The name of the block goes onto the line of its first field when that field has
            // such a line; over a flag, or a field with guests, it keeps a line of its own.
            var name = Upper(group.First().GroupName);
            bool onLine = Heads(known[0]) && !guests[known[0].Id].Any();
            if (!onLine) body.Children.Add(new TextBlock { Classes = { "grp" }, Text = name });
            for (int i = 0; i < known.Count; i++)
            {
                var field = known[i];
                Built.Add(field.Id);
                foreach (var guest in guests[field.Id]) Built.Add(guest.Id);
                var cell = Cell(field, guests[field.Id].ToList(), i == 0 && onLine ? name : "");
                // Two texts in a row share a line - the two links of the installers: one under
                // the other they cost the form a row it does not have.
                if (field.Kind == "text" && i + 1 < known.Count && known[i + 1].Kind == "text"
                    && !guests[field.Id].Any() && !guests[known[i + 1].Id].Any())
                {
                    var next = known[++i];
                    Built.Add(next.Id);
                    var second = Cell(next, new List<Field>());
                    Grid.SetColumn(second, 2);
                    // No gap of its own between the two: the gutter of the second is the gap.
                    body.Children.Add(new Grid { ColumnDefinitions = new ColumnDefinitions("*,2,*"), Children = { cell, second } });
                    continue;
                }
                body.Children.Add(cell);
            }
            var frame = new Border { Classes = { "grp", "fblock" }, Child = body };
            if (known.Any(field => field.Kind is "checkboxes" or "text"))
            {
                // The whole width; the columns begin anew under it.
                left = right = null;
                leftHeight = rightHeight = 0;
                host.Children.Add(frame);
                continue;
            }
            if (left is null || right is null)
            {
                var columns = new Grid { ColumnDefinitions = new ColumnDefinitions("*,8,*") };
                left = new StackPanel();
                right = new StackPanel();
                Grid.SetColumn(right, 2);
                columns.Children.Add(left);
                columns.Children.Add(right);
                host.Children.Add(columns);
            }
            // How tall the block will be, near enough to tell the shorter column: its frame and
            // its name (on a line of its own or not), then its fields - as measured on the window.
            double height = (onLine ? 10 : 33) + known.Sum(field => field.Kind switch
            {
                "checkbox" => field.Note.Length > 0 ? 53 : 38,
                "number" => field.Note.Length > 0 ? 52 : 38,
                _ => 57,
            });
            if (leftHeight <= rightHeight) { left.Children.Add(frame); leftHeight += height; }
            else { right.Children.Add(frame); rightHeight += height; }
        }
        return host;
    }

    // ------------------------------------------------------------ cells

    Shapes.Ellipse Mark(Field field)
    {
        var mark = new Shapes.Ellipse { Classes = { "changed" } };
        ToolTip.SetTip(mark, Lang.T("Значение отличается от умолчания манифеста."));
        Watch(field, () => mark.Opacity = _values.IsChanged(field) ? 1 : 0);
        return mark;
    }

    static TextBlock Label(Field field)
    {
        var label = new TextBlock { Classes = { "flabel" }, Text = field.Name };
        if (field.Tip.Length > 0) ToolTip.SetTip(label, field.Tip);
        return label;
    }

    /// <summary>The few words of the manifest under a control; null when the field has none or they go without saying.</summary>
    static TextBlock? Note(Field field) =>
        field.Note.Length == 0 ? null : new TextBlock { Classes = { "fnote" }, Text = field.Note };

    /// <summary>
    /// One field of a block: the mark of a changed value in the gutter on the left, then the
    /// field - a flag with its words inside the switch; anything else as its name, its control
    /// and its words under them. Its guests - the fields the manifest places beside it - stand
    /// at the right end of the same line, each as its control alone: "Pack into an archive" and,
    /// on that line, ZIP or 7Z.
    /// </summary>
    Control Cell(Field field, List<Field> guests, string block = "")
    {
        // A field with guests keeps the line to itself: the name of its block stands over it.
        var cell = Cell(field, guests.Count == 0 ? block : "");
        if (guests.Count == 0) return cell;
        var body = cell.Children[1];
        cell.Children.Remove(body);
        var line = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto") };
        Grid.SetColumn(body, 0);
        line.Children.Add(body);
        var beside = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
        foreach (var guest in guests) beside.Children.Add(Guest(guest));
        Grid.SetColumn(beside, 1);
        line.Children.Add(beside);
        Grid.SetColumn(line, 1);
        cell.Children.Add(line);
        return cell;
    }

    /// <summary>
    /// A field on the line of another: the mark of a changed value, then the control without its
    /// name - the field it stands beside says what it is of. The name is the first line of the
    /// tooltip.
    /// </summary>
    Control Guest(Field field)
    {
        var mark = Mark(field);
        mark.VerticalAlignment = VerticalAlignment.Center;
        mark.Margin = new Thickness(9, 0, 0, 0);
        var controls = field.Kind switch
        {
            "checkbox" => new List<Control> { Flag(field) },
            "checkboxes" => new List<Control> { FlagRow(field).Row },
            "text" => new List<Control> { TextBox(field) },
            "number" => new List<Control> { NumberBox(field) },
            _ => Choice(field),
        };
        var tip = field.Tip.Length > 0 ? field.Name + "\n\n" + field.Tip : field.Name;
        var row = new StackPanel { Orientation = Orientation.Horizontal, Classes = { "fguest" }, Children = { mark } };
        foreach (var control in controls)
        {
            ToolTip.SetTip(control, tip);
            row.Children.Add(control);
        }
        return row;
    }

    Grid Cell(Field field, string block = "")
    {
        var cell = new Grid { ColumnDefinitions = new ColumnDefinitions("10,*"), Classes = { "fcell" } };
        if (block.Length > 0)
        {
            // The first field of a block: its line, with the name of the block in front, runs
            // from the edge of the block; the control stands under it, past the gutter.
            var (control, all) = field.Kind switch
            {
                "checkboxes" => FlagRow(field),
                "text" => ((Control)TextBox(field), (Control?)null),
                _ => ((Control)Row(Choice(field)), (Control?)null),
            };
            cell.RowDefinitions = new RowDefinitions("Auto,Auto");
            var head = Head(field, all, block, Mark(field));
            Grid.SetColumnSpan(head, 2);
            cell.Children.Add(head);
            Grid.SetRow(control, 1);
            Grid.SetColumn(control, 1);
            cell.Children.Add(control);
            return cell;
        }
        var mark = Mark(field);
        mark.VerticalAlignment = VerticalAlignment.Top;
        mark.Margin = new Thickness(0, 10, 0, 0);
        cell.Children.Add(mark);
        Control body;
        switch (field.Kind)
        {
            case "checkbox":
                body = Flag(field);
                break;
            case "checkboxes":
                var (row, tools) = FlagRow(field);
                body = Titled(field, row, above: true, tools);
                break;
            case "text":
                body = Titled(field, TextBox(field), above: true);
                break;
            case "number":
                body = Titled(field, NumberBox(field), above: false);
                break;
            default:
                // The name over the switches, always: beside them it fitted one choice and wrapped the next.
                body = Titled(field, Row(Choice(field)), above: true);
                break;
        }
        Grid.SetColumn(body, 1);
        cell.Children.Add(body);
        return cell;
    }

    /// <summary>
    /// A field with its name. Over the control: the name and, on the same line, the few words of
    /// the manifest that say what the field is for - a line of its own for them would cost every
    /// field a row of the form. Beside the control (a number): the name, the control, and the
    /// words under them.
    /// </summary>
    Control Titled(Field field, Control control, bool above, Control? tools = null)
    {
        var column = new StackPanel();
        if (above)
        {
            column.Children.Add(Head(field, tools));
            column.Children.Add(control);
            return column;
        }
        var label = Label(field);
        var note = Note(field);
        var line = new WrapPanel { Orientation = Orientation.Horizontal };
        label.Height = 24;
        label.Padding = new Thickness(0, 5, 0, 0);
        line.Children.Add(label);
        line.Children.Add(control);
        column.Children.Add(line);
        if (note is not null) column.Children.Add(note);
        return column;
    }

    /// <summary>
    /// The line over a control: the name of the field, the few words of the manifest, and at the
    /// right end what acts on the whole field - all of the browsers, none. For the first field
    /// of a block the line begins with the name of the block and a rule: a line of its own for
    /// that name cost every block a row of the form (the owner, 10.10.2026: "why are the name of
    /// a block and its description not on one line, through a divider? We would save height").
    /// The mark of a changed value then stands on this line too, before the name of the field.
    /// </summary>
    static Grid Head(Field field, Control? tools, string block = "", Control? mark = null)
    {
        var head = new Grid { ColumnDefinitions = new ColumnDefinitions("Auto,Auto,Auto,Auto,*,Auto"), Margin = new Thickness(0, 0, 0, 6) };
        if (block.Length > 0)
        {
            head.Children.Add(new TextBlock { Classes = { "grp", "inhead" }, Text = block });
            var rule = new Border { Classes = { "fsep" } };
            Grid.SetColumn(rule, 1);
            head.Children.Add(rule);
            if (mark is not null)
            {
                mark.Classes.Add("inhead");
                Grid.SetColumn(mark, 2);
                head.Children.Add(mark);
            }
        }
        var label = Label(field);
        label.Margin = new Thickness(0, 0, 9, 0);
        Grid.SetColumn(label, 3);
        head.Children.Add(label);
        if (Note(field) is { } note)
        {
            note.Classes.Add("inline");
            if (field.Tip.Length > 0) ToolTip.SetTip(note, field.Tip);
            Grid.SetColumn(note, 4);
            head.Children.Add(note);
        }
        if (tools is not null)
        {
            Grid.SetColumn(tools, 5);
            head.Children.Add(tools);
        }
        return head;
    }

    /// <summary>A field whose name stands on a line over its control - the line the name of a block can share.</summary>
    static bool Heads(Field field) => field.Kind is not ("checkbox" or "number");

    static Panel Row(IEnumerable<Control> controls)
    {
        var row = new WrapPanel { Orientation = Orientation.Horizontal };
        foreach (var control in controls) row.Children.Add(control);
        return row;
    }

    // ------------------------------------------------------------ controls

    static Border TickBox() => new()
    {
        Classes = { "tickbox" },
        Child = new Shapes.Path { Classes = { "tick" }, Data = Geometry.Parse("M 1.5,5 L 4,7.5 L 8.5,2.5") },
    };

    /// <summary>
    /// A flag as a card: the tick box, the name, and under the name the few words that say what
    /// the flag is for. As wide as its block, so the flags of a block stand in one column.
    /// </summary>
    ToggleButton Flag(Field field)
    {
        var words = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
        words.Children.Add(new TextBlock { Classes = { "flagname" }, Text = field.Name });
        if (field.Note.Length > 0) words.Children.Add(new TextBlock { Classes = { "flagnote" }, Text = field.Note });
        var box = TickBox();
        box.VerticalAlignment = VerticalAlignment.Top;
        box.Margin = new Thickness(0, 2, 0, 0);
        var face = new Grid { ColumnDefinitions = new ColumnDefinitions("Auto,8,*") };
        face.Children.Add(box);
        Grid.SetColumn(words, 2);
        face.Children.Add(words);
        var flag = Named(new ToggleButton { Classes = { "switch", "flagcard" }, Content = face }, field);
        if (field.Tip.Length > 0) ToolTip.SetTip(flag, field.Tip);
        flag.Click += (_, _) => _values.Set(field, flag.IsChecked == true ? "true" : "false");
        Watch(field, () => flag.IsChecked = _values.IsOn(field));
        return flag;
    }

    /// <summary>A choice of a few: a row of switches, one of them on.</summary>
    List<Control> Choice(Field field)
    {
        var buttons = new List<(ToggleButton Button, string Key)>();
        foreach (var option in field.Options)
        {
            var button = Named(new ToggleButton { Classes = { "switch" }, Content = Face(field, option) }, field, option.Key);
            Paint(button, option);
            if (field.Tip.Length > 0) ToolTip.SetTip(button, field.Tip);
            var key = option.Key;
            button.Click += (_, _) =>
            {
                _values.Set(field, key);
                // A press on the chosen one must not switch it off: one of the row is always on.
                button.IsChecked = _values.Get(field) == key;
            };
            buttons.Add((button, key));
        }
        Watch(field, () =>
        {
            var current = _values.Get(field);
            foreach (var (button, key) in buttons) button.IsChecked = key == current;
        });
        var row = buttons.Select(item => (Control)item.Button).ToList();
        if (row.Count == 0) row.Add(NoOptionsNote(field));
        return row;
    }

    /// <summary>
    /// A group of flags - the browsers: each a button with its mark, on or off; the value is the
    /// list of those that are on, in the order of the manifest. A button is as wide as its name
    /// and goes to the next line whole. With it come two small tools for the line of the field's
    /// name: all of them at a press, and none.
    /// </summary>
    (Control Row, Control? Tools) FlagRow(Field field)
    {
        var row = new WrapPanel { Orientation = Orientation.Horizontal };
        var flags = new List<(ToggleButton Flag, string Key)>();
        foreach (var option in field.Options)
        {
            var flag = Named(new ToggleButton { Classes = { "switch", "logo" }, Content = Face(field, option) }, field, option.Key);
            Paint(flag, option);
            if (field.Tip.Length > 0) ToolTip.SetTip(flag, field.Tip);
            flags.Add((flag, option.Key));
            row.Children.Add(flag);
        }
        void Take() => _values.Set(field, string.Join(Values.ListSeparator, flags.Where(item => item.Flag.IsChecked == true).Select(item => item.Key)));
        foreach (var (flag, _) in flags) flag.Click += (_, _) => Take();
        Watch(field, () =>
        {
            var picked = _values.Picked(field);
            foreach (var (flag, key) in flags) flag.IsChecked = picked.Contains(key);
        });
        if (flags.Count == 0) { row.Children.Add(NoOptionsNote(field)); return (row, null); }
        if (flags.Count <= 2) return (row, null);
        Button Tool(string sign, string tip, string id, bool on)
        {
            var tool = Named(new Button { Classes = { "tool", "io" }, Content = Icons.Sign(sign) }, field, id);
            ToolTip.SetTip(tool, Lang.T(tip));
            tool.Click += (_, _) => _values.Set(field, on ? string.Join(Values.ListSeparator, flags.Select(item => item.Key)) : "");
            return tool;
        }
        var tools = new StackPanel
        {
            Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center,
            Children = { Tool("check-check", "Отметить все.", "all", true), Tool("list-x", "Снять все отметки.", "none", false) },
        };
        return (row, tools);
    }

    /// <summary>What a button of a choice shows: the mark of the option, when it has one, and its name.</summary>
    Control Face(Field field, Option option)
    {
        if (option.Icon.Length == 0) return new TextBlock { Text = option.Name, VerticalAlignment = VerticalAlignment.Center };
        var ink = Color.TryParse(option.Tone, out var tone) ? new SolidColorBrush(tone) : null;
        // A logo is read by its shape: a size up from the signs of the buttons around.
        if (Icons.Face(option.Icon, ink, 14) is not { } sign)
        {
            NoSign.Add($"{field.Id}={option.Key}: {option.Icon}");
            return new TextBlock { Text = option.Name, VerticalAlignment = VerticalAlignment.Center };
        }
        return new StackPanel
        {
            Orientation = Orientation.Horizontal, Spacing = 7,
            Children = { sign, new TextBlock { Text = option.Name, VerticalAlignment = VerticalAlignment.Center } },
        };
    }

    /// <summary>
    /// An option with a colour of its own wears it: the rim of its button in that colour, a wash
    /// of it when the button is on. The colour is data - the logo of a browser, named by the
    /// registry of the engine - and reaches the styles as two resources of the button itself.
    /// </summary>
    static void Paint(ToggleButton button, Option option)
    {
        if (!Color.TryParse(option.Tone, out var tone)) return;
        button.Classes.Add("brand");
        // Both are rims - of the button that is on or under the pointer, and of the one that is
        // off - and are pressed down with every other rim of the window (ButtonTints.cs). With
        // the rim quiet, the wash under the name is what says the button is on.
        button.Resources["BrandInk"] = new SolidColorBrush(tone, ButtonTints.RimShare);
        button.Resources["BrandRim"] = new SolidColorBrush(tone, 0.55 * ButtonTints.RimShare);
        button.Resources["BrandWash"] = new SolidColorBrush(tone, ButtonTints.BrandWash);
    }

    /// <summary>A choice with nothing to choose from: the reason stands where the buttons would.</summary>
    TextBlock NoOptionsNote(Field field)
    {
        var why = field.OptionsError.Length > 0 ? field.OptionsError : Lang.T("вариантов нет");
        NoOptions.Add($"{field.Id}: {why}");
        return new TextBlock { Classes = { "note", "warn" }, Text = why, VerticalAlignment = VerticalAlignment.Center, TextWrapping = TextWrapping.Wrap };
    }

    TextBox TextBox(Field field)
    {
        var box = Named(new TextBox { Classes = { "field" }, PlaceholderText = field.Placeholder }, field);
        if (field.Tip.Length > 0) ToolTip.SetTip(box, field.Tip);
        box.TextChanged += (_, _) => _values.Set(field, box.Text ?? "");
        // The box a person types in is not rewritten under the cursor: only a value that came from elsewhere is put in.
        Watch(field, () => { if ((box.Text ?? "") != _values.Get(field)) box.Text = _values.Get(field); });
        return box;
    }

    /// <summary>
    /// A number: typed, or stepped with the arrow keys; kept inside min and max of the manifest.
    /// What is not a number is not taken - the box goes back to the value it had.
    /// </summary>
    TextBox NumberBox(Field field)
    {
        var box = Named(new TextBox { Classes = { "field", "number" } }, field);
        if (field.Tip.Length > 0) ToolTip.SetTip(box, field.Tip);
        double step = field.Step is { } given && given > 0 ? given : 1;

        void Take()
        {
            var typed = (box.Text ?? "").Trim().Replace(',', '.');
            // "NaN", "Infinity" and "1e999" are read as numbers too, and are none a field can hold.
            if (double.TryParse(typed, NumberStyles.Float, CultureInfo.InvariantCulture, out var number) && double.IsFinite(number)) _values.Set(field, Fit(field, number));
            box.Text = _values.Get(field);
        }
        void Move(int direction)
        {
            if (!double.TryParse(_values.Get(field), NumberStyles.Float, CultureInfo.InvariantCulture, out var number) || !double.IsFinite(number)) number = 0;
            // Rounded to the step's own digits: 0.1 added ten times is 1, not 0.9999999999999999.
            _values.Set(field, Fit(field, Math.Round(number + direction * step, 6)));
        }
        box.LostFocus += (_, _) => Take();
        box.KeyDown += (_, e) =>
        {
            if (e.Key == Key.Enter) { Take(); e.Handled = true; }
            else if (e.Key == Key.Up) { Take(); Move(1); e.Handled = true; }
            else if (e.Key == Key.Down) { Take(); Move(-1); e.Handled = true; }
        };
        Watch(field, () => { if (!box.IsFocused || (box.Text ?? "") != _values.Get(field)) box.Text = _values.Get(field); });
        return box;
    }

    static string Fit(Field field, double number)
    {
        if (field.Min is { } min && number < min) number = min;
        if (field.Max is { } max && number > max) number = max;
        return number.ToString("0.######", CultureInfo.InvariantCulture);
    }
}
