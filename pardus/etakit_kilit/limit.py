"""Oturum süresi. Son kilit açılışından sayar; tahta bu süreyi sunucuya yazmaz."""

from __future__ import annotations

import math
import os


def runtime_dir() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or ""
    if not runtime:
        getuid = getattr(os, "getuid", None)
        uid = getuid() if getuid else 0
        runtime = f"/tmp/etakit-{uid}"
    os.makedirs(runtime, mode=0o700, exist_ok=True)
    return runtime


def remember_login(directory: str, session_id: str, now: float) -> float:
    """Kilit programı aynı grafik oturumda yeniden başlasa da sayaç baştan başlamaz."""
    path = os.path.join(directory, "etakit-login")
    marker = session_id.strip()
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        if len(lines) >= 2 and lines[0] == marker:
            return float(lines[1])
    except (OSError, ValueError):
        pass
    return restart_login(directory, session_id, now)


def restart_login(directory: str, session_id: str, now: float) -> float:
    """Kilit her açıldığında sayaç baştan başlar."""
    os.makedirs(directory, mode=0o700, exist_ok=True)
    with open(os.path.join(directory, "etakit-login"), "w", encoding="utf-8") as handle:
        handle.write(f"{session_id.strip()}\n{now}\n")
    return now


def remaining_seconds(limit: int, started: float, now: float) -> int | None:
    if limit <= 0 or started <= 0:
        return None
    left = limit - (now - started)
    if left <= 0:
        return 0
    return math.ceil(left)


def format_session_left(seconds: int | None) -> str:
    if seconds is None:
        return ""
    if seconds >= 60:
        return f"{seconds // 60} dk"
    return f"{seconds} sn"
