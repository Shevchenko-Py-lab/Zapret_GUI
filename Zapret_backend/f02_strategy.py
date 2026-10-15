"""F-02. List and test user-supplied strategies. Does not change stored settings."""
from __future__ import annotations
import argparse
from dataclasses import dataclass
from pathlib import Path
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from .f01_power import PowerController
from .version_store import latest_installation


@dataclass(frozen=True)
class TestResult:
    name: str
    successes: int
    total: int
    elapsed_seconds: float
    errors: tuple[str, ...]


def strategies_in(folder: str | Path) -> list[Path]:
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    return sorted(p for p in root.iterdir()
                  if p.is_file() and p.name.lower().startswith('general') and p.suffix.lower() == '.bat')


def probe(url: str, timeout: float = 6.0) -> tuple[bool, str]:
    try:
        request = Request(url, headers={'User-Agent': 'ZapretControlTest/0.1', 'Cache-Control': 'no-cache'})
        with urlopen(request, timeout=timeout) as response:
            # HTTP success is evidence of reachability only, not proof of correct page/content.
            return 200 <= response.status < 400, f'HTTP {response.status}'
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return False, str(exc)


def test_strategy(start_file: str | Path, stop_file: str | Path | None,
                  urls: list[str], warmup: float = 2.0, timeout: float = 6.0) -> TestResult:
    if not urls:
        raise ValueError('Нужен хотя бы один проверяемый URL')
    controller = PowerController(start_file, stop_file)
    errors = []
    successes = 0
    started = time.monotonic()
    controller.start()
    try:
        time.sleep(warmup)
        for url in urls:
            ok, detail = probe(url, timeout)
            successes += int(ok)
            if not ok:
                errors.append(f'{url}: {detail}')
    finally:
        controller.stop()
    return TestResult(Path(start_file).stem, successes, len(urls),
                      round(time.monotonic() - started, 3), tuple(errors))


def main() -> None:
    parser = argparse.ArgumentParser(description='F-02: тестирование стратегий')
    parser.add_argument('--strategies', default=str(latest_installation(Path(__file__).resolve().parent.parent / 'Zapert_dir')), help='Каталог со стартовыми файлами')
    parser.add_argument('--stop', required=True, help='Команда корректной остановки zapret между тестами')
    parser.add_argument('--url', action='append', required=True, help='URL для проверки (можно повторять)')
    parser.add_argument('--warmup', type=float, default=2)
    args = parser.parse_args()
    for path in strategies_in(args.strategies):
        try:
            result = test_strategy(path, args.stop, args.url, args.warmup)
            print(f'{result.name}: {result.successes}/{result.total}, {result.elapsed_seconds:.2f}s')
            for error in result.errors:
                print('  ✗', error)
        except Exception as exc:
            print(f'{path.name}: ОШИБКА {exc}')


if __name__ == '__main__':
    main()
