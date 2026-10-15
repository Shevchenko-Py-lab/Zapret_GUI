"""F-01: control one installed winws executable, with startup diagnostics.

Requires psutil. Does not elevate permissions automatically or kill winws globally.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import psutil

START_TIMEOUT = 15.0


class PowerController:
    def __init__(self, start_file: str | Path, stop_file: str | Path | None = None):
        self.start_file = Path(start_file).expanduser().resolve()
        self.stop_file = Path(stop_file).expanduser().resolve() if stop_file else None
        self.root = self.start_file.parent
        self.winws = self.root / 'bin' / 'winws.exe'
        self.process: subprocess.Popen | None = None
        self.last_log: Path | None = None
        if not self.start_file.is_file():
            raise FileNotFoundError(self.start_file)
        if not self.winws.is_file():
            raise FileNotFoundError(f'Не найден winws.exe: {self.winws}')

    @staticmethod
    def _command(path: Path) -> list[str]:
        if os.name == 'nt' and path.suffix.lower() in ('.bat', '.cmd'):
            return ['cmd.exe', '/d', '/c', str(path)]
        if path.suffix.lower() == '.py':
            return [sys.executable, str(path)]
        return [str(path)]

    def _instances(self) -> list[psutil.Process]:
        wanted = os.path.normcase(os.path.normpath(str(self.winws)))
        found = []
        for proc in psutil.process_iter(['pid', 'name', 'exe']):
            try:
                if (proc.info['name'] or '').lower() != 'winws.exe':
                    continue
                actual = proc.info['exe']
                if actual and os.path.normcase(os.path.normpath(actual)) == wanted:
                    found.append(proc)
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except psutil.AccessDenied as exc:
                raise PermissionError(
                    f'Нет доступа к сведениям о winws PID {proc.pid}. '
                    'Проверь, с какими правами запущен Zapret Control.'
                ) from exc
        return found

    def is_running(self) -> bool:
        return bool(self._instances())

    def _startup_error(self, elapsed: float) -> RuntimeError:
        notes = [f'BAT запущен, но нужный winws.exe не обнаружен за {elapsed:.0f} секунд.',
                 f'BAT: {self.start_file}', f'Ожидаемый EXE: {self.winws}']
        if self.process is not None:
            code = self.process.poll()
            notes.append('BAT завершился, код: ' + str(code) if code is not None
                         else 'BAT всё ещё выполняется (возможно, ожидает ввода).')
        other = []
        for proc in psutil.process_iter(['pid', 'name', 'exe']):
            try:
                if (proc.info['name'] or '').lower() == 'winws.exe':
                    other.append(f"PID {proc.pid}: {proc.info.get('exe') or 'путь недоступен'}")
            except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
                continue
        if other:
            notes.append('Найдены winws.exe (возможно, из другой папки): ' + '; '.join(other[:5]))
        else:
            notes.append('В системе не найден видимый процесс winws.exe.')
        if self.last_log and self.last_log.is_file():
            try:
                content = self.last_log.read_bytes()[-5000:]
                output = content.decode('utf-8', errors='replace').strip()
                if output:
                    notes.append('Последний вывод BAT:\n' + output[-2500:])
            except OSError:
                pass
            notes.append(f'Лог запуска: {self.last_log}')
        notes.append('Попробуй вручную запустить этот BAT из каталога zapret; '
                     'проверь ошибки WinDivert, блокировки антивируса и права.')
        return RuntimeError('\n'.join(notes))

    def start(self, timeout: float = START_TIMEOUT) -> None:
        if self.is_running():
            raise RuntimeError('winws.exe из этой версии zapret уже работает')
        kwargs = {'cwd': str(self.root), 'stdin': subprocess.DEVNULL}
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs['start_new_session'] = True
        # Redirect BAT output to a file rather than PIPE: some launchers leave
        # stdout inherited by children, which could otherwise block the caller.
        fd, path = tempfile.mkstemp(prefix='zapret_start_', suffix='.log')
        self.last_log = Path(path)
        try:
            with os.fdopen(fd, 'wb') as output:
                self.process = subprocess.Popen(self._command(self.start_file),
                                                stdout=output, stderr=subprocess.STDOUT,
                                                **kwargs)
        except Exception:
            try:
                self.last_log.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_running():
                return
            time.sleep(0.25)
        # One last check after deadline (process might appear at boundary).
        if self.is_running():
            return
        raise self._startup_error(timeout)

    def _running_services(self, pids: set[int]) -> list[str]:
        if os.name != 'nt':
            return []
        names = []
        for svc in psutil.win_service_iter():
            try:
                data = svc.as_dict()
                if data.get('status') == 'running' and data.get('pid') in pids:
                    names.append(data['name'])
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
        return names

    def stop(self) -> None:
        instances = self._instances()
        if not instances:
            return
        service_names = self._running_services({p.pid for p in instances})
        if service_names:
            for name in service_names:
                cmd = subprocess.run(['sc.exe', 'stop', name], capture_output=True,
                                     text=True, errors='replace', timeout=15,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
                if cmd.returncode != 0:
                    raise RuntimeError(f'Не удалось остановить службу {name}: {cmd.stdout} {cmd.stderr}')
        else:
            for proc in instances:
                try:
                    proc.terminate()
                except psutil.NoSuchProcess:
                    continue
                except psutil.AccessDenied as exc:
                    raise PermissionError(f'Нет прав завершить winws PID {proc.pid}') from exc
            _, alive = psutil.wait_procs(instances, timeout=5)
            if alive:
                raise RuntimeError('winws не завершился после запроса остановки; повтори после проверки прав')
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if not self.is_running():
                return
            time.sleep(0.25)
        raise RuntimeError('winws всё ещё работает после команды остановки')


if __name__ == '__main__':
    from .version_store import latest_installation
    root = latest_installation(Path(__file__).resolve().parent.parent / 'Zapret_dir')
    control = PowerController(root / 'general.bat')
    print('RUNNING' if control.is_running() else 'STOPPED')
