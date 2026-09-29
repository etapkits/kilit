"""Sunucu kilit ayarı. Kayıt yoksa yerel ayar durur; tahta bu ayarları sunucuya yazmaz."""

from __future__ import annotations

from etakit_kilit.config import Config

_FIELDS = (
    "idle_seconds",
    "lock_countdown_seconds",
    "offline_grace_seconds",
    "emergency_seconds",
    "session_seconds",
    "emergency_pin_hash",
)


def snapshot_policy(config: Config) -> dict[str, int | str]:
    return {name: getattr(config, name) for name in _FIELDS}


def apply_server_policy(config: Config, baseline: dict[str, int | str], payload: dict) -> bool:
    """Kayıtlı sunucu ayarını canlı ayarın üzerine yazar. Kayıt yoksa yerel haline döner."""
    source = payload if payload.get("configured") else baseline
    changed = False
    for name in _FIELDS:
        value = _field(name, source.get(name, baseline[name]))
        if getattr(config, name) != value:
            setattr(config, name, value)
            changed = True
    return changed


def _field(name: str, value: int | str) -> int | str:
    if name == "emergency_pin_hash":
        text = str(value or "")
        if text.startswith("pbkdf2_sha256$"):
            return text
        return ""
    number = int(value)
    if name == "idle_seconds":
        return max(15, min(180 * 60, number))
    if name == "lock_countdown_seconds":
        return max(0, min(120, number))
    if name == "offline_grace_seconds":
        return max(5, min(300, number))
    if name == "session_seconds":
        return max(0, min(360 * 60, number))
    return max(30, min(3600, number))
