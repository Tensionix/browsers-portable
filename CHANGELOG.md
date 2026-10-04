# Changelog

## 1.4.0 — 2026-10-04

- Update portable builds made elsewhere. The build is found by the browser executable anywhere under Source — any folder name, with the browser in `Chrome\`, in `App\Chrome-bin\` or flat in the folder — and the profile by its `Local State` file; the update then brings it to the standard App/Data/Cache layout.
- Update every build found in Source in one run — different browsers, or several builds of one, each in a folder of its own. A failure with one build does not stop the rest.
- Make the browser choice optional for CHECK and UPDATE: with nothing ticked every build in Source is taken, and ticks only narrow that down. BUILD still needs a browser named. UPDATE with nothing ticked and nothing found fails with a plain message instead of finishing green.
- Place CHECK to the left of UPDATE in one panel, reading the same fields, instead of a panel of its own that repeated the browser choice (`beside` in the manifest).
- Move the profile by rename only, never copying or deleting it. Give the folder the standard name when it is the only build of that browser in Source; several builds of one browser keep their own names. Leave the build exactly as it was brought in when the download fails.
- Tell Google Chrome from Chromium builds that share `chrome.exe` by the executable's ProductName; recognise Chromium-Gost and Ungoogled Chromium by folder name, or by the tick when exactly one of the two is ticked.
- Stop before any download when the browser of such a build is running or the standard folder name is already taken beside it.
- Size captions to the controls they stand over: section titles in bold capitals at 13 px, field captions at 13 px, hints at 12 px, all on whole-pixel line heights. Checkbox cards keep the reference 38 px height with the caption on one line — a card grows to its caption instead of wrapping it and making its row taller.
- Report builds in a foreign layout in CHECK, with the version read from where the executable actually is.
- Log the version actually installed after an update, and say so when the vendor's installer is older than the published version.
- Stop announcing a Chrome update that the installer cannot deliver. Google's list names a new version while it is still rolled out to a fraction of users; the version served to everyone is taken instead. A build also remembers the installer it was made from, so CHECK says "up to date" and UPDATE downloads nothing while Google serves that same file — and the kept installer is used only while it is still the one on the server.
- Replace the program icon with a vector drawing: a round blue browser emblem with a download arrow. The icon gains frames for 20 and 40 px, the sizes a display at 125% takes, and its small frames are plain bitmaps; before, all seven frames were PNG and Windows scaled a neighbouring one for those sizes.
- Keep the profile when BUILD is pressed again for a browser that is already in the Target: only `App` inside the existing build is replaced. Before, the folder was cleared first, profile included.
- Delete nothing that lay beside the browser when a build made elsewhere is brought to the standard layout. Only what is certainly the browser goes into `App`; the person's own files stay where they were, and another packer's launchers and settings are moved into `Old files` inside the build instead of being removed.
- Pack each updated build into an archive of its own, named after the build's folder. Two builds of one browser used to be packed into one file, the second over the first.
- Bring the new browser beside the build before the old one is touched, then swap the two by renaming. A copy across volumes that stopped halfway used to leave a partial `App` in place and the old one unrestored.
- Honour Cancel: the download stops, a build that has not yet been touched stays as it was, the remaining builds are not started, and the operation ends as not done instead of as finished.
- Build and update under a long Target path. The installer's nested folders ran past the 260 characters Windows opens, and the build failed with `Chrome.7z was not found`. A build that ends up deeper than that limit is still made, with a warning saying how many of its files Windows may not open from there.

## 1.3.0 — 2026-10-01

- Bring the remaining Chrome/Yandex tools into the shared application: update only the selected DLL library, override Chrome/Yandex installer URLs, disable Yandex's bundled updater, and add certificate files to existing builds.
- Update libraries in place with architecture checks and rollback; preserve browser files, profiles, existing INI settings for the same engine, and verified offline library fallback.
- Download custom installers afresh and read custom Chrome versions from their executables rather than comparing them with Google's stable release API.
- Remove only Yandex's service_update.exe inside App, including an already-current build, and keep certificate download/staging separate from Windows trust changes.
- Wrap browser-selection header controls below their label at narrow window widths to prevent overlap.

## 1.2.0 — 2026-10-01

- Centre section tabs, use Forest Green run buttons, add vertical space between field groups, and align archive controls in one responsive row.
- Add a separate Downloads section for official Cent Browser portable SFX, LibreWolf portable ZIP, Vivaldi's installer for Install Standalone, and the regular DuckDuckGo Windows installer.
- Resolve the current release from official download pages or the vendor's current installer endpoint and retain the original file names and contents; no automatic extraction, installer execution, or DLL replacement.
- Keep verified repeat downloads and collect individual source failures without stopping the remaining downloads.
- Compare SHA256 of a fresh temporary DuckDuckGo installer with the saved copy, updating it only when the contents change; download records stay under report.
- Leave Zen out of this section because its current official Windows release has no portable package.

## 1.1.0 — 2026-10-01

- Make Proxy library the default and write every optional INI switch as 0.
- Replace the unavailable Bush2021 source with DeftKing/chrome_plus, including the new per-architecture ZIP layout.
- Add Vivaldi++ from ca-x/vivaldi_plus to build and update commands.
- Validate library archives and use a verified cached or bundled release of the selected library when its source is unavailable.
- Start generated browser launchers in App so APPDIR=0 retains portable relative profile paths.
