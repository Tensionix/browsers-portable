# Changelog

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
