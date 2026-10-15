"""F-05: Control an EXISTING official Cloudflare WARP Windows installation.

No installation, service manipulation, registration or elevated permissions are
performed by this module. The official warp-cli is the source of truth.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum


class WarpState(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    DISCONNECTING = "disconnecting"
    UNKNOWN = "unknown"
    NOT_INSTALLED = "not_installed"


@dataclass(frozen=True)
class WarpStatus:
    state: WarpState
    details: str = ""


class WarpError(RuntimeError):
    pass


def parse_warp_status(output: str) -> WarpStatus:
    """Parse warp-cli versions reporting either 'Status update:' or 'Status:'."""
    match = re.search(r"(?im)^\s*(?:status update|status)\s*:\s*([^\r\n]+)", output)
    if not match:
        return WarpStatus(WarpState.UNKNOWN, output)

    value = match.group(1).strip().lower()
    if value.startswith("disconnected"):
        state = WarpState.DISCONNECTED
    elif value.startswith("disconnecting"):
        state = WarpState.DISCONNECTING
    elif value.startswith("connected"):
        state = WarpState.CONNECTED
    elif value.startswith(("connecting", "reconnecting", "initializing")):
        state = WarpState.CONNECTING
    else:
        state = WarpState.UNKNOWN
    return WarpStatus(state, output)


class WarpController:
    def __init__(self, cli: str | Path | None = None):
        self.cli = Path(cli) if cli else self.find_cli()

    @staticmethod
    def find_cli() -> Path | None:
        candidates = []
        for env in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env)
            if base:
                candidates.append(Path(base) / "Cloudflare" / "Cloudflare WARP" / "warp-cli.exe")
        which = shutil.which("warp-cli.exe") or shutil.which("warp-cli")
        if which:
            candidates.insert(0, Path(which))
        return next((p for p in candidates if p.is_file()), None)

    def _run(self, *args: str, timeout: int = 30) -> str:
        if not self.cli or not self.cli.is_file():
            raise WarpError("Cloudflare WARP не установлен или warp-cli.exe не найден")
        try:
            result = subprocess.run(
                [str(self.cli), *args],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise WarpError(f"warp-cli {' '.join(args)}: превышено время ожидания") from exc
        except OSError as exc:
            raise WarpError(f"Не удалось запустить warp-cli: {exc}") from exc
        output = "\n".join(x.strip() for x in (result.stdout, result.stderr) if x.strip())
        if result.returncode != 0:
            raise WarpError(f"warp-cli {' '.join(args)} завершился с кодом {result.returncode}: {output}")
        return output

    def status(self) -> WarpStatus:
        if not self.cli or not self.cli.is_file():
            return WarpStatus(WarpState.NOT_INSTALLED, "warp-cli.exe не найден")
        try:
            output = self._run("status", timeout=12)
        except WarpError as exc:
            return WarpStatus(WarpState.UNKNOWN, str(exc))
        return parse_warp_status(output)

    def connect(self) -> WarpStatus:
        self._run("connect", timeout=30)
        return self.status()

    def disconnect(self) -> WarpStatus:
        self._run("disconnect", timeout=30)
        return self.status()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Manage installed Cloudflare WARP")
    parser.add_argument("command", choices=("status", "connect", "disconnect"), nargs="?", default="status")
    args = parser.parse_args()
    manager = WarpController()
    try:
        result = getattr(manager, args.command)()
        print(f"WARP: {result.state.value}\n{result.details}")
    except WarpError as exc:
        parser.exit(1, str(exc) + "\n")
