using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.Json;

namespace AudionBrowsersPortable.Core;

/// <summary>
/// Settings, history and caches are written whole or not at all, and a
/// failure is said, not swallowed (audit 18):
///   - the new text goes to "name.tmp" and replaces the file in one step;
///     the version it replaces stays as "name.bak";
///   - a file that cannot be read is not silently replaced by defaults: it is
///     set aside as "name.broken-DATE", the last good "name.bak" is taken
///     instead, and the program says so.
/// </summary>
public static class SafeFile
{
    static readonly UTF8Encoding Utf8 = new(false);

    /// <summary>What went wrong with a file: shown in the status line.</summary>
    public static event Action<string>? Problem;

    /// <summary>Problems met before the window could listen (reading at start).</summary>
    public static List<string> Pending { get; } = new();

    static void Report(string text)
    {
        Journal.Add("[НАСТРОЙКИ] " + text);
        if (Problem is { } listener) listener(text);
        else Pending.Add(text);
    }

    public static bool Write(string path, string text)
    {
        var temp = path + ".tmp";
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            File.WriteAllText(temp, text, Utf8);
            if (File.Exists(path)) File.Replace(temp, path, path + ".bak", ignoreMetadataErrors: true);
            else File.Move(temp, path);
            return true;
        }
        catch (Exception ex)
        {
            try { if (File.Exists(temp)) File.Delete(temp); } catch { }
            Report(Lang.F("не сохранилось {0}: {1}", Path.GetFileName(path), ex.Message));
            return false;
        }
    }

    /// <summary>The object in a JSON file, the last good copy when it is broken, or null when there is none.</summary>
    public static T? Read<T>(string path, JsonSerializerOptions options) where T : class
    {
        var backup = path + ".bak";
        if (!File.Exists(path))
        {
            // Put aside after a failed restore: the last good copy still counts.
            if (!File.Exists(backup)) return null;
            try { return JsonSerializer.Deserialize<T>(File.ReadAllText(backup), options); } catch { return null; }
        }
        try
        {
            return JsonSerializer.Deserialize<T>(File.ReadAllText(path), options);
        }
        catch (Exception ex)
        {
            var aside = $"{path}.broken-{DateTime.Now:yyyyMMdd-HHmmss}";
            try { File.Move(path, aside); } catch { }
            T? restored = null;
            try { if (File.Exists(backup)) restored = JsonSerializer.Deserialize<T>(File.ReadAllText(backup), options); } catch { }
            // The good copy becomes the file again, so every later read finds it
            // (audit recheck R3); the .bak itself stays as it was.
            if (restored is not null)
                try { File.Copy(backup, path, overwrite: false); } catch { }
            Report(restored is not null
                ? Lang.F("{0} испорчен ({1}); взята прошлая версия, испорченный отложен как {2}", Path.GetFileName(path), ex.Message, Path.GetFileName(aside))
                : Lang.F("{0} испорчен ({1}); отложен как {2}, начато с чистого", Path.GetFileName(path), ex.Message, Path.GetFileName(aside)));
            return restored;
        }
    }
}
