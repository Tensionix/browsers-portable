using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using AudionBrowsersPortable.Core;

namespace AudionBrowsersPortable;

/// <summary>
/// The self-check of the window (--self-check): the manifest is read through the door and every
/// section, every card and every form is built - without the window being shown - and what was
/// built is counted against the manifest. An operation without a button, a field without a
/// control, a field of a kind the form does not know or a choice with nothing to choose from
/// fails the check: an empty place in a form must be an error of a test, not a find of the owner's.
/// The result is a JSON file (logs\gui-self-check.json, or the file named after the key) and the
/// exit code: 0 - everything of the manifest is in the window, 1 - something is not, 2 - the
/// manifest could not be read.
/// </summary>
public partial class MainWindow
{
    /// <summary>The ids of the operations that got a button, as the cards are built.</summary>
    readonly HashSet<string> _withButton = new();
    /// <summary>Kinds of field the form could not draw, as "field: kind"; said in the journal once each.</summary>
    readonly HashSet<string> _unknownKinds = new();
    /// <summary>Choices with nothing to choose from, as "field: reason"; said in the journal once each.</summary>
    readonly HashSet<string> _noOptions = new();
    /// <summary>Sections found inside a section: the window has no place for them.</summary>
    readonly HashSet<string> _nested = new();
    /// <summary>Signs a node of the manifest names that the set of the window does not have, as "node: sign".</summary>
    readonly HashSet<string> _noSign = new();
    /// <summary>The missing signs - of nodes and of options - already said in the journal.</summary>
    readonly HashSet<string> _noSignSaid = new();
    /// <summary>The fields the card of an operation drew when it was built last, by the id of the operation.</summary>
    readonly Dictionary<string, HashSet<string>> _builtOf = new();

    public async Task<int> SelfCheck(string file)
    {
        // "version" is the number of the project (config\version.json), "window" the number this
        // window was built with. They part as the engine grows: the window is not rebuilt with it.
        var built = System.Reflection.CustomAttributeExtensions
            .GetCustomAttribute<System.Reflection.AssemblyInformationalVersionAttribute>(typeof(MainWindow).Assembly)?.InformationalVersion ?? "";
        var report = new Dictionary<string, object?> { ["version"] = Paths.Version, ["window"] = built, ["root"] = Paths.Root };
        int code;
        try
        {
            code = await SelfCheck(report);
        }
        catch (Exception ex)
        {
            report["ok"] = false;
            report["error"] = ex.ToString();
            code = 1;
        }
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(file))!);
            File.WriteAllText(file, JsonSerializer.Serialize(report, new JsonSerializerOptions
            {
                WriteIndented = true,
                Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
            }), new UTF8Encoding(false));
        }
        catch { code = code == 0 ? 1 : code; }
        return code;
    }

    async Task<int> SelfCheck(Dictionary<string, object?> report)
    {
        if (Door.Trouble is { Length: > 0 } trouble)
        {
            report["ok"] = false;
            report["error"] = trouble;
            return 2;
        }
        var (manifest, why) = await Door.Describe();
        if (manifest is null)
        {
            report["ok"] = false;
            report["error"] = why;
            return 2;
        }
        _manifest = manifest;
        BuildTabs();

        // Every section is opened in turn, and every part of a section that has parts: the cards
        // are built, and each card notes what it drew.
        var sections = manifest.Nodes.Where(node => !node.Direct).ToList();
        int parted = 0;
        foreach (var section in sections)
        {
            var parts = PartsOf(section);
            if (parts.Count == 0) { SelectTab(section); continue; }
            parted++;
            foreach (var (key, _) in parts)
            {
                _partOf[section.Id] = key;
                SelectTab(section);
            }
        }

        var operations = manifest.Operations().ToList();
        var expected = operations.Where(node => !node.Workbench).ToList();
        var missingOperations = expected.Where(node => !_withButton.Contains(node.Id)).Select(node => node.Id).ToList();
        var missingFields = new List<string>();
        var kinds = new SortedDictionary<string, int>();
        int fields = 0, fieldsBuilt = 0;
        foreach (var operation in expected)
        {
            _builtOf.TryGetValue(operation.Id, out var built);
            foreach (var field in operation.FormFields)
            {
                fields++;
                kinds[field.Kind] = kinds.GetValueOrDefault(field.Kind) + 1;
                if (built is not null && built.Contains(field.Id)) fieldsBuilt++;
                else missingFields.Add(operation.Id + "." + field.Id);
            }
        }
        // A section that holds nothing: no button would show it. (A guest whose host is not a card
        // of its section is not lost - it becomes a card of its own, Node.Cards - and an operation
        // left without a button is counted above.)
        var emptySections = sections.Where(node => node.Children.Count == 0).Select(node => node.Id).ToList();

        // A sign named by the manifest or by a registry of the engine and not in the set of the
        // window: the button would stand without its picture, and nobody would know why.
        var signless = _noSignSaid.OrderBy(item => item).ToList();
        // Every section and every operation with a button is to have a sign of its own.
        var faceless = manifest.Nodes.Where(node => !node.Direct).Concat(expected).Where(node => node.Icon.Length == 0).Select(node => node.Id).Distinct().ToList();

        bool ok = missingOperations.Count == 0 && missingFields.Count == 0 && _unknownKinds.Count == 0 && _noOptions.Count == 0
            && emptySections.Count == 0 && _nested.Count == 0 && signless.Count == 0 && faceless.Count == 0;
        report["ok"] = ok;
        report["sections"] = sections.Count;
        report["sections_with_parts"] = parted;
        report["operations"] = operations.Count;
        report["operations_with_button"] = expected.Count - missingOperations.Count;
        report["operations_of_the_workbench"] = operations.Where(node => node.Workbench).Select(node => node.Id).ToList();
        report["fields"] = fields;
        report["fields_built"] = fieldsBuilt;
        report["kinds"] = kinds;
        report["missing_operations"] = missingOperations;
        report["missing_fields"] = missingFields;
        report["unknown_kinds"] = _unknownKinds.OrderBy(item => item).ToList();
        report["choices_without_options"] = _noOptions.OrderBy(item => item).ToList();
        report["empty_sections"] = emptySections;
        report["nested_sections"] = _nested.OrderBy(item => item).ToList();
        report["signs_not_in_the_set"] = signless;
        report["nodes_without_a_sign"] = faceless;
        return ok ? 0 : 1;
    }
}
