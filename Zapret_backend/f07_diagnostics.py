"""F-07 read-only Windows diagnostics for Zapret Control.

No automatic repairs, DNS flush, service start/stop, or system modifications.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import json
import os
import subprocess
import time
import locale
import ctypes

try:
    import psutil
except ImportError:
    psutil = None


@dataclass(frozen=True)
class CheckResult:
    key: str
    title: str
    status: str  # ok / warning / error / unknown
    details: str


def _command(*args: str, timeout: int = 6) -> tuple[int, str]:
    try:
        process = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        if os.name == "nt":
            encoding = f"cp{ctypes.windll.kernel32.GetOEMCP()}"
        else:
            encoding = locale.getpreferredencoding(False)

        output = process.stdout.decode(
            encoding, errors="replace"
        ).strip()

        return process.returncode, output

    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)


class Diagnostics:
    def __init__(self, zapret_dir: str | Path):
        self.root = Path(zapret_dir)

    def run(self) -> list[CheckResult]:
        results = []
        def add(key: str, title: str, status: str, details: str):
            results.append(CheckResult(key, title, status, details))

        root = self.root
        add('install', 'Файлы zapret', 'ok' if (root/'service.bat').is_file() and (root/'bin'/'winws.exe').is_file() else 'error',
            f'Каталог: {root}')
        if os.name != 'nt':
            add('platform', 'Windows', 'unknown', 'Диагностика системных служб доступна только в Windows')
            return results

        try:
            service = psutil.win_service_get("BFE")
            status = service.status()

            add(
                "bfe",
                "Base Filtering Engine",
                "ok" if status == "running" else "error",
                f"Состояние службы: {status}"
            )

        except (psutil.Error, OSError) as exc:
            add(
                "bfe",
                "Base Filtering Engine",
                "unknown",
                f"Не удалось проверить службу: {exc}"
            )

        winws = []
        conflicts = []
        if psutil is not None:
            for proc in psutil.process_iter(['name','exe','pid']):
                try:
                    name = (proc.info.get('name') or '').lower()
                    if name == 'winws.exe':
                        winws.append(f"PID {proc.info['pid']}: {proc.info.get('exe') or '?'}")
                    if name in {'adguardsvc.exe', 'goodbyedpi.exe'}:
                        conflicts.append(f"{name} PID {proc.info['pid']}")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        add('winws', 'Процесс winws.exe', 'ok' if winws else ('unknown' if psutil is None else 'warning'),
            '; '.join(winws) if winws else ('psutil не установлен' if psutil is None else 'Не запущен'))
        add('conflicts', 'Конфликтующее ПО', 'warning' if conflicts else ('unknown' if psutil is None else 'ok'),
            '; '.join(conflicts) if conflicts else 'Известные процессы не обнаружены')

        code, output = _command('netsh', 'interface', 'tcp', 'show', 'global')
        add('tcp', 'Параметры TCP', 'ok' if code == 0 else 'unknown', output[-900:])
        code, output = _command('netsh', 'winhttp', 'show', 'proxy')
        add('proxy', 'WinHTTP proxy', 'ok' if code == 0 else 'unknown', output[-450:])
        try:
            service = psutil.win_service_get("WinDivert")
            status = service.status()

            add(
                "windivert",
                "Служба WinDivert",
                "ok" if status == "running" else "warning",
                f"Состояние службы: {status}"
            )

        except (psutil.Error, OSError) as exc:
            add(
                "windivert",
                "Служба WinDivert",
                "warning",
                f"Служба недоступна или не зарегистрирована: {exc}"
            )
        return results

    @staticmethod
    def export_json(results: list[CheckResult], destination: str | Path) -> Path:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'checked_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                                    'results': [asdict(x) for x in results]}, ensure_ascii=False, indent=2), encoding='utf-8')
        return path
