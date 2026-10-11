using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace AudionBrowsersPortable.Core;

/// <summary>
/// The manifest as the door tells it (browsers_cli.cmd describe): sections, operations and fields.
/// The window draws by it and knows no operation by name: a new field or operation of
/// config\tool_manifest.yaml appears in the window by itself, and a kind of field the window
/// does not know is said, not left as an empty place (FieldKinds.Known).
/// </summary>
public sealed class Manifest
{
    public List<Node> Nodes { get; set; } = new();
    public List<Node> Maintenance { get; set; } = new();
    public string Version { get; set; } = "";
    public ProjectPaths Paths { get; set; } = new();

    static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        PropertyNameCaseInsensitive = true,
        NumberHandling = JsonNumberHandling.AllowReadingFromString,
    };

    /// <summary>The answer of "describe" read; an answer that is no manifest throws.</summary>
    public static Manifest Parse(string json)
    {
        var manifest = JsonSerializer.Deserialize<Manifest>(json, Json) ?? throw new JsonException("the door gave no manifest");
        foreach (var node in manifest.Nodes) Bind(node, null);
        foreach (var node in manifest.Maintenance) Bind(node, null);
        return manifest;
    }

    static void Bind(Node node, Node? parent)
    {
        node.Parent = parent;
        foreach (var child in node.Children) Bind(child, node);
    }

    /// <summary>Every node of the sections, parents before children.</summary>
    public IEnumerable<Node> All() => Nodes.SelectMany(Walk);

    static IEnumerable<Node> Walk(Node node)
    {
        yield return node;
        foreach (var child in node.Children)
            foreach (var deeper in Walk(child))
                yield return deeper;
    }

    /// <summary>Everything that runs: the operations of the sections, then those of the maintenance.</summary>
    public IEnumerable<Node> Operations() => All().Concat(Maintenance).Where(node => node.Runs);

    public Node? Find(string id) => All().Concat(Maintenance).FirstOrDefault(node => node.Id == id);
}

/// <summary>The folders of the project, as the door resolves them.</summary>
public sealed class ProjectPaths
{
    public string Root { get; set; } = "";
    public string Input { get; set; } = "";
    public string Output { get; set; } = "";
    public string Logs { get; set; } = "";
    public string Report { get; set; } = "";
    public string Config { get; set; } = "";
    public string Workspace { get; set; } = "";
}

/// <summary>A section of the manifest or an operation; an operation is a node that runs.</summary>
public sealed class Node
{
    public string Id { get; set; } = "";
    public string Title { get; set; } = "";
    public string TitleRu { get; set; } = "";
    public string Description { get; set; } = "";
    public string DescriptionRu { get; set; } = "";
    public string Tooltip { get; set; } = "";
    public string TooltipRu { get; set; } = "";
    /// <summary>"safe" or "dangerous": a dangerous one is asked about.</summary>
    public string Kind { get; set; } = "";
    /// <summary>The sign on the button of the node: a name of Icons.cs; "" - none.</summary>
    public string Icon { get; set; } = "";
    /// <summary>The logical block of its section an operation stands in; operations of one block are shown together under its name.</summary>
    public string Block { get; set; } = "";
    public string BlockRu { get; set; } = "";
    /// <summary>
    /// The operation whose card this one shares: its button stands beside that operation's own and
    /// it runs on the fields of that card. "" - a card of its own.
    /// </summary>
    public string Beside { get; set; } = "";
    /// <summary>
    /// The window gives this operation no button of its own: the working folders do its work with
    /// the paths chosen there (CLEAR). It stays an operation of the door.
    /// </summary>
    public bool Workbench { get; set; }
    public bool Runs { get; set; }
    /// <summary>Runs, and has neither fields nor children: a button is all it needs.</summary>
    public bool Direct { get; set; }
    public List<Field> Fields { get; set; } = new();
    public List<Node> Children { get; set; } = new();

    [JsonIgnore] public Node? Parent { get; set; }

    [JsonIgnore] public string Name => Lang.Pick(TitleRu, Title);
    [JsonIgnore] public string About => Lang.Pick(DescriptionRu, Description);
    [JsonIgnore] public string Tip => Lang.Pick(TooltipRu, Tooltip);
    [JsonIgnore] public string BlockName => Lang.Pick(BlockRu, Block);
    [JsonIgnore] public bool Dangerous => Kind == "dangerous";

    /// <summary>The fields a person fills in.</summary>
    [JsonIgnore] public IEnumerable<Field> FormFields => Fields;

    /// <summary>
    /// The operations of a section as its cards: an operation the manifest places beside another
    /// joins the card of that one when that one is a card with fields in the same section.
    /// </summary>
    public List<(Node Host, List<Node> Guests)> Cards()
    {
        var hosts = Children.Where(child => child.Fields.Count > 0 && child.Children.Count == 0 && child.Beside.Length == 0)
            .Select(child => child.Id).ToHashSet();
        var cards = new List<(Node Host, List<Node> Guests)>();
        foreach (var child in Children.Where(child => !hosts.Contains(child.Beside)))
            cards.Add((child, Children.Where(guest => guest.Beside == child.Id && hosts.Contains(child.Id)).ToList()));
        return cards;
    }
}

public sealed class Field
{
    public string Id { get; set; } = "";
    /// <summary>checkbox, checkboxes, radio, select, number, text.</summary>
    public string Kind { get; set; } = "";
    public JsonElement Default { get; set; }
    /// <summary>The block of the form: source, options, format, output, run, parameters, encoding - or a name of the manifest's own.</summary>
    public string Group { get; set; } = "";
    /// <summary>Rarely needed: stands under the name "Advanced", after the everyday fields.</summary>
    public bool Advanced { get; set; }
    /// <summary>The field of the same block on whose line this one stands, as its control alone (ZIP or 7Z beside "Pack into an archive"); "" - a line of its own.</summary>
    public string Beside { get; set; } = "";
    public string Label { get; set; } = "";
    public string LabelRu { get; set; } = "";
    public string Hint { get; set; } = "";
    public string HintRu { get; set; } = "";
    public string Tooltip { get; set; } = "";
    public string TooltipRu { get; set; } = "";
    public double? Min { get; set; }
    public double? Max { get; set; }
    public double? Step { get; set; }
    public string Placeholder { get; set; } = "";
    /// <summary>The short line beside the control says nothing the control does not (ZIP or 7Z): the form does not write it, the tooltip keeps it.</summary>
    public bool Obvious { get; set; }
    public int MinSelected { get; set; }
    public List<Option> Options { get; set; } = new();
    /// <summary>Why the provider of the options gave none; "" when it did.</summary>
    public string OptionsError { get; set; } = "";

    [JsonIgnore] public string Name => Lang.Pick(LabelRu, Label);

    /// <summary>The short line the form writes beside the control: a few words of what the field is for; "" when it goes without saying.</summary>
    [JsonIgnore] public string Note => Obvious ? "" : Lang.Pick(HintRu, Hint);

    /// <summary>
    /// The tooltip: the short line the manifest keeps beside the control, then the full
    /// explanation - a caption is a few words, everything else is here.
    /// </summary>
    [JsonIgnore]
    public string Tip
    {
        get
        {
            var hint = Lang.Pick(HintRu, Hint);
            var more = Lang.Pick(TooltipRu, Tooltip);
            return hint.Length > 0 && more.Length > 0 ? hint + "\n\n" + more : hint.Length > 0 ? hint : more;
        }
    }

    /// <summary>The name of the block the field stands in, in the language of the window.</summary>
    [JsonIgnore]
    public string GroupName => Group switch
    {
        "source" => Lang.T("Источник"),
        "options" => Lang.T("Опции"),
        "format" => Lang.T("Формат"),
        "output" => Lang.T("Результат"),
        "run" => Lang.T("Запуск"),
        "encoding" => Lang.T("Кодирование"),
        "preset" => Lang.T("Профиль"),
        "parameters" or "" => Lang.T("Параметры"),
        _ => char.ToUpperInvariant(Group[0]) + Group[1..].Replace('_', ' '),
    };
}

public sealed class Option
{
    public JsonElement Value { get; set; }
    public string Label { get; set; } = "";
    public string LabelRu { get; set; } = "";
    /// <summary>The mark of the option - a browser's logo: a name of Icons.cs; "" - none.</summary>
    public string Icon { get; set; } = "";
    /// <summary>The colour of that logo, "#RRGGBB"; "" - the option wears the colours of the theme.</summary>
    public string Tone { get; set; } = "";

    [JsonIgnore] public string Name => Lang.Pick(LabelRu, Label);
    /// <summary>The value as the text the form keeps and compares.</summary>
    [JsonIgnore] public string Key => FieldKinds.Text(Value);
}

public static class FieldKinds
{
    /// <summary>The kinds of field the window can draw. Anything else fails the self-check of the window.</summary>
    public static readonly string[] Known = { "checkbox", "checkboxes", "radio", "select", "number", "text" };

    /// <summary>A value of the manifest as text: a number without a tail of zeros, a flag as true or false.</summary>
    public static string Text(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.String => value.GetString() ?? "",
        JsonValueKind.Number => value.GetDouble().ToString("0.############", System.Globalization.CultureInfo.InvariantCulture),
        JsonValueKind.True => "true",
        JsonValueKind.False => "false",
        JsonValueKind.Null or JsonValueKind.Undefined => "",
        _ => value.GetRawText(),
    };
}
