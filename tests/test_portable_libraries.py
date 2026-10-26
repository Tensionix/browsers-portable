from __future__ import annotations

from pathlib import Path
from urllib.error import URLError
import configparser
import json
import shutil
import struct
import subprocess
import zipfile

import pytest
import yaml

from system_core.core.jobs import JobContext
from system_core.core.manifest import Operation
from system_core.core.paths import ensure_project_dirs, get_project_paths
from system_core.services import browsers_portable_service as service
from system_core.services import portable_libraries as libraries
from system_core.services.browser_registry import browser

ROOT = Path(__file__).resolve().parents[1]
CASES = [('proxy_library', 'x86'), ('proxy_library', 'x64')] + [(e, a) for e in ['chrome_plus', 'vivaldi_plus'] for a in ['x86', 'x64', 'arm64']]


def context(tmp_path, **parameters):
    paths = get_project_paths(tmp_path)
    ensure_project_dirs(paths)
    operation = Operation(id='library_test', title='Test', description='', service='', parameters=dict(parameters))
    return JobContext(paths=paths, operation=operation, log_file=paths.logs / 'test.log', report_dir=paths.report)


def bundle(ctx, engine, arch):
    folder = libraries._key(engine, arch)
    source = ROOT / 'tools/portable_libraries' / folder
    if not source.is_dir():
        # The reserve of the libraries - the unchanged upstream archives - is not shipped and not
        # kept in git: the cleanup of the project removes tools\ whole. On a tree without it these
        # checks have nothing real to read, and say so instead of failing.
        pytest.skip(f'no local reserve of the libraries: {source.relative_to(ROOT)}')
    target = ctx.paths.root / 'tools/portable_libraries' / folder
    shutil.copytree(source, target, dirs_exist_ok=True)
    meta = next(target.glob('*.json'))
    data = json.loads(meta.read_text())
    return target / data['name'], data


def offline(*args, **kwargs):
    raise URLError('fixture: library host is unavailable')


def download_wrapper(ctx, engine, arch):
    return service._download_wrapper(ctx, engine, arch)


def place(ctx, build, engine, asset):
    return service._place_wrapper(ctx, browser('chrome'), build, engine=engine, archive=asset, wipe_registry=True)


def fake_browser(build, arch):
    executable = 'chrome.exe'
    raw = bytearray(70)
    raw[:2] = b'MZ'
    struct.pack_into('<I', raw, 0x3C, 64)
    raw[64:68] = b'PE\x00\x00'
    struct.pack_into('<H', raw, 68, {v:k for k,v in libraries.MACHINES.items()}[arch])
    app = build / 'App'
    app.mkdir(parents=True, exist_ok=True)
    (app / executable).write_bytes(raw)


def test_manifest_and_backend_use_proxy_by_default(tmp_path):
    assert service._portable_engine(context(tmp_path)) == 'proxy_library'
    manifest = yaml.safe_load((ROOT / 'config/tool_manifest.yaml').read_text(encoding='utf-8'))
    found = []
    def inspect(node):
        if isinstance(node, dict):
            if node.get('id') == 'portable_engine':
                found.append(node)
                assert node['default'] == 'proxy_library'
                assert [x['value'] for x in node['options']] == list(libraries.ENGINES)
            for value in node.values():
                inspect(value)
        elif isinstance(node, list):
            for value in node:
                inspect(value)
    inspect(manifest)
    assert found


@pytest.mark.parametrize('engine,arch', CASES)
def test_source_outage_uses_selected_library_and_architecture(tmp_path, monkeypatch, engine, arch):
    ctx = context(tmp_path, chrome_plus_arch=arch)
    expected, data = bundle(ctx, engine, arch)
    monkeypatch.setattr(service, 'proxy_library_release', offline)
    monkeypatch.setattr(service, 'github_latest_assets', offline)
    asset, version = download_wrapper(ctx, engine, arch)
    path = asset
    assert path == expected
    assert version == data['version']
    assert libraries.pe_architecture_bytes(libraries.library_files(path, engine, arch)['version.dll']) == arch
    assert '[FALLBACK]' in ctx.log_file.read_text(encoding='utf-8')


@pytest.mark.parametrize('engine,arch', CASES)
def test_download_failure_uses_verified_reserve(tmp_path, monkeypatch, engine, arch):
    ctx = context(tmp_path, chrome_plus_arch=arch)
    expected, data = bundle(ctx, engine, arch)
    monkeypatch.setattr(service, 'proxy_library_release', lambda: (data['version'], data['url']))
    monkeypatch.setattr(service, 'github_latest_assets', lambda repo: (data['version'], [(data['name'], data['url'])]))
    monkeypatch.setattr(service, '_download', offline)
    asset, _ = download_wrapper(ctx, engine, arch)
    assert asset == expected


@pytest.mark.parametrize('engine,arch', CASES)
def test_install_preserves_profile_and_validates_architecture(tmp_path, engine, arch):
    ctx = context(tmp_path, chrome_plus_arch=arch)
    archive, _ = bundle(ctx, engine, arch)
    build = tmp_path / 'build'
    fake_browser(build, arch)
    for folder in ['Data', 'Cache']:
        (build / folder).mkdir()
        (build / folder / 'keep.txt').write_text('existing profile')
    place(ctx, build, engine, archive)
    assert libraries.installed_engine(build) == engine
    assert libraries.pe_architecture(build / 'App/version.dll') == arch
    for folder in ['Data', 'Cache']:
        assert (build / folder / 'keep.txt').read_text() == 'existing profile'
    if engine == 'proxy_library':
        ini = configparser.ConfigParser()
        ini.read(build / 'App/version.ini', encoding='ascii')
        assert all(value == '0' for value in ini['Parameters'].values())
        assert len(ini['Parameters']) == 11
        assert ini['General']['datadir'] == '..\\Data'
        assert ini['General']['cachedir'] == '..\\Cache'
        assert not libraries.needs_refresh(build, engine)
        launcher = service._write_launcher(ctx, browser('chrome'), build)
        raw = launcher.read_bytes()
        assert b'/D "%~dp0App"' in raw
        assert b'\n' not in raw.replace(b'\r\n', b'')
        assert b'\r\r\n' not in raw


@pytest.mark.parametrize('engine', libraries.ENGINES)
def test_mismatch_is_refused_before_replacing_an_existing_dll(tmp_path, engine):
    ctx = context(tmp_path, chrome_plus_arch='x64')
    archive, _ = bundle(ctx, engine, 'x64')
    build = tmp_path / 'build'
    fake_browser(build, 'x86')
    (build / 'App/version.dll').write_bytes(b'preserve existing DLL')
    with pytest.raises(RuntimeError, match='must match'):
        place(ctx, build, engine, archive)
    assert (build / 'App/version.dll').read_bytes() == b'preserve existing DLL'


def test_corrupt_cache_is_skipped_and_valid_cache_precedes_bundle(tmp_path):
    ctx = context(tmp_path)
    archive, data = bundle(ctx, 'vivaldi_plus', 'x64')
    cache_root = service._archives_dir(ctx)
    cache = cache_root / 'portable_libraries/vivaldi_plus/x64'
    cache.mkdir(parents=True)
    cached = cache / data['name']
    shutil.copy2(archive, cached)
    cached.with_suffix('.json').write_text(json.dumps(data))
    assert libraries.local_library(ctx, 'vivaldi_plus', 'x64', cache_root)[0].path == cached
    cached.write_bytes(b'corrupted archive')
    assert libraries.local_library(ctx, 'vivaldi_plus', 'x64', cache_root)[0].path == archive


def test_vivaldi_releases_with_same_asset_name_use_different_cache_files(tmp_path):
    ctx = context(tmp_path)
    archive, _ = bundle(ctx, 'vivaldi_plus', 'x64')
    paths = []
    def download(ctx, url, target, label, **kwargs):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive, target)
        paths.append(target)
        return libraries.LibraryAsset(target.name, url, target, libraries._digest(target), target.stat().st_size)
    for version in ['1.5.8.8', '1.5.8.9']:
        libraries.download_library(ctx, 'vivaldi_plus', 'x64', lambda: (version, 'windows_x64.zip', 'fixture'), download, service._archives_dir(ctx))
    assert paths[0] != paths[1]


def test_no_valid_reserve_returns_an_actionable_error(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    monkeypatch.setattr(service, 'proxy_library_release', offline)
    with pytest.raises(RuntimeError, match='no valid local reserve'):
        download_wrapper(ctx, 'proxy_library', 'x64')
    assert not list(ctx.paths.output.rglob('*.dll'))


@pytest.mark.parametrize('engine', libraries.ENGINES)
def test_invalid_download_payload_falls_back_without_publishing_it(tmp_path, engine):
    ctx = context(tmp_path)
    archive, data = bundle(ctx, engine, 'x64')
    def download(ctx, url, target, label, **kwargs):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'<html>upstream download is unavailable</html>')
        return libraries.LibraryAsset(target.name, url, target, libraries._digest(target), target.stat().st_size)
    asset, version = libraries.download_library(ctx, engine, 'x64',
        lambda: (data['version'], archive.name, data['url']), download, service._archives_dir(ctx))
    assert asset.path == archive
    assert version == data['version']
    assert not list(service._archives_dir(ctx).rglob('*.json'))


def test_an_edited_proxy_ini_is_no_update_and_a_fresh_install_writes_the_defaults(tmp_path):
    """A switch a person turned on is the person's: not a reason to update. Putting the library in anew writes zeros."""
    ctx = context(tmp_path, chrome_plus_arch='x64')
    archive, _ = bundle(ctx, 'proxy_library', 'x64')
    build = tmp_path / 'build'
    fake_browser(build, 'x64')
    place(ctx, build, 'proxy_library', archive)
    (build / 'Data/keep.txt').write_text('existing profile')
    ini = build / 'App/version.ini'
    ini.write_bytes(ini.read_bytes().replace(b'REGOFF=0', b'REGOFF=1').replace(b'DNSOFF=0', b'DNSOFF=1'))
    assert not libraries.needs_refresh(build, 'proxy_library')
    ini.unlink()
    # Without its file the proxy library keeps nothing in the build: that is a reason.
    assert libraries.needs_refresh(build, 'proxy_library')
    place(ctx, build, 'proxy_library', archive)
    assert not libraries.needs_refresh(build, 'proxy_library')
    assert b'REGOFF=0' in ini.read_bytes() and b'DNSOFF=0' in ini.read_bytes()
    assert (build / 'Data/keep.txt').read_text() == 'existing profile'


@pytest.mark.parametrize('engine', libraries.ENGINES)
def test_complete_build_can_use_reserve_when_library_host_is_offline(tmp_path, monkeypatch, engine):
    ctx = context(tmp_path, portable_engine=engine, chrome_plus_arch='x64', browsers=['chrome'], guard_defender=False)
    bundle(ctx, engine, 'x64')
    payload = tmp_path / 'payload'
    fake_browser(payload, 'x64')
    monkeypatch.setattr(service, 'proxy_library_release', offline)
    monkeypatch.setattr(service, 'github_latest_assets', offline)
    monkeypatch.setattr(service, '_require_7zip', lambda context: Path('fixture-7za'))
    def download(ctx, url, target, label, **kwargs):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'fixture browser installer')
        return service.DownloadedAsset(target.name, url, target, libraries._digest(target), target.stat().st_size)
    monkeypatch.setattr(service, '_download', download)
    monkeypatch.setattr(service, 'published_source', lambda spec: ('152.0.0.0', 'fixture', 'browser.zip'))
    monkeypatch.setattr(service, '_unpack_browser', lambda *args: payload / 'App')
    result = service.build_selected(ctx)
    assert not result['failed']
    built = result['built'][0]
    artifact = Path(built['artifact'])
    assert libraries.installed_engine(artifact) == engine
    assert (artifact / 'App/version.dll').is_file()
    assert built['portable_engine'] == engine
    assert '[FALLBACK]' in ctx.log_file.read_text(encoding='utf-8')


# --- Chrome++ of its author: one 7z of every architecture -------------------------------------------------------------

needs_7zip = pytest.mark.skipif(not libraries.SEVEN_ZIP.is_file(), reason='portable 7-Zip is not in place')
AUTHOR_URL = 'https://github.com/Bush2021/chrome_plus/releases/download/1.18.2/Chrome%2B%2B_v1.18.2_x86_x64_arm64.7z'
FORK_URL = 'https://github.com/DeftKing/chrome_plus/releases/download/1.18.3/version-x64-1.18.3.zip'


def pe(arch):
    raw = bytearray(96)
    raw[:2] = b'MZ'
    struct.pack_into('<I', raw, 0x3C, 64)
    raw[64:68] = b'PE\x00\x00'
    struct.pack_into('<H', raw, 68, {v: k for k, v in libraries.MACHINES.items()}[arch])
    return bytes(raw)


def author_7z(folder, swapped=False):
    """The layout of the author: <arch>/App/version.dll and chrome++.ini (UTF-16 LE), <arch>/Data."""
    tree = folder / 'tree'
    for arch in ('x86', 'x64', 'arm64'):
        app = tree / arch / 'App'
        app.mkdir(parents=True)
        (app / 'version.dll').write_bytes(pe({'x86': 'x64', 'x64': 'x86'}.get(arch, arch) if swapped else arch))
        (app / 'chrome++.ini').write_bytes(b'\xff\xfe' + '[general]\nlaunch_on_exit=\n'.encode('utf-16-le'))
        (tree / arch / 'Data').mkdir()
    archive = folder / 'Chrome++_v1.18.2_x86_x64_arm64.7z'
    subprocess.run([str(libraries.SEVEN_ZIP), 'a', '-t7z', str(archive), 'x86', 'x64', 'arm64'], cwd=tree, check=True, capture_output=True)
    return archive


def fork_zip(path):
    with zipfile.ZipFile(path, 'w') as zipped:
        zipped.writestr('version.dll', pe('x64'))
        zipped.writestr('chrome++.ini', b'')
    return path


def test_the_release_of_the_author_is_one_7z_of_every_architecture():
    assets = [('setdll.7z', 'setdll'), ('Chrome%2B%2B_v1.18.2_x86_x64_arm64.7z', AUTHOR_URL)]
    asked = []
    for arch in ('x86', 'x64', 'arm64'):
        found = libraries.github_release('chrome_plus', arch, lambda repo: asked.append(repo) or ('1.18.2', assets))
        assert found == ('1.18.2', 'Chrome++_v1.18.2_x86_x64_arm64.7z', AUTHOR_URL)
    assert set(asked) == {'Bush2021/chrome_plus'}
    with pytest.raises(RuntimeError, match='no arm64 archive'):
        libraries.github_release('chrome_plus', 'arm64', lambda repo: ('1.18.2', [('Chrome++_v1.18.2_x86_x64.7z', AUTHOR_URL)]))


@needs_7zip
def test_the_files_of_one_architecture_are_read_out_of_the_7z(tmp_path):
    archive = author_7z(tmp_path / 'author')
    for arch in ('x86', 'x64', 'arm64'):
        files = libraries.library_files(archive, 'chrome_plus', arch)
        assert sorted(files) == ['chrome++.ini', 'version.dll']
        assert libraries.pe_architecture_bytes(files['version.dll']) == arch
    with pytest.raises(RuntimeError, match='must match x64'):
        libraries.library_files(author_7z(tmp_path / 'swapped', swapped=True), 'chrome_plus', 'x64')
    with pytest.raises(RuntimeError, match='7z of Bush2021/chrome_plus'):
        libraries.library_files(fork_zip(tmp_path / 'version-x64-1.18.2.zip'), 'chrome_plus', 'x64')


@needs_7zip
def test_a_reserve_of_the_fork_is_passed_by_and_the_one_of_the_author_is_taken(tmp_path):
    ctx = context(tmp_path)
    cache_root = service._archives_dir(ctx)
    folder = cache_root / 'portable_libraries/chrome_plus/x64'
    folder.mkdir(parents=True)
    author = folder / '1.18.2-Chrome++_v1.18.2_x86_x64_arm64.7z'
    shutil.copy2(author_7z(tmp_path / 'author'), author)
    fork = fork_zip(folder / '1.18.3-version-x64-1.18.3.zip')
    for path, version, url in ((author, '1.18.2', AUTHOR_URL), (fork, '1.18.3', FORK_URL)):
        path.with_suffix('.json').write_text(json.dumps(dict(engine='chrome_plus', architecture='x64', version=version, name=path.name,
                                                             url=url, sha256=libraries._digest(path), size=path.stat().st_size)))
    asset, version = libraries.local_library(ctx, 'chrome_plus', 'x64', cache_root)
    assert (asset.path, version) == (author, '1.18.2')
    assert not ctx.log_file.exists() or '[WARN]' not in ctx.log_file.read_text(encoding='utf-8')


@needs_7zip
def test_chrome_plus_of_the_author_is_placed_beside_the_browser(tmp_path):
    ctx = context(tmp_path)
    build = tmp_path / 'build'
    fake_browser(build, 'x86')
    place(ctx, build, 'chrome_plus', author_7z(tmp_path / 'author'))
    assert libraries.installed_engine(build) == 'chrome_plus'
    assert libraries.pe_architecture(build / 'App/version.dll') == 'x86'
    assert 'launch_on_exit=' in (build / 'App/chrome++.ini').read_bytes()[2:].decode('utf-16-le')
