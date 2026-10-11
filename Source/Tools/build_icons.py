"""Builds Source/Icons.cs from the owner's icon sets - no icon of the window is drawn by hand.

    E:\\TOOLS\\Lucide-Icons    signs of actions (ISC): strokes on a 24 grid
    E:\\TOOLS\\Simple-Icons    brand marks (CC0): one filled path on a 24 grid, with the brand colour
    E:\\TOOLS\\Devicons        brand marks Simple Icons dropped (MIT): the Mono set, filled paths

Run:  py -3.12 Source\\Tools\\build_icons.py [-Sets E:\\TOOLS]
Add a name to LUCIDE, SIMPLE or DEVICONS below and run it again; Icons.cs is never edited by hand.
"""
import json
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

SETS = pathlib.Path(sys.argv[sys.argv.index('-Sets') + 1]) if '-Sets' in sys.argv else pathlib.Path('E:/TOOLS')
OUT = pathlib.Path(__file__).resolve().parents[1] / 'Icons.cs'

# What the window shows. The key is the name in the set: a sign is asked for by it everywhere.
LUCIDE = [
    # a run and its stop
    'play', 'square',
    # the working folders: pick a folder, the history of paths, pin, open in Explorer, back to the
    # defaults, clear the contents (and the workspace, at the top), list what lies in the source
    'folder', 'history', 'pin', 'external-link', 'rotate-ccw', 'eraser', 'list',
    # the journal: copy, clear the panel, the whole column and back
    'copy', 'trash-2', 'maximize-2', 'minimize-2',
    # a form: back to the defaults, every browser ticked and none
    'undo-2', 'check-check', 'list-x',
    # the top: the look
    'sun',
    # the folders beside the journal: logs, reports, config
    'file-text', 'scroll-text', 'settings',
    # what the manifest names for its sections and operations (the key `icon` of a node):
    # install and build, download, update, check, the library alone
    'package-plus', 'hammer', 'download', 'refresh-cw', 'scan-search', 'puzzle',
    # the certificates: the section, into a build, install, revoke
    'shield-check', 'file-plus', 'shield-plus', 'shield-minus',
    # the service: the section, the unpacker, the exclusion of Defender, the workspace
    'wrench', 'file-archive', 'shield-x', 'brush-cleaning',
    # a browser that is in none of the brand sets (the key `icon` of an option)
    'globe',
]
# The marks of the browsers, named by the registries of the engine (browser_registry.py,
# ready_portable_service.py): the key `icon` of an option. Their colours come from there too.
SIMPLE = ['googlechrome', 'brave', 'vivaldi', 'librewolf', 'duckduckgo']
DEVICONS = ['yandex']
# A mark that is a part of its drawing. Devicons draws Yandex as the whole word; on a button the
# first letter alone is the mark - the first path of the coloured drawing, a letter 208 units
# tall (from y 182.6 to y 390.2 of the 600 box). name -> (the drawing, which path, its height).
DEVICONS_PART = {'yandex': ('Color', 0, 208)}
SVG = '{http://www.w3.org/2000/svg}'


def number(value):
    text = ('%.4f' % float(value)).rstrip('0').rstrip('.')
    return text if text not in ('-0', '') else '0'


NUMBER = r'-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'


def absolute_start(d):
    """The first "m" of a path is absolute whatever its case - but only inside its own path.

    Lucide starts paths with a small "m", and several paths are joined into one geometry here:
    the second "m" would then be counted from the end of the first drawing. It becomes "M", and
    the pairs that followed it - implicit relative lines - get their own "l".
    """
    hit = re.match(r'\s*m\s*(%s)[\s,]*(%s)\s*,?\s*' % (NUMBER, NUMBER), d)
    if not hit:
        return d
    rest = d[hit.end():]
    lead = 'l' if rest and (rest[0].isdigit() or rest[0] in '-.') else ''
    return 'M%s %s%s%s' % (hit.group(1), hit.group(2), lead, rest)


def shape_to_path(element):
    """One SVG primitive as path data, so a sign is a single geometry."""
    tag = element.tag.replace(SVG, '')
    get = lambda name, default=0.0: float(element.get(name, default))
    if tag == 'path':
        return absolute_start(element.get('d', '').strip())
    if tag == 'line':
        return 'M%s %sL%s %s' % tuple(number(get(n)) for n in ('x1', 'y1', 'x2', 'y2'))
    if tag in ('polyline', 'polygon'):
        points = re.findall(r'-?\d*\.?\d+', element.get('points', ''))
        pairs = ['%s %s' % (number(points[i]), number(points[i + 1])) for i in range(0, len(points) - 1, 2)]
        return 'M' + 'L'.join(pairs) + ('Z' if tag == 'polygon' else '')
    if tag in ('circle', 'ellipse'):
        cx, cy = get('cx'), get('cy')
        rx = get('r') if tag == 'circle' else get('rx')
        ry = get('r') if tag == 'circle' else get('ry')
        return 'M%s %sa%s %s 0 1 0 %s 0a%s %s 0 1 0 %s 0Z' % (
            number(cx - rx), number(cy), number(rx), number(ry), number(2 * rx), number(rx), number(ry), number(-2 * rx))
    if tag == 'rect':
        x, y, w, h = get('x'), get('y'), get('width'), get('height')
        rx = float(element.get('rx', element.get('ry', 0)))
        ry = float(element.get('ry', element.get('rx', 0)))
        rx, ry = min(rx, w / 2), min(ry, h / 2)
        if rx == 0 and ry == 0:
            return 'M%s %sh%sv%sh%sZ' % (number(x), number(y), number(w), number(h), number(-w))
        return ('M%s %sh%sa%s %s 0 0 1 %s %sv%sa%s %s 0 0 1 %s %sh%sa%s %s 0 0 1 %s %sv%sa%s %s 0 0 1 %s %sZ' % (
            number(x + rx), number(y), number(w - 2 * rx), number(rx), number(ry), number(rx), number(ry),
            number(h - 2 * ry), number(rx), number(ry), number(-rx), number(ry),
            number(-(w - 2 * rx)), number(rx), number(ry), number(-rx), number(-ry),
            number(-(h - 2 * ry)), number(rx), number(ry), number(rx), number(-ry)))
    return ''


def paths_of(file):
    root = ET.parse(file).getroot()
    parts = [shape_to_path(e) for e in root.iter() if e.tag.replace(SVG, '') in ('path', 'line', 'polyline', 'polygon', 'circle', 'ellipse', 'rect')]
    box = [float(v) for v in root.get('viewBox', '0 0 24 24').split()]
    return ' '.join(p for p in parts if p), box


def quote(text):
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


def main():
    lines = [
        '// Generated by Source/Tools/build_icons.py from Lucide (ISC), Simple Icons (CC0) and Devicons (MIT) - do not edit by hand.',
        'using System.Collections.Generic;',
        '',
        'namespace AudionBrowsersPortable;',
        '',
        '/// <summary>The signs of the window, by their names in the sets they come from. Nothing here is drawn by hand.</summary>',
        'public static partial class Icons',
        '{',
        '    /// <summary>Lucide: strokes on a 24 grid.</summary>',
        '    public static readonly IReadOnlyDictionary<string, string> Lucide = new Dictionary<string, string>',
        '    {',
    ]
    for name in sorted(set(LUCIDE)):
        data, _ = paths_of(SETS / 'Lucide-Icons' / 'package' / 'icons' / (name + '.svg'))
        lines.append('        [%s] = %s,' % (quote(name), quote(data)))
    lines += ['    };', '', '    /// <summary>Brand marks: name -> (brand colour, the side of the square it is drawn in, a filled path).</summary>',
              '    public static readonly IReadOnlyDictionary<string, (string Hex, double Box, string Path)> Brand = new Dictionary<string, (string, double, string)>', '    {']
    colours = {}
    try:
        for entry in json.load(open(SETS / 'Simple-Icons' / 'simple-icons.json', encoding='utf-8')):
            colours[re.sub(r'[^a-z0-9]', '', entry.get('slug', entry['title']).lower())] = '#' + entry['hex']
    except Exception:
        pass
    for name in sorted(set(SIMPLE)):
        data, box = paths_of(SETS / 'Simple-Icons' / 'SVG' / (name + '.svg'))
        lines.append('        [%s] = (%s, %s, %s),' % (quote(name), quote(colours.get(name, '')), number(box[2]), quote(data)))
    for name in sorted(set(DEVICONS)):
        if name in DEVICONS_PART:
            drawing, index, height = DEVICONS_PART[name]
            root = ET.parse(SETS / 'Devicons' / 'SVG' / drawing / (name + '.svg')).getroot()
            part = [e for e in root.iter() if e.tag.replace(SVG, '') == 'path'][index]
            lines.append('        [%s] = (%s, %s, %s),' % (quote(name), quote(''), number(height), quote(shape_to_path(part))))
            continue
        file = SETS / 'Devicons' / 'SVG' / 'Mono' / (name + '.svg')
        if not file.exists():
            file = SETS / 'Devicons' / 'SVG' / (name + '.svg')
        data, box = paths_of(file)
        lines.append('        [%s] = (%s, %s, %s),' % (quote(name), quote(''), number(max(box[2], box[3])), quote(data)))
    lines += ['    };', '}', '']
    OUT.write_text('\n'.join(lines), encoding='utf-8', newline='\r\n')
    print('Icons.cs: Lucide %d, brands %d' % (len(set(LUCIDE)), len(set(SIMPLE)) + len(set(DEVICONS))))


if __name__ == '__main__':
    main()
