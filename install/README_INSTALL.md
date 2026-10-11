# Audion Browsers Portable - build notes

## What a build is for

A build brings back fresh components, and nothing else is downloaded:

- the embedded Python `3.12.x` of the day with the packages of `install\requirements_full.in`
  - PyYAML for the engine, the build bootstrap (`setuptools`, `wheel`, `packaging`) and
  `pytest`;
- the portable 7-Zip the engine unpacks installers with.

`App\` - the window and the PowerShell the program carries - is **not** a product of a build.
It stays through a cleanup and travels in a release as it is. It is rebuilt only when
`Source\` changes, and that needs the .NET 10 SDK.

## The builder

```bat
builder_main.cmd
```

```text
[01] PYTHON ENV CMD        build the portable runtime with curl and tar
[02] PYTHON ENV PS         the same through PowerShell
[04] START LAUNCHER        rebuild Start.exe
[05] WINDOW                rebuild App\ - only after a change in Source\
[06] 7-ZIP                 portable 7za.exe, required to unpack browsers
[09] PORTABLE OFFLINE      install from the local runtime and wheelhouse
[70] CLEAN INSTALL CACHE   the cache of the install, bytecode, bin and obj of Source
[71] VERIFY / DOCTOR       the environment, the console door and the built window
[72] CMD ENCODING CHECK    UTF-8 without BOM, CRLF
[73] UPDATE REQUIREMENTS LOCK
[95]-[98] OPEN install, runtime, wheelhouse, licenses
[00] EXIT
```

What a release build runs, in this order: `02`, `06`, `71`, `70`.

The scripts take the PowerShell of the program first, `App\AudionPs.exe`, so a build runs on
the same PowerShell on every machine; the one of Windows is used only where `App\` is not
built yet.

## The steps of the portable environment

1. Make the folders (`install\init_folders.cmd`).
2. Find and download the latest Python Embedded `3.12.x`.
3. Unpack it into `runtime\`.
4. Enable `import site` in `python3<minor>._pth`.
5. Download `get-pip.py` and install pip.
6. Install the build bootstrap.
7. Rebuild `wheelhouse\` as installable wheels - what was there is removed first.
8. Install the packages into the runtime from the local `wheelhouse\`.
9. Verify with `system_core\doctor.py`.

## Offline

With `runtime\` and `wheelhouse\` already filled:

```bat
install\install_portable_offline.cmd
install\verify_portable_env.cmd
```

## The window

```bat
install\Build-App.cmd
```

The checks of the window, the PowerShell of the program (`Source\Ps`), the window
(`Source\`), the self-check of the built copy against the manifest - and only then the swap
of `App\`. A build that fails leaves the working `App\` in place.

`install\Build-StartLauncher.cmd` rebuilds `Start.exe`, which opens
`App\AudionBrowsersPortable.exe` and steps aside.

## Release archives

The archives of a release, with the third-party notices and the license files in them, are
made by Audion Build Orchestrator and Audion Build Licenses. The builder of this project
makes none.

## Hygiene

- Python Embedded follows the latest `3.12.x`; no patch version is pinned in a script or a
  document.
- `runtime\`, `wheelhouse\` and `tools\` are reproducible payloads. A script cleanly replaces
  only the target it owns.
- `install\Clean-Install-Cache.cmd` clears what an install leaves behind: the transient files
  of `install\download\`, Python bytecode outside the runtime, `bin` and `obj` under `Source\`.
- `cleanup_project.cmd` is a different tool: it returns the project to its sources - the
  environment, the downloads and the working folders go, with the builds and profiles that
  lie in `input` and `output`. It is not part of an install and is never run by one.
- `.cmd` files are UTF-8 without BOM and CRLF. A running `.cmd` cannot be rewritten from
  inside, so builds only check; `[72]` repairs, and the builder is started anew after it.
- A builder stops at a failed command. `[SUCCESS]` is not printed after pip failed.
