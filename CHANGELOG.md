# Changelog

## 2.0.1 — 2026-10-13

- Read what `certutil` and 7-Zip say in the encoding they say it in. The Russian lines of `certutil` came into the journal of "Certificate: install" and "revoke" as rows of replacement marks, because they were read as UTF-8; 7-Zip is now told to name files in UTF-8.

## 2.0.0 — 2026-10-11

The window is new, and every operation can now be run from a console. The engine went through an audit of its own.

- Replace the window: C# on Avalonia instead of NiceGUI in a pywebview frame. `Start.exe` opens `App\AudionBrowsersPortable.exe`; the window carries its own .NET, so nothing is installed. The former window is gone with its launchers (`launcher_gui.cmd` and the rest), its command bar, fzf, the portable PowerShell in `system_core\powershell`, and NiceGUI, pywebview and their dozens of packages: the environment is now Python with PyYAML, 51 MB instead of 123.
- Run any operation from a console through `browsers_cli.cmd`, with the same settings as the button: `describe` prints the sections, operations and fields as JSON, `run --operation ID [--source] [--target] [--set NAME=VALUE] [--params FILE] [--result FILE] [--cancel-file FILE]` runs one. Exit codes: 0 done, 1 failed or done in part, 130 stopped, 2 a wrong call - refused before anything runs. The window is the face of this command and runs nothing itself; the line it would type is written into its journal at every start.
- Lay the window out anew: the sections as a row of large buttons under the header; the button that runs a form at the right end of its head, filled with the accent, its description in the tooltip; the rarely needed fields under "Advanced" in sight, nothing folded; a browser as a button with its logo in the colour of that logo; four themes and Russian or English, switched without a restart.
- Show a download as one live line under the journal - percent, megabytes, speed - instead of ten lines of the journal per file.
- Write a line of an operation in the colours its author gave it. The window colours only what it says itself: the title of a run, its verdict, a refusal.
- Stop inside the long steps. STOP is now seen while 7-Zip unpacks or packs, while a build is copied or published, and while a ZIP is written; the operation ends as stopped instead of being ended by the window two minutes later.
- Write an archive beside its place and put it there only when it is whole. A full Target or a stop used to leave a part of an archive under the name of a finished one, with the archive before it already deleted.
- Clear the working folders from the window through the Recycle Bin, where the drive has one. The root of a drive, the program and its own folders are refused, judged by where a path really leads; a path that is itself a link is refused by name.
- Keep the profile with a build named as the Source. Pointing the Source straight at a build - not at the folder holding it - made Update take the browser branch for the build and leave the real profile outside it.
- Keep a folder named like a date that lies beside a browser from elsewhere (`10.10.2026`, `2025.03.01`). It was taken for the browser's version folder and deleted with the old browser.
- Do not let a Chromium tick take another browser. A `chrome.exe` that calls itself Cent Browser, Supermium or Iron is left alone, whatever is ticked.
- Count a download against the size the server announced. A connection that closed early left a cut file in the cache, and every later run took it from there; a kept file that does not open is now removed, and of the downloads of a browser only the last one is kept.
- Prove a certificate by its thumbprint before anything is done with its file. Whatever lay under the name of the certificate in a shared Target used to be added to the Root store first and compared afterwards.
- Fetch the certificate files of an update before the browser is swapped. A certificate server that did not answer used to fail an update whose browser was already replaced, and the next run passed the build by as current.
- Put a build back as it was when rearranging it breaks, and ask once more whether its browser is running right before rearranging. Clear what a run that was ended hard left inside a build - a new browser never put in, an old one never removed - at the next run.
- Defender guard: restore the script it starts through UAC (it was missing, so nothing was excluded); remember the folders it excluded until the exclusion is seen gone, and take those back with "Defender: remove exclusion" too; report a removal that did not happen as a failure; ask for UAC once for "Update DLL only" instead of once per browser; accept a folder that is the root of a drive.
- Let Check finish when one vendor does not answer: the row of that browser says why, the others are shown.
- End Update with an error when the ticked browsers have no build at all, instead of a green "done".
- Keep the settings file of the portability library (INI) through Update, as "Update DLL only" always did: the same library keeps the file as it was left, another library gets its own defaults. An edited `version.ini` of the proxy library no longer counts as "wrapper update available".
- Pack the archive of an updated build without its profile: the browser, its launcher, the note of the versions and the certificate files, with `Data` and `Cache` empty. It used to be the whole build folder - bookmarks, passwords and cookies with it - under a tooltip that called it ready to hand over.
- Find builds for "Update DLL only" and "Add to build" the way Check and Update find them: at any depth under Source, under any folder name, every build of a ticked browser. Both used to look only in Source itself and right under it, by the standard folder name.
- Read only `%NAME%` in a path. A folder called `$TEMP` used to turn into the temporary folder of the user.
- Make "Clear workspace" delete what its tooltip names: the downloaded installers and what was unpacked from them, `Portable\_archives` and `Portable\_tmp` in the Target folder.
- Keep `cleanup_project.cmd` inside the project: with a folder link anywhere in the tree it no longer walks through it.

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
