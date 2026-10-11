using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Text.RegularExpressions;
using Avalonia;
using Avalonia.Controls;
using Avalonia.LogicalTree;

namespace AudionBrowsersPortable.Core;

/// <summary>
/// RU / EN / DE. Russian is the source language: every text is written in
/// Russian and looked up in config\i18n.json at the surface - where it is
/// shown - instead of a key per string all over the code.
///
/// Texts with numbers and names inside ("ОБЪЕКТОВ: 12") are templates in the
/// table ("ОБЪЕКТОВ: {0}"); the parts caught by {n} are translated in turn,
/// so nested texts ("КОПИРОВАНИЕ: Big [1 из 2]: готово") come out whole.
/// </summary>
public static class Lang
{
    public static string Code { get; private set; } = "ru";

    public static event Action? Changed;

    sealed record Template(Regex Pattern, string En, string De);

    static readonly Dictionary<string, (string En, string De)> Exact = new();
    static readonly List<Template> Templates = new();
    static bool _loaded;

    public static void Set(string code)
    {
        Code = code is "en" or "de" ? code : "ru";
        Changed?.Invoke();
    }

    static void Load()
    {
        if (_loaded) return;
        _loaded = true;
        try
        {
            var file = Path.Combine(AppContext.BaseDirectory, "config", "i18n.json");
            if (!File.Exists(file)) return;
            using var doc = JsonDocument.Parse(File.ReadAllText(file));
            foreach (var entry in doc.RootElement.EnumerateObject())
            {
                var en = entry.Value.TryGetProperty("en", out var e) ? e.GetString() ?? entry.Name : entry.Name;
                var de = entry.Value.TryGetProperty("de", out var d) ? d.GetString() ?? en : en;
                if (Regex.IsMatch(entry.Name, @"\{\d+\}"))
                {
                    // Literal text around the holes. The first hole is greedy:
                    // it is usually a title that itself holds " · " or ":"
                    // ("КОПИРОВАНИЕ: Big · фильтр [1 из 2]"); the rest are lazy.
                    var parts = Regex.Split(entry.Name, @"\{\d+\}").Select(Regex.Escape).ToArray();
                    var pattern = "^" + parts[0];
                    for (int i = 1; i < parts.Length; i++) pattern += (i == 1 ? "(.+)" : "(.+?)") + parts[i];
                    pattern += "$";
                    Templates.Add(new Template(new Regex(pattern, RegexOptions.Singleline), en, de));
                }
                else Exact[entry.Name] = (en, de);
            }
            // Longer templates first: the most specific one wins.
            Templates.Sort((a, b) => b.Pattern.ToString().Length.CompareTo(a.Pattern.ToString().Length));
        }
        catch { }
    }

    /// <summary>The text in the current language; unknown text stays as it is.</summary>
    public static string T(string? text) => T(text, 0);

    static string T(string? text, int depth)
    {
        if (string.IsNullOrEmpty(text) || Code == "ru") return text ?? "";
        Load();
        if (Exact.TryGetValue(text, out var hit)) return Code == "de" ? hit.De : hit.En;
        if (depth < 4)
        {
            foreach (var template in Templates)
            {
                var m = template.Pattern.Match(text);
                if (!m.Success) continue;
                var args = m.Groups.Cast<Group>().Skip(1).Select(g => (object)T(g.Value, depth + 1)).ToArray();
                try { return string.Format(Code == "de" ? template.De : template.En, args); }
                catch { return text; }
            }
            // Several lines (tooltips): each line on its own.
            if (text.Contains('\n'))
                return string.Join("\n", text.Split('\n').Select(line => T(line, depth + 1)));
        }
        if (depth == 0 && Misses is { } file && text.Any(IsCyrillic))
            try { File.AppendAllText(file, text.Replace("\n", "⏎") + Environment.NewLine); } catch { }
        return text;
    }

    // Test aid: AUDION_I18N_MISSES=<file> collects every Russian text left untranslated.
    static readonly string? Misses = Environment.GetEnvironmentVariable("AUDION_I18N_MISSES");

    /// <summary>
    /// A text the manifest gives in both languages: the one of the window, the other when that one
    /// is empty - a field without an English label is shown in Russian, not as a blank.
    /// </summary>
    public static string Pick(string? ru, string? en)
    {
        ru ??= ""; en ??= "";
        return Code == "ru" ? (ru.Length > 0 ? ru : en) : (en.Length > 0 ? en : ru);
    }

    /// <summary>A Russian format string translated, then filled.</summary>
    public static string F(string format, params object[] args)
    {
        if (Code == "ru") return string.Format(format, args);
        var text = string.Format(T(format), args);
        // The way back: a text made in another language cannot be looked up in the table, and a
        // line that stays in sight - the status, the note of the left zone - has to follow the
        // language when it is switched to Russian again.
        lock (Made)
        {
            if (!Made.ContainsKey(text))
            {
                MadeOrder.Enqueue(text);
                if (MadeOrder.Count > MadeLimit) Made.Remove(MadeOrder.Dequeue());
            }
            Made[text] = (format, args);
        }
        return text;
    }

    /// <summary>What F made in a language other than Russian, by the text it gave: the last of them.</summary>
    static readonly Dictionary<string, (string Format, object[] Args)> Made = new();
    static readonly Queue<string> MadeOrder = new();
    const int MadeLimit = 512;

    /// <summary>The Russian of a text F made in another language; null for any other text.</summary>
    static string? Russian(string text)
    {
        (string Format, object[] Args) recipe;
        lock (Made)
            if (!Made.TryGetValue(text, out recipe)) return null;
        try { return string.Format(recipe.Format, recipe.Args); } catch { return null; }
    }

    // ------------------------------------------------------------ the walker

    // What each translated property was in Russian and what the walker put
    // there last: EN -> DE looks up the source, and text changed by code in
    // between becomes the new source.
    static readonly ConditionalWeakTable<AvaloniaObject, Dictionary<string, (string Source, string Set)>> Seen = new();

    static string? Translate(AvaloniaObject owner, string property, string current)
    {
        var map = Seen.GetOrCreateValue(owner);
        string source = map.TryGetValue(property, out var known) && known.Set == current ? known.Source : current;
        // A text F built in another language is taken back to its Russian: from there it goes
        // the way of every other text.
        if (Russian(source) is { } russian) source = russian;
        if (!map.ContainsKey(property) && !source.Any(IsCyrillic)) return null;
        var result = T(source);
        map[property] = (source, result);
        return result;
    }

    static bool IsCyrillic(char ch) => ch is >= 'А' and <= 'я' or 'Ё' or 'ё';

    /// <summary>
    /// A line that code rewrites all the time (status, filter chip): every
    /// new Russian text is translated as it lands.
    /// </summary>
    public static void Follow(TextBlock block)
    {
        block.PropertyChanged += (_, e) =>
        {
            if (e.Property != TextBlock.TextProperty || block.Text is not { } s) return;
            if (Translate(block, "Text", s) is { } t && t != s) block.Text = t;
        };
    }

    /// <summary>
    /// Translates what markup and code built: texts, button captions, menu
    /// items, placeholders, tooltips. Safe to run again after any rebuild and
    /// after a language switch. Rows of the file list and items of drop-down
    /// lists are data - file names - and are left alone.
    /// </summary>
    public static void Walk(ILogical root)
    {
        foreach (var node in root.GetSelfAndLogicalDescendants())
        {
            if (node is not Control c) continue;
            if (c.FindLogicalAncestorOfType<ComboBoxItem>() is not null || c is ComboBoxItem) continue;
            switch (c)
            {
                case TextBlock tb when tb.Text is { } s && Translate(tb, "Text", s) is { } ts:
                    if (ts != s) tb.Text = ts;
                    break;
                case MenuItem mi when mi.Header is string h && Translate(mi, "Header", h) is { } th:
                    if (th != h) mi.Header = th;
                    break;
                case ContentControl cc when cc.Content is string s2 && Translate(cc, "Content", s2) is { } ts2:
                    if (ts2 != s2) cc.Content = ts2;
                    break;
            }
            if (c is TextBox box && box.PlaceholderText is { } p && Translate(box, "Placeholder", p) is { } tp) box.PlaceholderText = tp;
            if (c is ComboBox combo && combo.PlaceholderText is { } q && Translate(combo, "Placeholder", q) is { } tq) combo.PlaceholderText = tq;
            if (ToolTip.GetTip(c) is string tip && Translate(c, "Tip", tip) is { } tt) ToolTip.SetTip(c, tt);
        }
    }
}