"""Which browsers this program builds, and where each one actually comes from.

The rule for being here: the vendor ships **no portable build that updates
itself**. A browser that already has both — Vivaldi with its standalone install,
Cent Browser with its portable SFX, Opera with its own autoupdate packages — gains
nothing from this program and is deliberately absent.

Two shapes cover every entry:

- `installer` — a setup .exe whose payload is a 7z archive holding the browser
  directory (`Chrome.7z` → `Chrome-bin`, `browser.7z` → `Browser-bin`). Two
  unpackings, no installation.
- `archive`   — a .zip the vendor already publishes; one unpacking, and the
  browser folder is found by looking for its executable.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrowserSpec:
    """One browser: where to get it, how to open it, how to name its version."""

    id: str
    name: str
    folder: str
    executable: str
    kind: str  # "installer" | "archive"
    version_source: str  # "github_tag" | "chrome_api" | "yandex_redirect"
    why: str  # why this browser is here at all - shown in the docs, not the code
    repo: str = ""
    asset: str = ""  # regular expression over the release asset names
    url: str = ""  # direct download, when the vendor publishes no release list
    # How the published version relates to what the executable reports.
    # "exact" - the same number. "tail" - the published one is the end of it:
    # Brave tags 1.93.134 and ships a brave.exe calling itself 151.1.93.134,
    # where 151 is the Chromium major.
    version_match: str = "exact"
    payload_archive: str = ""  # installer only: the 7z inside the setup
    payload_directory: str = ""  # installer only: the folder inside that 7z
    registry_branch: str = ""  # what the browser leaves in HKCU, if anything
    shared_registry_with_installed: bool = True
    user_agent: str = ""


# The Windows user agent Yandex insists on; with anything else its download
# address redirects to the App Store.
WINDOWS_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/139.0.0.0 Safari/537.36"
)


BROWSERS: tuple[BrowserSpec, ...] = (
    BrowserSpec(
        id="chrome",
        name="Google Chrome",
        folder="Google Chrome Portable",
        executable="chrome.exe",
        kind="installer",
        version_source="chrome_api",
        why="Google ships no portable build at all; the installed one updates itself, a copied folder does not.",
        url="https://dl.google.com/chrome/install/ChromeStandaloneSetup64.exe",
        payload_archive="Chrome.7z",
        payload_directory="Chrome-bin",
        registry_branch=r"HKCU\Software\Google\Chrome",
    ),
    BrowserSpec(
        id="yandex",
        name="Yandex Browser",
        folder="Yandex Browser Portable",
        executable="browser.exe",
        kind="installer",
        version_source="yandex_redirect",
        why="No portable build, and it is the browser that already trusts the Russian Trusted CA.",
        url="https://browser.yandex.ru/download?full=1",
        payload_archive="browser.7z",
        payload_directory="Browser-bin",
        registry_branch=r"HKCU\Software\Yandex\YandexBrowser",
        user_agent=WINDOWS_USER_AGENT,
    ),
    BrowserSpec(
        id="brave",
        name="Brave",
        folder="Brave Portable",
        executable="brave.exe",
        # The zip rather than BraveBrowserStandaloneSilentSetup.exe on purpose:
        # that installer is a PE holding one LZMA stream, and the portable
        # `7za.exe` cannot open PE containers at all - only the full 7-Zip can.
        # Brave publishes this build in the same release, so the version still
        # matches and no extra tooling has to be shipped.
        kind="archive",
        version_source="github_tag",
        why="Only third-party portable builds exist, and none of them updates itself.",
        repo="brave/brave-browser",
        asset=r"brave-v[\d.]+-win32-x64\.zip",
        version_match="tail",
        registry_branch=r"HKCU\Software\BraveSoftware\Brave-Browser",
    ),
    BrowserSpec(
        id="chromium_gost",
        name="Chromium-Gost",
        folder="Chromium-Gost Portable",
        executable="chrome.exe",
        kind="archive",
        version_source="github_tag",
        why="Speaks GOST TLS, which government portals require and no amount of certificates gives plain Chrome. Ships a zip and nothing that updates it.",
        repo="deemru/chromium-gost",
        asset=r"chromium-gost-[\d.]+-windows-amd64\.zip",
        registry_branch=r"HKCU\Software\Chromium",
    ),
    BrowserSpec(
        id="ungoogled_chromium",
        name="Ungoogled Chromium",
        folder="Ungoogled Chromium Portable",
        executable="chrome.exe",
        kind="archive",
        version_source="github_tag",
        why="A zip exists, an updater does not: the whole point of the project is that nothing phones home.",
        repo="ungoogled-software/ungoogled-chromium-windows",
        asset=r"ungoogled-chromium_[\d.\-]+_windows_x64\.zip",
        registry_branch=r"HKCU\Software\Chromium",
    ),
)


BROWSERS_BY_ID = {spec.id: spec for spec in BROWSERS}


# Kept in the code rather than in a document, because someone will ask again and
# the answer has to survive the asking. Each of these ships a portable build that
# updates itself, so this program would add nothing.
EXCLUDED = {
    "vivaldi": "Install Standalone is official and its automatic updates work in it.",
    "cent": "Official portable SFX with a built-in updater that can be switched off in settings.",
    "opera": "No separate portable file, but the vendor publishes Autoupdate packages beside every Setup.",
    # Not the rule above - this one was tried and dropped. Every point was seen
    # on a live build, not read off a page:
    #   * Alex313031/Thorium-Win publishes releases with no files at all and
    #     points at the gz83 fork, so there is no single source to follow;
    #   * that fork ships only WIN32_SSE2, a 32-bit build for old machines;
    #   * the Chrome Web Store does not work in it - a plain uBlock Origin
    #     install fails, which is what a build without Google API keys does.
    "thorium": "Dropped after testing: no first-party release files, 32-bit only, and the Web Store does not work in it.",
    # AI browsers are declined as a class, and for one reason: an assistant that
    # drives the browser a person already uses removes the point of a separate
    # browser. Both cases below say the same thing from opposite ends. Comet
    # peaked near 10M users in early 2026 and sat at roughly 3M active by
    # mid-year, and its public installer is a ~14 MB stub whose real URL is
    # issued by page scripts - nothing stable to unpack. Atlas was macOS-only for
    # its whole life and OpenAI shut it down on 9 August 2026, nine months after
    # launch, folding agentic browsing back into ChatGPT and Codex.
    "comet": "Declined: an assistant inside the usual browser removes the reason for a separate one; only a stub installer is published.",
    "atlas": "Gone: discontinued by OpenAI on 9 August 2026, never released for Windows, same reason as Comet.",
    # Out for a different reason than any of the above: it does not fit the
    # machinery. The Windows build is a thin app on WebView2, so the engine comes
    # from the system's Edge runtime rather than from the download - there is no
    # browser directory to unpack and no Chromium process for Chrome++ to hook.
    "duckduckgo": "Not applicable: the Windows build runs on the system WebView2 runtime, not a Chromium tree of its own.",
}


def browser(browser_id: str) -> BrowserSpec:
    spec = BROWSERS_BY_ID.get(str(browser_id or "").strip().lower())
    if spec is None:
        known = ", ".join(BROWSERS_BY_ID)
        raise RuntimeError(f"Unknown browser: {browser_id!r}. Known: {known}")
    return spec
