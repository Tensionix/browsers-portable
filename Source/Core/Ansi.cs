using System;
using System.Collections.Generic;
using System.Text;
using Avalonia.Media;

namespace AudionBrowsersPortable.Core;

/// <summary>
/// How a piece of a line of the terminal is written: in a colour of its own - the one its writer
/// asked for - or, when it asked for none, in a colour of the theme by its name; bold; underlined.
/// </summary>
public readonly record struct Hue(string Key, Color? Own, bool Bold, bool Underline);

/// <summary>
/// The colours an operation and the programs it runs write their lines in, carried over as they
/// are - the window adds none of its own to a line of an operation. They come as the colour codes
/// of a console and are read here the way the window on NiceGUI read them (its reader lay in
/// system_core\core\ansi.py until 2.0.0): the sixteen colours of a console by the same table, the 256 by
/// their cube and their greys, a 24-bit colour by the nearest entry of the cube. A piece nobody
/// coloured is written in the colour of the journal's text - and the engine of today colours
/// nothing, so its lines are all of that one colour, as they were in the window before. A colour
/// opened on one line and closed on a later one is ordinary console output, so what is in force
/// outlives a line - until Reset. The reader of Audion Get Tools.
/// </summary>
public sealed class Ansi
{
    Color? _own;
    bool _bold, _dim, _underline;

    public void Reset()
    {
        _own = null;
        _bold = _dim = _underline = false;
    }

    bool Open => _own is not null || _bold || _dim || _underline;

    /// <summary>The pieces of a line, and the line as text alone.</summary>
    public List<(string Text, Hue Hue)> Read(string line, out string plain)
    {
        var parts = new List<(string, Hue)>();
        if (!Open && line.IndexOf('\x1b') < 0 && !HasControl(line))
        {
            plain = line;
            if (line.Length > 0) parts.Add((line, new Hue("LogPlain", null, false, false)));
            return parts;
        }

        var text = new StringBuilder();
        var whole = new StringBuilder();
        void Flush()
        {
            if (text.Length == 0) return;
            // Dimmed text is the same colour, fainter; with no colour asked for - the quiet one of the theme.
            Color? own = _own is { } colour && _dim ? Color.FromArgb(184, colour.R, colour.G, colour.B) : _own;
            parts.Add((text.ToString(), new Hue(_dim && own is null ? "LogMuted" : "LogPlain", own, _bold, _underline)));
            whole.Append(text);
            text.Clear();
        }
        for (int i = 0; i < line.Length;)
        {
            char ch = line[i];
            if (ch == '\x1b') { Flush(); i = Escape(line, i); continue; }
            if (!IsControl(ch)) text.Append(ch);
            i++;
        }
        Flush();
        plain = whole.ToString();
        return parts;
    }

    static bool IsControl(char ch) => ch != '\t' && (ch < ' ' || ch == 127 || (ch >= 128 && ch <= 159));

    static bool HasControl(string line)
    {
        foreach (var ch in line)
            if (IsControl(ch)) return true;
        return false;
    }

    /// <summary>An escape sequence is passed by; one that sets the look of the text is taken. Where the text goes on.</summary>
    int Escape(string line, int at)
    {
        if (at + 1 >= line.Length) return at + 1;
        char marker = line[at + 1];
        if (marker == '[')
        {
            int cursor = at + 2;
            while (cursor < line.Length && !(line[cursor] >= '@' && line[cursor] <= '~')) cursor++;
            if (cursor >= line.Length) return line.Length;
            if (line[cursor] == 'm') Apply(line.Substring(at + 2, cursor - at - 2));
            return cursor + 1;
        }
        if (marker == ']')
        {
            // The title of a console window and the like: up to its end mark.
            int cursor = at + 2;
            while (cursor < line.Length)
            {
                if (line[cursor] == '\a') return cursor + 1;
                if (line[cursor] == '\x1b' && cursor + 1 < line.Length && line[cursor + 1] == '\\') return cursor + 2;
                cursor++;
            }
            return line.Length;
        }
        return Math.Min(at + 2, line.Length);
    }

    void Apply(string payload)
    {
        var codes = new List<int>();
        foreach (var item in payload.Split(';'))
        {
            if (item.Length == 0) { codes.Add(0); continue; }
            if (!int.TryParse(item, out var code) || code < 0) return;
            codes.Add(code);
        }
        for (int at = 0; at < codes.Count;)
        {
            int code = codes[at];
            switch (code)
            {
                case 0: Reset(); break;
                case 1: _bold = true; _dim = false; break;
                case 2: _dim = true; _bold = false; break;
                case 4: _underline = true; break;
                case 22: _bold = _dim = false; break;
                case 24: _underline = false; break;
                case 39: _own = null; break;
                // Both forms of a colour past the sixteen are read to their end even when the colour is
                // not used - a background - or their numbers are read back as codes of their own.
                case 38: case 48: at = Extended(codes, at, code == 38); continue;
                default:
                    if (Sixteen.TryGetValue(code, out var colour)) _own = colour;
                    break;
            }
            at++;
        }
    }

    /// <summary>The sixteen colours of a console: the table the window on NiceGUI kept (SGR_FG of its ansi.py).</summary>
    static readonly Dictionary<int, Color> Sixteen = new()
    {
        [30] = Color.Parse("#111827"), [31] = Color.Parse("#f87171"), [32] = Color.Parse("#4ade80"), [33] = Color.Parse("#facc15"),
        [34] = Color.Parse("#60a5fa"), [35] = Color.Parse("#c084fc"), [36] = Color.Parse("#22d3ee"), [37] = Color.Parse("#e5e7eb"),
        [90] = Color.Parse("#6b7280"), [91] = Color.Parse("#fca5a5"), [92] = Color.Parse("#86efac"), [93] = Color.Parse("#fde047"),
        [94] = Color.Parse("#93c5fd"), [95] = Color.Parse("#d8b4fe"), [96] = Color.Parse("#67e8f9"), [97] = Color.Parse("#f9fafb"),
    };

    int Extended(List<int> codes, int at, bool text)
    {
        if (at + 1 >= codes.Count) return codes.Count;
        int mode = codes[at + 1];
        if (mode == 5 && at + 2 < codes.Count)
        {
            if (text) _own = Indexed(codes[at + 2]);
            return at + 3;
        }
        if (mode == 2 && at + 4 < codes.Count)
        {
            if (text)
            {
                // The nearest entry of the cube keeps the tone, as the window before did.
                static int Step(int value) => value < 48 ? 0 : Math.Min(5, (value - 35) / 40);
                _own = Indexed(16 + 36 * Step(codes[at + 2]) + 6 * Step(codes[at + 3]) + Step(codes[at + 4]));
            }
            return at + 5;
        }
        if (text) _own = null;
        return at + 2;
    }

    /// <summary>A colour of the 256: the first sixteen are the console's own, then a cube of 6x6x6, then 24 greys.</summary>
    static Color? Indexed(int index)
    {
        if (index < 0 || index > 255) return null;
        if (index < 16) return Sixteen[index < 8 ? 30 + index : 90 + index - 8];
        if (index < 232)
        {
            int offset = index - 16;
            static byte Level(int level) => (byte)(level == 0 ? 0 : 55 + 40 * level);
            return Color.FromRgb(Level(offset / 36), Level(offset / 6 % 6), Level(offset % 6));
        }
        byte grey = (byte)(8 + 10 * (index - 232));
        return Color.FromRgb(grey, grey, grey);
    }

    static readonly Dictionary<Color, IBrush> Own = new();

    /// <summary>The brush of a hue in the theme in use.</summary>
    public static IBrush Brush(Hue hue)
    {
        if (hue.Own is not { } colour) return App.Ink(hue.Key);
        if (!Own.TryGetValue(colour, out var brush)) Own[colour] = brush = new Avalonia.Media.Immutable.ImmutableSolidColorBrush(colour);
        return brush;
    }
}
