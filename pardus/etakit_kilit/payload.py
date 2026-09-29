"""Karekod metni. Sunucu biçimi: etakit:1:<tahta kodu>:<tek kullanımlık>."""

from __future__ import annotations

import re
import secrets

_PAYLOAD = re.compile(r"^[A-Za-z0-9:._~-]{16,512}$")


def make_qr_payload(device_code: str) -> str:
    payload = f"etakit:1:{device_code}:{secrets.token_hex(16)}"
    if _PAYLOAD.fullmatch(payload) is None:
        raise ValueError("Karekod üretilemedi.")
    return payload
