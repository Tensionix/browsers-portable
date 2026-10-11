using System;
using System.Globalization;
using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace AudionBrowsersPortable.Core;

/// <summary>
/// config\window.json: the look of the window and its sizes. The working folders are not kept:
/// every start begins with input and output of the project. The file is the state of one
/// machine - cleanup_project.cmd removes it.
/// </summary>
public sealed class Settings
{
    /// <summary>The theme, one of Audion Build Orchestrator's: "vault" (the default there too), "dark", "codedark", "light".</summary>
    public string Look { get; set; } = "vault";

    /// <summary>"" - none chosen yet. "ru" or "en" once the button was pressed.</summary>
    public string Lang { get; set; } = "";

    public double Width { get; set; }
    public double Height { get; set; }

    /// <summary>The width of the right column - the working folders and the journal; 0 - the default share.</summary>
    public double Side { get; set; }

    [JsonIgnore]
    public string LanguageInUse => LanguageFor(Lang, CultureInfo.CurrentUICulture);

    /// <summary>The language of the window: the chosen one; with none chosen, Russian when the system speaks it, English otherwise.</summary>
    public static string LanguageFor(string chosen, CultureInfo system) => chosen switch
    {
        "ru" => "ru",
        "en" => "en",
        _ => string.Equals(system.TwoLetterISOLanguageName, "ru", StringComparison.OrdinalIgnoreCase) ? "ru" : "en",
    };

    static readonly JsonSerializerOptions Json = new()
    {
        WriteIndented = true,
        PropertyNameCaseInsensitive = true,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    static string File_ => Path.Combine(Paths.Config, "window.json");

    public static Settings Load() => (SafeFile.Read<Settings>(File_, Json) ?? new Settings()).Clean();

    Settings Clean()
    {
        Look = Look is "vault" or "dark" or "codedark" or "light" ? Look : "vault";
        Lang ??= "";
        if (double.IsNaN(Side) || Side < 0) Side = 0;
        return this;
    }

    public void Save() => SafeFile.Write(File_, JsonSerializer.Serialize(this, Json));
}
