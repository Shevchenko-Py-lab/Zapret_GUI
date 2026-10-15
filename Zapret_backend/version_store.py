"""Find complete zapret installations below Zapert_dir without relying on folder names.

Only inspect files; never change installations. Prefer highest parsed version.
"""
from __future__ import annotations

from pathlib import Path
import re

ROOT_NAME = 'Zapert_dir'
REQUIRED = ('service.bat', 'bin/winws.exe', 'utils/test zapret.ps1')
VERSION_PATTERN = re.compile(r'(?<!\d)(\d+\.\d+\.\d+(?:\.\d+)?)(?!\d)')


def version_key(folder: Path) -> tuple[int, ...] | None:
    """Return version in directory name (supports suffixes, prefixes and v1.10.3)."""
    hits = VERSION_PATTERN.findall(folder.name)
    return tuple(int(x) for x in hits[-1].split('.')) if hits else None


def missing_files(folder: Path) -> list[str]:
    missing = [p for p in REQUIRED if not (folder / p).is_file()]
    if not any(p.is_file() for p in folder.glob('general*.bat')):
        missing.append('general*.bat')
    return missing


def is_complete(folder: Path) -> bool:
    return folder.is_dir() and not missing_files(folder)


def _iter_dirs(root: Path):
    """Discover directories without following symlinks or scanning outside storage."""
    import os
    for parent, folders, _ in os.walk(root, followlinks=False):
        folders[:] = [d for d in folders if not (Path(parent) / d).is_symlink()]
        yield Path(parent)


def _version_for(folder: Path, storage: Path) -> tuple[int, ...] | None:
    current = folder
    while current != storage:
        version = version_key(current)
        if version is not None:
            return version
        current = current.parent
    return version_key(storage)


def installed_versions(storage: str | Path) -> list[Path]:
    storage = Path(storage).expanduser().resolve()
    if not storage.is_dir():
        return []
    candidates = []
    for folder in _iter_dirs(storage):
        # Only version-identifiable roots: skip unrelated nested utilities.
        if (folder / 'service.bat').is_file() and is_complete(folder):
            version = _version_for(folder, storage)
            if version is not None:
                candidates.append((version, folder))
    candidates.sort(key=lambda entry: (entry[0], str(entry[1]).casefold()), reverse=True)
    return [folder for _, folder in candidates]


def latest_installation(storage: str | Path) -> Path:
    storage = Path(storage).expanduser().resolve()
    versions = installed_versions(storage)
    if versions:
        return versions[0]
    if not storage.is_dir():
        raise FileNotFoundError(f'Не найдена папка хранилища: {storage}')
    found = []
    for folder in _iter_dirs(storage):
        if (folder / 'service.bat').is_file():
            relative = folder.relative_to(storage)
            missing = missing_files(folder)
            version = _version_for(folder, storage)
            status = (f'отсутствует: {", ".join(missing)}' if missing else
                      'файлы на месте, но версия не найдена в имени каталога')
            found.append(f'  {relative} [версия: {version}] — {status}')
    details = '\n'.join(found[:20]) if found else '  Ни одного service.bat внутри Zapert_dir не обнаружено'
    raise FileNotFoundError(
        f'В {storage} нет полной установки zapret с определяемой версией.\n'
        f'Обнаружено:\n{details}\n'
        f'Необходимы {", ".join(REQUIRED)} и general*.bat.\n'
        'Если файлы есть, проверь фактическую структуру и названия папок.'
    )
