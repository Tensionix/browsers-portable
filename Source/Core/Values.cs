using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace AudionBrowsersPortable.Core;

/// <summary>
/// The values of the fields, by the id of a field and not by the operation: the browsers ticked
/// for a build are the ones ticked for an update, and a command placed beside another reads the
/// fields of that one. So the window on NiceGUI kept them. They live as long as the window and
/// are not written to the disk - every start begins with the defaults of the manifest.
/// A value is kept as text: a flag as "true" or "false", a number without a tail of zeros, a
/// group of flags as its values joined by a line break.
/// </summary>
public sealed class Values
{
    readonly Dictionary<string, string> _held = new();

    /// <summary>A value was changed: the id of its field.</summary>
    public event Action<string>? Changed;

    public const char ListSeparator = '\n';

    /// <summary>The default of a field as the text this class keeps.</summary>
    public static string DefaultOf(Field field)
    {
        var value = field.Default;
        if (field.Kind == "checkboxes")
            return value.ValueKind == JsonValueKind.Array ? string.Join(ListSeparator, value.EnumerateArray().Select(FieldKinds.Text)) : "";
        return FieldKinds.Text(value);
    }

    public string Get(Field field) => _held.TryGetValue(field.Id, out var value) ? value : DefaultOf(field);

    public bool IsOn(Field field) => Get(field) == "true";

    public HashSet<string> Picked(Field field) =>
        Get(field).Split(ListSeparator, StringSplitOptions.RemoveEmptyEntries).ToHashSet();

    /// <summary>Not the default of the manifest: what was set for this document.</summary>
    public bool IsChanged(Field field) => Get(field) != DefaultOf(field);

    public void Set(Field field, string value)
    {
        if (Get(field) == value) return;
        // A value equal to the default is not held: another operation with the same field id and
        // another default keeps its own default.
        if (value == DefaultOf(field)) _held.Remove(field.Id);
        else _held[field.Id] = value;
        Changed?.Invoke(field.Id);
    }

    /// <summary>Back to the defaults of the manifest.</summary>
    public void Reset(IEnumerable<Field> fields)
    {
        foreach (var field in fields)
        {
            if (!_held.Remove(field.Id)) continue;
            Changed?.Invoke(field.Id);
        }
    }

    /// <summary>
    /// The values of an operation as the door reads them (--params): every field of its form by
    /// its id, in the type the field has - a flag, a number, a list, a text. An untouched field
    /// goes with the default of the manifest exactly as the manifest wrote it.
    /// </summary>
    public string ForDoor(Node node)
    {
        var payload = new JsonObject();
        foreach (var field in node.FormFields)
        {
            if (!IsChanged(field)) { payload[field.Id] = JsonNode.Parse(DefaultJson(field)); continue; }
            var text = Get(field);
            payload[field.Id] = field.Kind switch
            {
                "checkbox" => JsonValue.Create(text == "true"),
                "number" => Number(text),
                "checkboxes" => new JsonArray(text.Split(ListSeparator, StringSplitOptions.RemoveEmptyEntries).Select(item => (JsonNode?)JsonValue.Create(item)).ToArray()),
                // A choice goes as the manifest wrote its value: a number stays a number.
                "radio" or "select" => field.Options.FirstOrDefault(option => option.Key == text) is { } option
                    ? JsonNode.Parse(option.Value.GetRawText()) : JsonValue.Create(text),
                _ => JsonValue.Create(text),
            };
        }
        return payload.ToJsonString(new JsonSerializerOptions
        {
            WriteIndented = true,
            Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        });
    }

    static string DefaultJson(Field field) =>
        field.Default.ValueKind == JsonValueKind.Undefined ? (field.Kind == "checkboxes" ? "[]" : "\"\"") : field.Default.GetRawText();

    static JsonNode Number(string text)
    {
        // What is not a finite number goes as the text it is: JSON has no word for it, and the door refuses it by name.
        if (!double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out var number) || !double.IsFinite(number)) return JsonValue.Create(text);
        return number == Math.Floor(number) && Math.Abs(number) < 1e15 ? JsonValue.Create((long)number) : JsonValue.Create(number);
    }

    /// <summary>The fields of an operation that are not at their defaults, as "--set" would name them.</summary>
    public Dictionary<string, string> ChangedOf(Node node)
    {
        var changed = new Dictionary<string, string>();
        foreach (var field in node.FormFields)
            if (IsChanged(field)) changed[field.Id] = Get(field).Replace(ListSeparator, ',');
        return changed;
    }
}
