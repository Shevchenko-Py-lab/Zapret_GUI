"""F-03: adapter for Flowseal zapret-discord-youtube 1.10.3 built-in tester.

No replacement bypass algorithms. On Windows this runs a temporary, noninteractive
copy of utils/test zapret.ps1 *inside the original utils directory*, so its
$PSScriptRoot-dependent paths keep working. The original script is not edited.

WARNING: The upstream test temporarily stops winws processes and may modify
ipset-all.txt before restoring it; requires elevated permissions. Only run
against a zapret distribution you trust.
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass
from pathlib import Path
from .version_store import latest_installation
import os
import re
import subprocess
import tempfile
import uuid
import threading
import time
import queue
from collections import deque
from typing import Callable


class TestCancelled(Exception):
    """User cancelled an F-03 test. No result should be applied."""


class TestCancellation:
    """Thread-safe cancellation signal and handle to the active PowerShell process."""
    def __init__(self):
        self.event = threading.Event()
        self._lock = threading.Lock()
        self._process = None

    def register(self, process):
        with self._lock:
            self._process = process
            if self.event.is_set():
                self._stop_process(process)

    def clear_process(self):
        with self._lock:
            self._process = None

    def cancel(self):
        self.event.set()
        with self._lock:
            if self._process is not None:
                self._stop_process(self._process)

    @staticmethod
    def _stop_process(process):
        if process.poll() is not None:
            return
        if os.name == 'nt':
            # Terminate only the process tree spawned for this test (not all winws processes).
            subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=15, check=False)
        else:
            process.terminate()


@dataclass(frozen=True)
class Candidate:
    name: str
    ok: int
    errors: int
    unsupported: int
    ping_ok: int = 0
    blocked: int = 0


@dataclass(frozen=True)
class Selection:
    best: Path | None
    candidates: tuple[Candidate, ...]
    report: Path | None = None


STANDARD = re.compile(r'^(.+?)\s+:\s+HTTP OK:\s*(\d+),\s*ERR:\s*(\d+),\s*UNSUP:\s*(\d+),\s*Ping OK:\s*(\d+),\s*Fail:\s*(\d+)', re.M)
DPI = re.compile(r'^(.+?)\s+:\s+OK:\s*(\d+),\s*FAIL:\s*(\d+),\s*UNSUP:\s*(\d+),\s*BLOCKED:\s*(\d+)', re.M)
BEST = re.compile(r'^Best strategy:\s*(.+?)\s*$', re.M)


def parse_report(content: str, root: Path, report: Path | None = None) -> Selection:
    """Read upstream saved report (not colored terminal output)."""
    analytics = content.split('=== ANALYTICS ===')[-1]
    candidates = []
    for m in STANDARD.finditer(analytics):
        name, ok, err, unsup, ping, _fail = m.groups()
        candidates.append(Candidate(name.strip(), int(ok), int(err), int(unsup), int(ping)))
    for m in DPI.finditer(analytics):
        name, ok, fail, unsup, blocked = m.groups()
        candidates.append(Candidate(name.strip(), int(ok), int(fail), int(unsup), blocked=int(blocked)))
    match = BEST.search(analytics)
    best_name = match.group(1).strip() if match else ''
    # A strategy with zero working checks is not usable, even if upstream prints a name.
    if best_name and any(x.name == best_name and x.ok > 0 for x in candidates):
        best = root / best_name
        if best.parent != root or best.suffix.lower() != '.bat' or not best.is_file():
            raise ValueError('Upstream result references unknown BAT strategy: ' + best_name)
    else:
        best = None
    return Selection(best, tuple(candidates), report)


def make_unattended(source: str, test_type: str = 'dpi', run_mode: str = 'all', selected_names: tuple[str, ...] = ()) -> str:
    """Adapt prompts without changing the upstream test implementations."""
    if test_type not in ('standard', 'dpi'):
        raise ValueError('test_type must be standard or dpi')
    if run_mode not in ('all', 'select'):
        raise ValueError('run_mode must be all or select')
    if run_mode == 'select' and not selected_names:
        raise ValueError('Select mode requires at least one configuration')
    # Exact anchors for the inspected 1.10.3 version: fail closed if it changes.
    replacements = {
        '$testType = Read-TestType': "$testType = '" + test_type + "'",
        '$mode = Read-ModeSelection': "$mode = '" + run_mode + "'",
    }
    for old, new in replacements.items():
        if source.count(old) != 1:
            raise RuntimeError(f'Unsupported upstream tester: expected exactly one {old!r}')
        source = source.replace(old, new, 1)
    if run_mode == 'select':
        anchor = '$selected = Read-ConfigSelection -allFiles $batFiles'
        if source.count(anchor) != 1:
            raise RuntimeError('Unsupported upstream tester: config selection anchor changed')
        names = ', '.join("'" + name.replace("'", "''") + "'" for name in selected_names)
        selection_expr = (
            "$requestedNames = @(" + names + "); "
            "$selected = @($batFiles | Where-Object { $_.Name -in $requestedNames }); "
            "if ($selected.Count -ne $requestedNames.Count) { throw 'Some selected BAT configs were not found' }"
        )
        source = source.replace(anchor, selection_expr, 1)
    start = source.find('function Wait-AnyKey {')
    end = source.find('\ntrap {', start)
    if start < 0 or end < 0:
        raise RuntimeError('Unsupported upstream tester: Wait-AnyKey function not found')
    source = source[:start] + "function Wait-AnyKey { param([string]$message = '') Write-Host $message }\n" + source[end:]
    # Check that the script actually produces a text report before running it.
    if 'Best strategy: $bestConfig' not in source or 'test_results_$dateStr.txt' not in source:
        raise RuntimeError('Unsupported upstream tester: report format changed')
    return source


def choose_best(root: str | Path, *, test_type: str = 'dpi', run_mode: str = 'all', selected_names: tuple[str, ...] = (), timeout: int = 1800, cancellation: TestCancellation | None = None, progress: Callable[[str], None] | None = None) -> Selection:
    """Run upstream tester and select the name it declares best. Windows/Admin only."""
    if os.name != 'nt':
        raise OSError('The bundled PowerShell/winws tester runs only on Windows')
    root = Path(root).expanduser().resolve()
    source_path = root / 'utils' / 'test zapret.ps1'
    report_dir = root / 'utils' / 'test results'
    if not source_path.is_file() or not (root / 'bin' / 'winws.exe').is_file():
        raise FileNotFoundError('Expected utils/test zapret.ps1 and bin/winws.exe in --zapret folder')
    if not any(root.glob('general*.bat')):
        raise FileNotFoundError('No general*.bat strategies found')
    src = source_path.read_text(encoding='utf-8-sig')
    if run_mode == 'select':
        available = {p.name for p in root.glob('general*.bat')}
        if not selected_names or len(set(selected_names)) != len(selected_names) or not set(selected_names) <= available:
            raise ValueError('Selected configurations must be unique existing general*.bat files')
    unattended = make_unattended(src, test_type, run_mode, selected_names)
    if cancellation is not None and cancellation.event.is_set():
        raise TestCancelled('Проверка отменена пользователем')
    # The upstream script requires $PSScriptRoot == original utils directory.
    temp = root / 'utils' / ('_gui_test_' + uuid.uuid4().hex + '.ps1')
    temp.write_text(unattended, encoding='utf-8-sig')
    report_dir.mkdir(exist_ok=True)
    previous = {p.resolve(): p.stat().st_mtime_ns for p in report_dir.glob('test_results_*.txt')}
    try:
        # Read stdout incrementally. PowerShell's terminal output is shown in Qt
        # while the process runs; upstream's final report remains authoritative.
        print("[F03] Запуск штатного тестера")
        print(f"[F03] Режим: {test_type}, конфигурации: {run_mode}")
        print(f"[F03] Скрипт: {temp}")
        start_time = time.monotonic()
        process = subprocess.Popen(
            ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(temp)],
            cwd=str(root), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, bufsize=1, text=True, errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW,
            env={**os.environ, 'NO_UPDATE_CHECK': '1'},
        )
        if cancellation is not None:
            cancellation.register(process)
        lines = queue.Queue()
        tail = deque(maxlen=90)

        def read_output():
            try:
                for line in process.stdout:
                    elapsed = time.monotonic() - start_time
                    clean_line = line.rstrip("\r\n")

                    print(f"[{elapsed:7.1f}s] {clean_line}", flush=True)
                    lines.put(clean_line)
            finally:
                lines.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout
        reader_done = False
        try:
            while not reader_done:
                if cancellation is not None and cancellation.event.is_set():
                    raise TestCancelled('Проверка отменена пользователем')
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'F-03 превысил время ожидания {timeout} сек.')
                try:
                    line = lines.get(timeout=0.2)
                except queue.Empty:
                    continue
                if line is None:
                    reader_done = True
                else:
                    tail.append(line)
                    if progress is not None:
                        try:
                            progress(line)
                        except Exception:
                            # A broken UI callback must not abort the upstream tester.
                            pass
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
            if cancellation is not None and cancellation.event.is_set():
                raise TestCancelled('Проверка отменена пользователем')
        except (TestCancelled, TimeoutError, subprocess.TimeoutExpired):
            TestCancellation._stop_process(process)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
        finally:
            if cancellation is not None:
                cancellation.clear_process()
            if process.stdout:
                process.stdout.close()
        returncode = process.returncode
        reports = [p for p in report_dir.glob('test_results_*.txt')
                   if p.resolve() not in previous or p.stat().st_mtime_ns > previous[p.resolve()]]
        if returncode or not reports:
            tail_text = '\n'.join(tail)[-1800:]
            raise RuntimeError(f'Upstream test failed (exit {returncode}); report not saved or incomplete:\n{tail_text}')
        report = max(reports, key=lambda p: p.stat().st_mtime_ns)
        result = parse_report(report.read_text(encoding='utf-8-sig'), root, report)
        if not result.candidates:
            raise RuntimeError('Report has no analyzable candidates')
        if cancellation is not None and cancellation.event.is_set():
            raise TestCancelled('Проверка отменена пользователем')
        return result
    finally:
        temp.unlink(missing_ok=True)


def launch_selected(selection: Selection) -> subprocess.Popen:
    """Launch selected BAT as a foreground-style winws profile, NOT as a Windows service."""
    if os.name != 'nt':
        raise OSError('Windows only')
    if selection.best is None:
        raise RuntimeError('No successful strategy selected')
    return subprocess.Popen(['cmd.exe', '/d', '/c', str(selection.best)],
                            cwd=str(selection.best.parent),
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)


def main() -> None:
    parser = argparse.ArgumentParser(description='F-03: upstream zapret strategy auto selection')
    parser.add_argument('--zapret', default=str(latest_installation(Path(__file__).resolve().parent.parent / 'Zapret_dir')), help='Folder containing service.bat and utils/')
    parser.add_argument('--type', choices=('standard', 'dpi'), default='dpi')
    parser.add_argument('--mode', choices=('all', 'select'), default='all')
    parser.add_argument('--configs', nargs='*', default=[], help='BAT filenames for --mode select')
    parser.add_argument('--timeout', type=int, default=1800)
    parser.add_argument('--apply', action='store_true', help='Launch best BAT (NOT service installation)')
    parser.add_argument('--report', help='Offline: parse a saved upstream report instead of running anything')
    args = parser.parse_args()
    try:
        root = Path(args.zapret).expanduser().resolve()
        if args.report:
            report = Path(args.report).resolve()
            result = parse_report(report.read_text(encoding='utf-8-sig'), root, report)
        else:
            result = choose_best(root, test_type=args.type, run_mode=args.mode, selected_names=tuple(args.configs), timeout=args.timeout)
        for c in sorted(result.candidates, key=lambda x: (-x.ok, -x.ping_ok, x.errors)):
            print(f'{c.name}: OK={c.ok}; ERROR={c.errors}; UNSUP={c.unsupported}; PingOK={c.ping_ok}')
        print('BEST:', result.best.name if result.best else 'NONE')
        if result.report:
            print('REPORT:', result.report)
        if args.apply:
            process = launch_selected(result)
            print('Selected BAT launched, PID:', process.pid)
    except Exception as exc:
        parser.exit(1, 'F-03 error: ' + str(exc) + '\n')

# --- F-03: two-pass confirmation of three leading strategies ---
MAX_RATE_DRIFT = 0.15  # 15 percentage points, provisional safety threshold


@dataclass(frozen=True)
class Comparison:
    name: str
    first: Candidate
    second: Candidate
    first_rate: float
    second_rate: float
    delta_pp: float
    unstable: bool


@dataclass(frozen=True)
class VerifiedSelection:
    # Keep Selection-compatible attributes for existing GUI code.
    best: Path | None
    candidates: tuple[Candidate, ...]
    report: Path | None
    first: Selection
    second: Selection | None
    comparisons: tuple[Comparison, ...]
    safe_auto_apply: bool
    warning: str


def _check_total(candidate: Candidate) -> int:
    return candidate.ok + candidate.errors + candidate.unsupported + candidate.blocked


def _ok_rate(candidate: Candidate) -> float:
    total = _check_total(candidate)
    return candidate.ok / total if total else 0.0


def analyze_stability(first: Selection, second: Selection | None,
                      *, max_drift: float = MAX_RATE_DRIFT,
                      expected_names: tuple[str, ...] | None = None) -> VerifiedSelection:
    """Fail closed if a recheck is missing, contradictory, or materially changed."""
    if expected_names is None:
        ranked = sorted(first.candidates, key=lambda c: (-c.ok, c.errors, c.name.casefold()))
        expected_names = tuple(dict.fromkeys(
            ([first.best.name] if first.best else []) + [c.name for c in ranked]
        ))[:3]
    if first.best is None or second is None:
        return VerifiedSelection(first.best, first.candidates, first.report,
                                 first, second, (), False,
                                 'Нет победителя или повторный тест не завершён')
    original = {c.name: c for c in first.candidates}
    repeated = {c.name: c for c in second.candidates}
    warnings = []
    comparisons = []
    if len(expected_names) != 3:
        warnings.append('Недостаточно стратегий для проверки ТОП-3')
    if len(set(expected_names)) != len(expected_names):
        warnings.append('Повторный тест содержит повторяющиеся имена стратегий')
    for name in expected_names:
        a, b = original.get(name), repeated.get(name)
        if a is None or b is None or _check_total(a) == 0 or _check_total(b) == 0:
            warnings.append(f'Нет сопоставимых результатов: {name}')
            continue
        if _check_total(a) != _check_total(b):
            warnings.append(f'{name}: разное количество проверок ({_check_total(a)} / {_check_total(b)})')
        delta_pp = (_ok_rate(b) - _ok_rate(a)) * 100.0
        unstable = abs(delta_pp) >= max_drift * 100
        comparisons.append(Comparison(name, a, b, _ok_rate(a), _ok_rate(b), delta_pp, unstable))
        if unstable:
            warnings.append(f'{name}: изменение OK на {delta_pp:+.1f} п.п.')
    if len(comparisons) != len(expected_names):
        warnings.append('Не все три стратегии проверены повторно')
    if second.best is None or second.best.name != first.best.name:
        warnings.append('Победитель изменился после повторного теста')
    safe = not warnings and len(comparisons) == 3
    return VerifiedSelection(first.best, first.candidates, first.report,
                             first, second, tuple(comparisons), safe,
                             '; '.join(warnings) if warnings else 'Повторная проверка подтверждена')


def choose_best_verified(root: str | Path, *, test_type: str = 'dpi',
                         run_mode: str = 'all', selected_names: tuple[str, ...] = (),
                         timeout: int = 1800,
                         cancellation: TestCancellation | None = None,
                         progress: Callable[[str], None] | None = None) -> VerifiedSelection:
    """First pass as usual; second pass retests TOP-3 via upstream PowerShell tester."""
    def emit(line: str):
        if progress is not None:
            progress(line)

    emit('[F03] Этап 1/2: проверяем стратегии')
    first = choose_best(root, test_type=test_type, run_mode=run_mode,
                        selected_names=selected_names, timeout=timeout,
                        cancellation=cancellation, progress=progress)
    if first.best is None:
        result = analyze_stability(first, None)
        emit('[F03] ' + result.warning)
        return result

    ranked = sorted(first.candidates, key=lambda c: (-c.ok, c.errors, c.name.casefold()))
    names = tuple(dict.fromkeys([first.best.name] + [c.name for c in ranked]))[:3]
    if len(names) < 3:
        result = analyze_stability(first, None, expected_names=names)
        emit('[F03] ' + result.warning + '; автоприменение запрещено')
        return result

    if cancellation is not None and cancellation.event.is_set():
        raise TestCancelled('Проверка отменена до второго этапа')
    emit('[F03] Этап 2/2: повторяем ' + ', '.join(names))
    # Avoid same-second upstream report name collision and allow cancellation.
    if cancellation is not None:
        if cancellation.event.wait(1.2):
            raise TestCancelled('Проверка отменена до второго этапа')
    else:
        time.sleep(1.2)
    second = choose_best(root, test_type=test_type, run_mode='select',
                         selected_names=names, timeout=timeout,
                         cancellation=cancellation, progress=progress)
    result = analyze_stability(first, second, expected_names=names)
    if run_mode != 'all':
        from dataclasses import replace
        result = replace(result, safe_auto_apply=False,
                         warning=result.warning + '; неполный первый этап')
    for check in result.comparisons:
        emit(f'[F03] {check.name}: OK {check.first.ok} -> {check.second.ok}; '
             f'FAIL {check.first.errors} -> {check.second.errors}; '
             f'BLOCKED {check.first.blocked} -> {check.second.blocked}; '
             f'delta OK {check.delta_pp:+.1f} п.п.' +
             (' [НЕСТАБИЛЬНО]' if check.unstable else ''))
    emit('[F03] ' + ('Автоприменение разрешено: результаты подтверждены'
                     if result.safe_auto_apply
                     else 'Автоприменение ЗАПРЕЩЕНО: ' + result.warning))
    return result


if __name__ == '__main__':
    main()
