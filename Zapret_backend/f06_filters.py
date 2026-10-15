"""F-06: safe editing of Flowseal zapret user lists and filter modes.

No UI or Windows privileges required. Call from a GUI worker if lists are large.
All changes affect the selected zapret installation only; restart zapret to apply.
"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import re
import tempfile
from typing import Iterable

LIST_FILES = {
    'domains': 'list-general-user.txt',
    'excludes': 'list-exclude-user.txt',
    'ips': 'ipset-all.txt',
    'ip_excludes': 'ipset-exclude-user.txt',
}
GAME_MODES = {'disabled', 'all', 'tcp', 'udp'}
IPSET_MODES = {'none', 'any', 'loaded'}
DOMAIN_RE = re.compile(r'^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$', re.I)


@dataclass(frozen=True)
class GameSettings:
    mode: str = 'disabled'
    tcp: str = '1024-65535'
    udp: str = '1024-65535'


def validate_entry(kind: str, raw: str) -> str:
    text = raw.strip()
    if kind in ('domains', 'excludes'):
        text = text.lower().rstrip('.')
        if not DOMAIN_RE.fullmatch(text):
            raise ValueError(f'Некорректное доменное имя: {raw!r}')
    elif kind in ('ips', 'ip_excludes'):
        try:
            network = ipaddress.ip_network(text, strict=False)
            text = str(network)
        except ValueError as exc:
            raise ValueError(f'Некорректный IP/CIDR: {raw!r}') from exc
    else:
        raise ValueError(f'Неизвестный тип списка: {kind}')
    return text


def validate_ports(value: str) -> str:
    result = []
    for item in value.replace(' ', '').split(','):
        parts = item.split('-')
        if len(parts) not in (1, 2) or not all(x.isascii() and x.isdecimal() for x in parts):
            raise ValueError(f'Неверный диапазон портов: {value!r}')
        nums = [int(x) for x in parts]
        if not all(1 <= n <= 65535 for n in nums) or nums[0] > nums[-1]:
            raise ValueError(f'Порт вне диапазона 1–65535: {item}')
        result.append('-'.join(str(n) for n in nums))
    return ','.join(result)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='\n', dir=path.parent, prefix='.zapret-', suffix='.tmp', delete=False) as f:
            tmp = Path(f.name)
            f.write(text)
        os.replace(tmp, path)
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()


class FilterManager:
    def __init__(self, zapret_dir: str | Path):
        self.root = Path(zapret_dir).resolve()
        if not (self.root / 'service.bat').is_file():
            raise FileNotFoundError(f'Не найдена установка zapret: {self.root}')
        self.lists = self.root / 'lists'
        self.game_file = self.root / 'utils' / 'game_filter.enabled'

    def path_for(self, kind: str) -> Path:
        if kind not in LIST_FILES:
            raise ValueError(f'Неизвестный список: {kind}')
        return self.lists / LIST_FILES[kind]

    def read_entries(self, kind: str) -> list[str]:
        path = self.path_for(kind)
        if not path.exists():
            return []
        # Preserve upstream comments when editing through add/remove helpers.
        return [line.strip() for line in path.read_text(encoding='utf-8-sig').splitlines()
                if line.strip() and not line.lstrip().startswith('#')]

    def add(self, kind: str, value: str) -> bool:
        value = validate_entry(kind, value)
        if kind == 'ips' and self.ipset_mode() != 'loaded':
            raise ValueError('Сначала переключите IPSet в режим loaded')
        path = self.path_for(kind)
        old = path.read_text(encoding='utf-8-sig') if path.exists() else ''
        if value in {v.lower() for v in self.read_entries(kind)}:
            return False
        _atomic_write(path, old.rstrip('\r\n') + ('\n' if old.strip() else '') + value + '\n')
        return True

    def remove(self, kind: str, value: str) -> bool:
        value = validate_entry(kind, value)
        if kind == 'ips' and self.ipset_mode() != 'loaded':
            raise ValueError('Сначала переключите IPSet в режим loaded')
        path = self.path_for(kind)
        if not path.exists():
            return False
        lines = path.read_text(encoding='utf-8-sig').splitlines()
        keep = [line for line in lines if line.strip().lower() != value]
        if len(keep) == len(lines):
            return False
        _atomic_write(path, '\n'.join(keep) + '\n')
        return True

    def replace_user_list(self, kind: str, entries: Iterable[str]) -> None:
        if kind == 'ips':
            raise ValueError('Не перезаписывайте ipset-all целиком: используйте add/remove для сохранения списка Flowseal')
        clean = list(dict.fromkeys(validate_entry(kind, x) for x in entries if x.strip()))
        _atomic_write(self.path_for(kind), ('\n'.join(clean) + '\n') if clean else '# user list\n')

    def read_game_settings(self) -> GameSettings:
        props = {}
        if self.game_file.exists():
            for line in self.game_file.read_text(encoding='utf-8-sig').splitlines():
                if '=' in line:
                    k, v = line.split('=', 1)
                    props[k.strip().lower()] = v.strip()
        mode = props.get('mode', 'disabled')
        return GameSettings(mode if mode in GAME_MODES else 'disabled',
                            props.get('tcp', '1024-65535'), props.get('udp', '1024-65535'))

    def save_game_settings(self, mode: str, tcp: str = '1024-65535', udp: str = '1024-65535') -> GameSettings:
        if mode not in GAME_MODES:
            raise ValueError('Game Filter: disabled/all/tcp/udp')
        cfg = GameSettings(mode, validate_ports(tcp), validate_ports(udp))
        _atomic_write(self.game_file, f'mode={cfg.mode}\ntcp={cfg.tcp}\nudp={cfg.udp}\n')
        return cfg

    def ipset_mode(self) -> str:
        active = self.path_for('ips')
        backup = self.lists / 'ipset-all.txt.backup'
        if not active.exists():
            return 'missing'
        data = active.read_text(encoding='utf-8-sig').strip()
        if not data or all(not s.strip() or s.lstrip().startswith('#') for s in data.splitlines()):
            return 'any'
        if data == '203.0.113.113/32':
            return 'none'
        return 'loaded'

    def set_ipset_mode(self, mode: str) -> None:
        """Flowseal semantics; 'any' can affect unrelated sites, confirm in GUI."""
        if mode not in IPSET_MODES:
            raise ValueError(mode)
        current = self.ipset_mode()
        active = self.path_for('ips')
        backup = self.lists / 'ipset-all.txt.backup'
        if current == mode:
            return
        if mode == 'loaded':
            if not backup.exists():
                raise FileNotFoundError('Нет ipset-all.txt.backup: сначала загрузите список IPSet')
            _atomic_write(active, backup.read_text(encoding='utf-8-sig'))
            backup.unlink()
            return
        if current == 'loaded':
            _atomic_write(backup, active.read_text(encoding='utf-8-sig'))
        elif not backup.exists():
            raise FileNotFoundError('Нет резервного IPSet для восстановления режима loaded')
        _atomic_write(active, '' if mode == 'any' else '203.0.113.113/32\n')


    def inspect_saved_settings(self) -> dict:
        """Read-only snapshot of stored filters; never calls service.bat or changes files.

        Missing files are reported instead of being silently treated as valid empty lists.
        Returned object contains source paths and human-readable warnings for the GUI.
        """
        data = {"installation": str(self.root), "lists": {}, "warnings": []}
        for kind, filename in LIST_FILES.items():
            path = self.path_for(kind)
            if not path.is_file():
                data["lists"][kind] = {"path": str(path), "exists": False, "entries": [], "count": 0}
                data["warnings"].append(f"Не найден файл {filename}")
                continue
            entries = self.read_entries(kind)
            data["lists"][kind] = {"path": str(path), "exists": True,
                                    "entries": entries, "count": len(entries)}
        data["game_filter"] = {
            "path": str(self.game_file),
            "exists": self.game_file.is_file(),
            "mode": self.read_game_settings().mode,
            "tcp": self.read_game_settings().tcp,
            "udp": self.read_game_settings().udp,
        }
        if not self.game_file.is_file():
            data["warnings"].append("game_filter.enabled отсутствует: Game Filter выключен по умолчанию")
        data["ipset"] = {"mode": self.ipset_mode(),
                         "backup_exists": (self.lists / 'ipset-all.txt.backup').is_file()}
        if data["ipset"]["mode"] == 'any':
            data["warnings"].append("IPSet any: правила могут затронуть широкий диапазон адресов")
        if data["ipset"]["mode"] == 'missing':
            data["warnings"].append("Режим IPSet не определён: нет ipset-all.txt")
        return data
