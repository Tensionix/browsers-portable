using System.Collections.Generic;
using System.Threading.Tasks;
using Avalonia.Controls;
using Avalonia.Media;

namespace AudionBrowsersPortable.Views;

public enum Tone { Plain, Run, Warn, Danger }

/// <summary>
/// A window that may be out of sight - the main window hidden in the tray - and knows how to come back. A dialog
/// cannot open over an owner nobody sees (Avalonia throws), and what asks for the window from another one
/// (DIFF, the search, an operation that ended) must find it on the screen (audit 5.38.0, ADT-538-01 and -02).
/// </summary>
public interface IReveal
{
    void Reveal();
}

/// <summary>
/// The confirmation window of Sync Tools. Returns null on
/// cancel; otherwise the typed value, or "" when there is no field.
/// </summary>
public partial class Dialog : Window
{
#if DEBUG
    public static string? DevAnswer { get; set; }
#endif
    string? _result;
    bool _secret;

    public Dialog()
    {
        InitializeComponent();
        CancelButton.Click += (_, _) => Close();
        OkButton.Click += (_, _) =>
        {
            // A password is taken as typed: a space at its end is part of it.
            _result = !Input.IsVisible ? "" : _secret ? Input.Text ?? "" : (Input.Text ?? "").Trim();
            Close();
        };
    }

    public static async Task<string?> Ask(Window owner, string head, IEnumerable<string> lines,
                                          string okText = "ДА", Tone tone = Tone.Plain, string? value = null, string? badge = null, bool secret = false)
    {
#if DEBUG
        // A check without a mouse (MainWindow.Dev): the next question is answered by the script.
        if (DevAnswer is { } answers)
        {
            // "yes,yes" answers two questions in a row: the second comes before the script can say more.
            var parts = answers.Split(',', 2);
            var answer = parts[0];
            DevAnswer = parts.Length > 1 ? parts[1] : null;
            Core.Journal.Add($"[dev] {Core.Lang.T(head)} -> {answer}");
            return answer == "no" ? null : answer == "yes" ? value ?? "" : answer;
        }
#endif
        var dialog = new Dialog { _secret = secret };
        if (secret) dialog.Input.PasswordChar = '•';
        dialog.Head.Text = Core.Lang.T(head);
        foreach (var line in lines)
        {
            dialog.Lines.Children.Add(new TextBlock
            {
                Text = line.Length == 0 ? " " : Core.Lang.T(line),
                TextWrapping = TextWrapping.Wrap,
                Classes = { line.StartsWith("  ") ? "caption" : "plain" },
            });
        }
        dialog.OkButton.Content = Core.Lang.T(okText);
        dialog.CancelButton.Content = Core.Lang.T(dialog.CancelButton.Content as string);
        if (badge is not null)
        {
            // Burnt orange, in one badge: what is unusual about this run is
            // said once, the window itself stays calm.
            dialog.Badge.IsVisible = true;
            dialog.BadgeText.Text = Core.Lang.T(badge);
            if (tone == Tone.Run)
            {
                // A plain count (COPY) wears the green of the owner's TC theme
                // ("Исполняемые", #18e030), not a blue-green.
                dialog.Badge.Background = new SolidColorBrush(Color.Parse("#2018e030"));
                dialog.Badge.BorderBrush = new SolidColorBrush(Color.Parse("#1a8f2a"));
                dialog.BadgeText.Foreground = new SolidColorBrush(Color.Parse("#18e030"));
            }
            else dialog.OkButton.BorderBrush = new SolidColorBrush(Color.Parse("#cc5500"));
        }
        dialog.OkButton.Classes.Add(tone switch { Tone.Run => "run", Tone.Warn => "warn", Tone.Danger => "danger", _ => "plain" });
        if (value is not null)
        {
            dialog.Input.IsVisible = true;
            dialog.Input.Text = value;
            dialog.Opened += (_, _) => { dialog.Input.Focus(); dialog.Input.SelectAll(); };
        }
        else if (tone == Tone.Danger)
        {
            // What cannot be taken back is not confirmed by a key held down: two such questions
            // come one after another (the folder outside the program, then the contents), and
            // Enter kept pressed after the first would answer the second. Enter and Esc both
            // mean "no" here; "yes" is a press of its own button. And not the second click of a
            // double click either: the two questions open in the same place, their "yes" under
            // the same pointer, so "yes" comes alive only when a double click is over.
            dialog.OkButton.IsDefault = false;
            dialog.OkButton.IsEnabled = false;
            dialog.Opened += async (_, _) =>
            {
                dialog.CancelButton.Focus();
                await Task.Delay(700);
                dialog.OkButton.IsEnabled = true;
            };
        }
        else
        {
            dialog.Opened += (_, _) => dialog.OkButton.Focus();
        }
        if (!owner.IsVisible && owner is IReveal hidden) hidden.Reveal();
        await dialog.ShowDialog(owner);
        return dialog._result;
    }
}
