using System.Collections.Generic;
using System.Linq;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Media;
using Avalonia.VisualTree;

namespace AudionBrowsersPortable;

/// <summary>
/// What a button and a chip look like in their states, made of the theme in use.
///
/// The rims are pressed down. The shell draws every button and every switch with a rim of its
/// own colour - grey, the accent, green, orange - and a form full of them flickers; the owner
/// had their brightness pressed down by 70% (10.10.2026). A rim keeps its colour and
/// <see cref="RimShare"/> of its strength.
///
/// With the rims quiet, a state is told by the fill. The shell showed the pointer over a button
/// by its rim alone, and that was gone with the rim: the fill of a button under the pointer now
/// goes a step towards the colour of the text (<see cref="HoverLift"/>), and the fill of a
/// switch that is on - a step towards the accent (<see cref="OnLift"/>; the owner: "the chosen
/// buttons a little brighter in the fill").
///
/// The shell is not rewritten for any of it. A style of the shell takes a colour from a brush of
/// the theme by its key, and a key is looked up from the element upwards. Every button carries
/// the pressed-down brushes of the rim keys in its own resources, so its rim finds them first
/// while panels, cards and fields, which read the same keys, stay as they were. And the fill of
/// a state - under the pointer, on - is set by the shell on the presenter inside the button, by
/// the same key as the fill at rest: the presenter carries the brushes of the states, so the
/// state finds them and the button at rest does not. The rims the shell paints with a colour
/// it also writes text in are named apart (RimAccent, RimBurn, RimFocus) and set by the styles
/// at the top of Window.Styles.axaml.
/// </summary>
public static class ButtonTints
{
    /// <summary>How much of a rim is left: 0.3 is "pressed down by 70%".</summary>
    public const double RimShare = 0.3;

    /// <summary>How far the fill of a button under the pointer goes towards the colour of the text.</summary>
    public const double HoverLift = 0.10;

    /// <summary>How far the fill of a switch that is on goes towards the accent.</summary>
    public const double OnLift = 0.16;

    /// <summary>The wash of a browser's own colour under its button when it is on.</summary>
    public const double BrandWash = 0.28;

    /// <summary>The brushes of the shell that are rims of buttons and switches and nothing else inside one.</summary>
    static readonly string[] RimKeys = { "BorderSoft", "SwitchBorder", "RunBorder", "WarnBorder", "DangerBorder", "AmberBorder", "PinBorder" };

    /// <summary>The fills the shell and this window give a button under the pointer - each the fill it has at rest.</summary>
    static readonly string[] FillKeys = { "ButtonBg", "SwitchBg", "RunBg", "WarnBg", "DangerBg", "PinBg", "ActFill", "ActBurnFill" };

    /// <summary>The pressed-down rims of the theme in use, by the key of the shell: worn by a button.</summary>
    static readonly Dictionary<string, IBrush> Rims = new();

    /// <summary>The fills of the states, by the key of the fill at rest: worn by the presenter of a button.</summary>
    static readonly Dictionary<string, IBrush> States = new();

    static bool _hooked;

    /// <summary>The same colour with <see cref="RimShare"/> of its strength.</summary>
    static IBrush Dim(IBrush brush) =>
        brush is ISolidColorBrush { Color: var colour, Opacity: var opacity } ? new SolidColorBrush(colour, opacity * RimShare) : brush;

    /// <summary>The colour of a brush moved a share of the way towards another.</summary>
    static IBrush Towards(IBrush brush, IBrush other, double share)
    {
        if (brush is not ISolidColorBrush { Color: var from, Opacity: var opacity } || other is not ISolidColorBrush { Color: var to }) return brush;
        static byte Step(byte a, byte b, double t) => (byte)System.Math.Round(a + (b - a) * t);
        return new SolidColorBrush(Color.FromArgb(from.A, Step(from.R, to.R, share), Step(from.G, to.G, share), Step(from.B, to.B, share)), opacity);
    }

    /// <summary>
    /// The brushes are made of the theme in use and every button of every open window puts them
    /// on. Called at the start and after each change of theme; a button made later puts them on
    /// when it is loaded.
    /// </summary>
    public static void Tint()
    {
        if (Application.Current is not { } app) return;
        foreach (var key in RimKeys) Rims[key] = Dim(App.Ink(key));
        app.Resources["RimAccent"] = Dim(App.Ink("Quasar"));
        app.Resources["RimBurn"] = Dim(App.Ink("BurnInk"));
        app.Resources["RimFocus"] = Dim(App.Ink("FocusBorder"));

        // The fill of a button at rest, under a name of its own: a button that is off takes it
        // (Window.Styles.axaml), and not the fill of the state its presenter carries by the key.
        app.Resources["RestFill"] = App.Ink("ButtonBg");

        var text = App.Ink("TextMain");
        foreach (var key in FillKeys) States[key] = Towards(App.Ink(key), text, HoverLift);
        States["SwitchOn"] = Towards(App.Ink("SwitchOn"), App.Ink("Quasar"), OnLift);

        if (!_hooked)
        {
            _hooked = true;
            // A switch is a button too: one handler dresses both.
            Control.LoadedEvent.AddClassHandler<Button>((button, _) => Wear(button));
        }
        if (app.ApplicationLifetime is not IClassicDesktopStyleApplicationLifetime desktop) return;
        foreach (var window in desktop.Windows)
            foreach (var button in window.GetVisualDescendants().OfType<Button>())
                Wear(button);
    }

    static void Wear(Button button)
    {
        foreach (var (key, brush) in Rims) button.Resources[key] = brush;
        if (button.Presenter is not { } face) return;
        foreach (var (key, brush) in States) face.Resources[key] = brush;
    }
}
