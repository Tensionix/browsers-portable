# Audion Browsers Portable - user guide

This program makes portable browsers: ones that live in a folder, start from
anywhere, and are never installed into Windows. Bookmarks, passwords and tabs
stay inside that folder, so it travels on a flash drive or goes to someone else
as it is.

## How the window works

Four tabs across the top: `INSTALL`, `UPDATE`, `CERTIFICATE`, `SERVICE`. That is
the whole menu: press a tab and its commands, with their settings, are right
underneath — there is nowhere to descend into. Above the tabs sits a service
strip with the folder cleanups, which belong to the program as a whole rather
than to any one tab.

Every choice is made with buttons. The chosen one is washed with blue, the rest
stay outlined. Browsers are outlined each in its own colour, so the row is read
by name rather than by position.

Captions are short. The explanation — what will happen, what a wrong answer
costs — appears in the tooltip when the pointer rests on the control.

## First run

1. Tab `SERVICE` → `7-ZIP`. Nothing can be unpacked without it.
2. Tab `INSTALL` → pick the browsers with the buttons and press `BUILD`.

Each browser is 120–240 MB of download and about half a gigabyte on disk. The
builds appear in the Target folder (`output\Portable`); start one with
`<Browser> Portable.cmd` in its root.

If one browser fails, the others are still built — the failures are listed at the
end.

## Which browsers are here, and why only these

Those without a self-updating portable build of their own:

- **Google Chrome** — no portable version exists;
- **Yandex Browser** — no portable version, and the Russian CA certificates are
  already built into it;
- **Brave** — the portable builds are third-party and do not update;
- **Chromium-Gost** — speaks the GOST encryption that government portals need;
- **Ungoogled Chromium** — Chromium without Google's services.

Not here and not planned: **Vivaldi**, **Cent Browser** and **Opera** — each has
its own portable install that updates itself, so this program adds nothing.
**Thorium** was tried and removed: 32-bit builds only, and store extensions do
not install in it.

## What is inside a build

| Folder or file | What it is |
| --- | --- |
| `App` | The browser itself. Replaced wholesale on update. |
| `Data` | Your profile: bookmarks, passwords, tabs, extensions. |
| `Cache` | Cache. Safe to delete. |
| `Certificates` | The certificates and two files — install and remove. |
| `<Browser> Portable.cmd` | Starts the browser. |
| `Portable-Build.json` | Which versions are inside. |

## Updating

The `UPDATE` tab.

`CHECK` shows what has been published next to the version of your build. Nothing
is downloaded.

`UPDATE` replaces only the browser inside the build and leaves `Data` and `Cache`
alone, so the profile stays. A build whose browser and wrapper are both current
is skipped without downloading.

**The update happens where the build lies.** Point Source at its folder — a flash
drive, a network share, wherever it lives — and it is updated in place. Nothing
has to be copied, and the Target folder is not used here: that one is for new
builds.

With no build in Source, the program looks in `output\Portable` — at what it made
itself.

## The certificates

Russian state sites are signed by an authority Windows does not trust out of the
box, so such a site opens with a security warning. The steps are separate so that
nothing happens by itself.

**While building** — the checkbox `State site certificates`. It puts the files
and two shortcuts, install and revoke, into the build. It installs nothing.

**Tab `CERTIFICATE` → `INSTALL`** — adds them to your own Windows account's
store, which every Chromium browser reads. No administrator rights; other users
of the machine see no change.

**`REVOKE`** — removes exactly those two certificates by fingerprint; anything
else stays.

**`CHECK`** shows whether they are installed and changes nothing.

Yandex Browser needs none of this: the certificates are already in it.

## Build settings

**Portability.** What keeps the profile inside the build folder. `CHROME++` is
the wrapper this program started with: its `version.dll` goes next to the
browser. `PROXY LIBRARY` is another wrapper of the same kind, by neyrostalker: it does
the same job, additionally blocks writes to the registry, and Microsoft's
antivirus does not treat it as a threat. The differences and the check results are in
`docs/CHROME_PLUS_AND_DEFENDER.md`.

**Wrapper architecture.** Leave it at `AUTO`. The program reads whether the
browser is 32- or 64-bit and uses the matching Chrome++ wrapper. This is not a
formality: a 32-bit browser with a 64-bit wrapper starts as if nothing were
wrong, but its profile goes to the system and the build stops being portable.

**Leave no traces in Windows.** Off by default. With it on, the browser wipes its
own registry branch when it exits. That branch is shared with an installed copy
of the same browser, so turn it on only where that browser is not installed.

**Pack into an archive.** Turn it on when the builds are to be handed over: one
file instead of a folder. The format sits next to it — `ZIP` opens anywhere, `7Z`
is smaller but needs 7-Zip on the other side.

**Keep working files** (under `Advanced`). Downloads and unpacked installers stay
in `workspace` — useful when a build failed and the reason has to be found.

## Worth knowing

The first start of any build takes longer — the browser is laying out its profile.

A portable build and an installed browser of the same name run side by side
without disturbing each other.
