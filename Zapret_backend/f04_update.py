"""F-04. Download a GitHub release ZIP and stage a new version.

Source repository and asset must be supplied explicitly. Does not execute downloads,
replace a running installation or attempt to migrate configuration.
"""
from __future__ import annotations
import argparse
from pathlib import Path, PurePosixPath
import json
import os
import re
import tempfile
from urllib.request import Request, urlopen
import shutil
from .version_store import is_complete
import zipfile

_API = 'https://api.github.com/repos/{repo}/releases/latest'


def latest_release(repo: str) -> dict:
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Репозиторий должен иметь вид owner/repo')
    request = Request(_API.format(repo=repo), headers={'Accept': 'application/vnd.github+json',
                                                       'User-Agent': 'ZapretControl/0.1'})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def pick_zip(release: dict, asset_name: str) -> dict:
    found = [a for a in release.get('assets', []) if a.get('name') == asset_name]
    if len(found) != 1 or not asset_name.lower().endswith('.zip'):
        raise ValueError(f'ZIP-артефакт {asset_name!r} не найден в релизе')
    url = found[0].get('browser_download_url', '')
    if not url.startswith('https://github.com/'):
        raise ValueError('Неожиданный источник загрузки')
    return found[0]


def _safe_extract(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        if len(infos) > 10000 or sum(i.file_size for i in infos) > 1024 * 1024 * 1024:
            raise ValueError('Слишком большой архив')
        for info in infos:
            name = info.filename.replace('\\', '/')
            p = PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or ':' in name or name.startswith('/'):
                raise ValueError(f'Недопустимый путь в ZIP: {name}')
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError('Символьные ссылки в ZIP не разрешены')
        zf.extractall(destination)


def install_release(repo: str, asset_name: str, versions_dir: str | Path) -> Path:
    """Create a new version folder; leave all existing versions untouched."""
    release = latest_release(repo)
    tag = str(release['tag_name'])
    version_match = re.fullmatch(r'v?(\d+(?:\.\d+)*)', tag, re.I)
    if not version_match:
        raise ValueError(f'Неподдерживаемый тег релиза: {tag}')
    name = f'zapret-discord-youtube-{version_match.group(1)}'
    asset = pick_zip(release, asset_name)
    versions = Path(versions_dir).expanduser().resolve()
    versions.mkdir(parents=True, exist_ok=True)
    target = versions / name
    if target.exists():
        raise FileExistsError(f'Версия уже существует (перезапись запрещена): {target}')
    with tempfile.TemporaryDirectory(dir=versions) as td:
        temp = Path(td)
        zip_path = temp / 'release.zip'
        request = Request(asset['browser_download_url'], headers={'User-Agent': 'ZapretControl/0.1'})
        limit = 1024 * 1024 * 1024
        with urlopen(request, timeout=60) as response, zip_path.open('wb') as output:
            downloaded = 0
            while part := response.read(1024 * 1024):
                downloaded += len(part)
                if downloaded > limit:
                    raise ValueError('Размер архива превысил ограничение')
                output.write(part)
        extract_dir = temp / 'extracted'
        extract_dir.mkdir()
        _safe_extract(zip_path, extract_dir)
        # Release archives may contain a single root directory or files at ZIP root.
        roots = [p for p in extract_dir.iterdir()]
        payload = roots[0] if len(roots) == 1 and roots[0].is_dir() else extract_dir
        if not is_complete(payload):
            raise ValueError('Распакованный релиз не содержит обязательные файлы zapret; ничего не установлено')
        # Atomic move from temp directory on the same filesystem: no partial final folder.
        payload.rename(target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description='F-04: получение новой версии zapret')
    parser.add_argument('--repo', required=True, help='Доверенный GitHub репозиторий owner/repo')
    parser.add_argument('--asset', required=True, help='Точное имя ZIP-архива в GitHub Releases')
    parser.add_argument('--versions-dir', default=str(Path(__file__).resolve().parent.parent / 'Zapert_dir'))
    parser.add_argument('--check', action='store_true', help='Только проверить последнюю версию')
    args = parser.parse_args()
    release = latest_release(args.repo)
    print('Последний релиз:', release['tag_name'])
    asset = pick_zip(release, args.asset)
    print('Архив:', asset['name'])
    if not args.check:
        print('Распаковано:', install_release(args.repo, args.asset, args.versions_dir))
        print('Новая версия скачана отдельно. Текущая установка не изменена.')


if __name__ == '__main__':
    main()
