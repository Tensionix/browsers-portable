using Avalonia;
using Avalonia.Controls;
using Avalonia.Media.TextFormatting;

namespace AudionBrowsersPortable.Controls;

/// <summary>
/// A page of the journal: some hundred lines of selectable text. The journal is a column of such
/// pages (MainWindow.OnJournal). The page of Audion Build Orchestrator.
///
/// A text block lays its text out when it is measured and - measured in a column, where the
/// height offered is without end - once more when it is placed, because the room it is given
/// then differs from the room it was offered. A page that only moved up, when the oldest page
/// left the column, laid its hundred lines out anew each time. The layout of the measuring is the
/// right one already - the width is the same, and a page has no controls inside its text to be
/// placed - so a page takes the room it is given and lays nothing out again.
/// </summary>
public class LogPage : SelectableTextBlock
{
#if DEBUG
    /// <summary>How many times a page laid its text out: a check of the window counts what a line added costs.</summary>
    public static int Laid;
#endif

    protected override TextLayout CreateTextLayout(string? text)
    {
#if DEBUG
        Laid++;
#endif
        return base.CreateTextLayout(text);
    }

    protected override Size ArrangeOverride(Size finalSize) => finalSize;
}
