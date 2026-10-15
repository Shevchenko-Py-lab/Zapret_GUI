"""F-08 network interface throughput and connection snapshots (psutil).

Counts per-interface OS traffic, NOT packets handled by winws / Zapret.
No packet capture. Poll from GUI QTimer/worker; no internal UI thread.
"""
from __future__ import annotations

from dataclasses import dataclass
import time

try:
    import psutil
except ImportError:
    psutil = None


@dataclass(frozen=True)
class TrafficSample:
    interface: str
    timestamp: float
    download_bps: float
    upload_bps: float
    received_bytes: int
    sent_bytes: int


class TrafficMonitor:
    def __init__(self, interface: str | None = None):
        if psutil is None:
            raise RuntimeError('Установите psutil: pip install psutil')
        self.interface = interface
        self._previous = None

    @staticmethod
    def interfaces() -> list[str]:
        if psutil is None:
            return []
        return sorted(psutil.net_io_counters(pernic=True).keys())

    def reset(self, interface: str | None = None):
        self.interface = interface
        self._previous = None

    def sample(self) -> TrafficSample:
        now = time.monotonic()
        counters = psutil.net_io_counters(pernic=True)
        if self.interface:
            if self.interface not in counters:
                raise ValueError(f'Сетевой интерфейс не найден: {self.interface}')
            value = counters[self.interface]
            name = self.interface
        else:
            value = psutil.net_io_counters(pernic=False)
            name = 'Все интерфейсы'
        recv, sent = value.bytes_recv, value.bytes_sent
        down = up = 0.0
        if self._previous is not None:
            before_t, before_recv, before_sent = self._previous
            dt = now - before_t
            if dt > 0:
                down = max(0, recv - before_recv) / dt
                up = max(0, sent - before_sent) / dt
        self._previous = (now, recv, sent)
        return TrafficSample(name, now, down, up, recv, sent)

    @staticmethod
    def connections(limit: int = 200) -> list[dict]:
        """Connection snapshot; may require admin privileges for process attribution."""
        if psutil is None:
            return []
        items = []
        try:
            conns = psutil.net_connections(kind='inet')
        except (psutil.AccessDenied, PermissionError):
            return []
        for c in conns[:max(0, limit)]:
            items.append({'pid': c.pid, 'local': _address(c.laddr),
                          'remote': _address(c.raddr), 'status': c.status})
        return items


def _address(a) -> str:
    if not a:
        return ''
    host, port = a[0], a[1]
    return f'[{host}]:{port}' if ':' in host else f'{host}:{port}'
