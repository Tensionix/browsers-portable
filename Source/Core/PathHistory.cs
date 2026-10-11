using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace AudionBrowsersPortable.Core;

/// <summary>One remembered path of the working folders.</summary>
public sealed class HistoryEntry
{
    public string Path { get; set; } = "";
    public int Count { get; set; } = 1;
    /// <summary>Local time, to the second: "2026-10-07T16:39:37". "" for an entry never used.</summary>
    public string LastUsed { get; set; } = "";
    public bool Pinned { get; set; }
}

/// <summary>
/// The history of sources and targets: config\path_history.json of the project. The file and its
/// rules are those of the window before this one (NiceGUI, removed on 11.10.2026; its
/// WorkbenchHistory), so what was pinned there is pinned here: pinned first, then the most used,
/// then the latest; one entry per path whatever its letter case; a hundred at most.
/// </summary>
public sealed class PathHistory
{
    public const int Limit = 100;

    readonly string _file;

    public PathHistory(string file) => _file = file;

    public static string Key(bool source) => source ? "sources" : "targets";

    // ------------------------------------------------------------ the file

    static Dictionary<string, List<HistoryEntry>> Empty() => new() { ["sources"] = new(), ["targets"] = new() };

    /// <summary>
    /// The file read; null when it is there and could not be opened - then nothing is written
    /// over it either. A file that opens and is not a history - cut short, edited by hand into
    /// something else - is not replaced in silence by an empty one, pins and all: it is set aside
    /// as "path_history.json.broken-DATE" and the journal says so.
    /// </summary>
    Dictionary<string, List<HistoryEntry>>? Load()
    {
        var data = Empty();
        string text;
        try
        {
            if (!File.Exists(_file)) return data;
            text = File.ReadAllText(_file);
        }
        catch { return null; }
        try
        {
            var root = JsonNode.Parse(text);
            // A file of the oldest kind is a plain list of sources.
            if (root is JsonArray plain) { data["sources"] = Normalize(plain); return data; }
            if (root is not JsonObject table) throw new JsonException("neither a list nor a table");
            data["sources"] = Normalize(table["sources"] ?? table["source"]);
            data["targets"] = Normalize(table["targets"] ?? table["target"]);
            return data;
        }
        catch (Exception ex)
        {
            var aside = $"{_file}.broken-{DateTime.Now:yyyyMMdd-HHmmss}";
            try { File.Move(_file, aside); }
            catch { return null; }
            Journal.Add(Lang.F("[окно] {0} испорчен ({1}); отложен как {2}, история начата с чистого",
                System.IO.Path.GetFileName(_file), ex.Message, System.IO.Path.GetFileName(aside)));
            return Empty();
        }
    }

    void Save(Dictionary<string, List<HistoryEntry>> data)
    {
        var root = new JsonObject();
        foreach (var key in new[] { "sources", "targets" })
        {
            var list = new JsonArray();
            foreach (var entry in data[key])
                list.Add(new JsonObject { ["path"] = entry.Path, ["count"] = entry.Count, ["last_used"] = entry.LastUsed, ["pinned"] = entry.Pinned });
            root[key] = list;
        }
        // Written as the former window wrote it: two spaces, Cyrillic as it is.
        var text = root.ToJsonString(new JsonSerializerOptions
        {
            WriteIndented = true, IndentSize = 2,
            Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        });
        try
        {
            Directory.CreateDirectory(System.IO.Path.GetDirectoryName(_file)!);
            var draft = _file + ".tmp";
            File.WriteAllText(draft, text, new UTF8Encoding(false));
            File.Move(draft, _file, overwrite: true);
        }
        catch (Exception ex)
        {
            Journal.Add(Lang.F("[окно] не сохранилось {0}: {1}", System.IO.Path.GetFileName(_file), ex.Message));
        }
    }

    static HistoryEntry? Coerce(JsonNode? item)
    {
        if (item is JsonValue value && value.TryGetValue<string>(out var plain))
            return plain.Trim().Length > 0 ? new HistoryEntry { Path = plain.Trim() } : null;
        if (item is not JsonObject row) return null;
        var path = (row["path"]?.ToString() ?? "").Trim();
        if (path.Length == 0) return null;
        int count = 1;
        try { count = Math.Max(1, int.Parse(row["count"]?.ToString() ?? "1")); } catch { }
        var pinned = (row["pinned"]?.ToString() ?? "").Trim().ToLowerInvariant() is "true" or "1" or "yes" or "on" or "pinned";
        return new HistoryEntry { Path = path, Count = count, LastUsed = (row["last_used"]?.ToString() ?? "").Trim(), Pinned = pinned };
    }

    static List<HistoryEntry> Normalize(JsonNode? entries)
    {
        var list = new List<HistoryEntry>();
        if (entries is JsonArray rows)
            foreach (var row in rows)
                if (Coerce(row) is { } entry) list.Add(entry);
        return Normalize(list);
    }

    /// <summary>One entry per path, counts added up, the order of the list.</summary>
    static List<HistoryEntry> Normalize(IEnumerable<HistoryEntry> entries)
    {
        var merged = new Dictionary<string, HistoryEntry>(StringComparer.OrdinalIgnoreCase);
        var order = new List<string>();
        foreach (var entry in entries)
        {
            if (!merged.TryGetValue(entry.Path, out var held))
            {
                merged[entry.Path] = entry;
                order.Add(entry.Path);
                continue;
            }
            held.Count += entry.Count;
            held.Pinned |= entry.Pinned;
            if (string.CompareOrdinal(entry.LastUsed, held.LastUsed) >= 0) held.Path = entry.Path;
            held.LastUsed = string.CompareOrdinal(entry.LastUsed, held.LastUsed) > 0 ? entry.LastUsed : held.LastUsed;
        }
        return order.Select(path => merged[path])
            .OrderByDescending(entry => entry.Pinned)
            .ThenByDescending(entry => entry.Count)
            .ThenByDescending(entry => entry.LastUsed, StringComparer.Ordinal)
            .Take(Limit).ToList();
    }

    static string Now => DateTime.Now.ToString("yyyy-MM-ddTHH:mm:ss");

    static string Clean(string path) => (path ?? "").Trim();

    // ------------------------------------------------------------ what the window asks

    public List<HistoryEntry> Entries(bool source) => (Load() ?? Empty())[Key(source)];

    public bool IsPinned(bool source, string path) =>
        Entries(source).Any(entry => entry.Pinned && string.Equals(entry.Path, Clean(path), StringComparison.OrdinalIgnoreCase));

    /// <summary>The file with input and output in it, when there is none yet.</summary>
    public void EnsureInitial(string input, string output)
    {
        if (Load() is not { } data) return;
        bool changed = !File.Exists(_file);
        if (data["sources"].Count == 0) { data["sources"].Add(new HistoryEntry { Path = input }); changed = true; }
        if (data["targets"].Count == 0) { data["targets"].Add(new HistoryEntry { Path = output }); changed = true; }
        if (changed) Save(data);
    }

    /// <summary>A path was chosen: it is counted, and it is the latest.</summary>
    public void Remember(bool source, string path)
    {
        path = Clean(path);
        if (path.Length == 0) return;
        if (Load() is not { } data) return;
        var list = data[Key(source)];
        if (list.FirstOrDefault(entry => string.Equals(entry.Path, path, StringComparison.OrdinalIgnoreCase)) is { } held)
        {
            held.Path = path;
            held.Count++;
            held.LastUsed = Now;
        }
        else list.Add(new HistoryEntry { Path = path, LastUsed = Now });
        data[Key(source)] = Normalize(list);
        Save(data);
    }

    public void SetPinned(bool source, string path, bool pinned)
    {
        path = Clean(path);
        if (path.Length == 0) return;
        if (Load() is not { } data) return;
        var list = data[Key(source)];
        if (list.FirstOrDefault(entry => string.Equals(entry.Path, path, StringComparison.OrdinalIgnoreCase)) is { } held)
        {
            held.Path = path;
            held.Pinned = pinned;
            held.LastUsed = Now;
        }
        else list.Add(new HistoryEntry { Path = path, LastUsed = Now, Pinned = pinned });
        data[Key(source)] = Normalize(list);
        Save(data);
    }

    /// <summary>A path leaves the history: its file was deleted.</summary>
    public void Forget(bool source, string path)
    {
        path = Clean(path);
        if (Load() is not { } data) return;
        data[Key(source)] = Normalize(data[Key(source)].Where(entry => !string.Equals(entry.Path, path, StringComparison.OrdinalIgnoreCase)));
        Save(data);
    }

    /// <summary>The history is emptied, the pins stay. Returns how many pins were kept.</summary>
    public int ClearKeepPins()
    {
        if (Load() is not { } data) return 0;
        int kept = 0;
        foreach (var key in new[] { "sources", "targets" })
        {
            data[key] = Normalize(data[key].Where(entry => entry.Pinned));
            kept += data[key].Count;
        }
        Save(data);
        return kept;
    }
}
