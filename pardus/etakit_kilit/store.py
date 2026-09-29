"""Tahta kimliği ve cihaz jetonu. Paket güncellenince aynı tahta kalır."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass

from etakit_kilit.client import device_base

_MACHINE_ID = re.compile(r"^[A-Za-z0-9._:-]{4,191}$")


@dataclass
class SavedDevice:
    token: str = ""
    device_code: str = ""
    board_id: str = ""
    name: str = ""
    server_url: str = ""


class StateStore:
    def __init__(self, state_dir: str):
        self.state_dir = state_dir

    def machine_id(self) -> str:
        path = self._path("machine_id")
        existing = self._read_text(path)
        if existing and _MACHINE_ID.fullmatch(existing):
            return existing

        machine_id = _system_machine_id() or uuid.uuid4().hex
        self._ensure_dir()
        self._write_text(path, machine_id + "\n")
        return machine_id

    def load_device(self) -> SavedDevice:
        path = self._path("device.json")
        if not os.path.isfile(path):
            return SavedDevice()
        try:
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return SavedDevice()
        if not isinstance(raw, dict):
            return SavedDevice()
        return SavedDevice(
            token=str(raw.get("device_token") or ""),
            device_code=str(raw.get("device_code") or ""),
            board_id=str(raw.get("board_id") or ""),
            name=str(raw.get("name") or ""),
            server_url=str(raw.get("server_url") or "").rstrip("/"),
        )

    def token_belongs(self, server_url: str) -> bool:
        saved = self.load_device()
        if saved.token == "":
            return False
        if saved.server_url == "":
            return True
        return device_base(saved.server_url) == device_base(server_url)

    def save_device(
        self,
        token: str,
        device_code: str,
        board_id: str,
        name: str,
        server_url: str | None = None,
    ) -> None:
        self._ensure_dir()
        previous = self.load_device()
        url = previous.server_url if server_url is None else server_url.strip().rstrip("/")
        payload = {
            "device_token": token,
            "device_code": device_code,
            "board_id": board_id,
            "name": name,
            "server_url": url,
        }
        path = self._path("device.json")
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _share(temporary)
        os.replace(temporary, path)
        _share(path)

    def clear_token(self) -> None:
        current = self.load_device()
        if current.token == "" and not os.path.isfile(self._path("device.json")):
            return
        self.save_device("", current.device_code, current.board_id, current.name)

    def _path(self, name: str) -> str:
        return os.path.join(self.state_dir, name)

    def _ensure_dir(self) -> None:
        os.makedirs(self.state_dir, mode=0o755, exist_ok=True)

    def _read_text(self, path: str) -> str:
        try:
            with open(path, encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return ""

    def _write_text(self, path: str, text: str) -> None:
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _share(temporary)
        os.replace(temporary, path)
        _share(path)


def _share(path: str) -> None:
    try:
        os.chmod(path, 0o666)
    except OSError:
        pass


def _system_machine_id() -> str:
    try:
        with open("/etc/machine-id", encoding="utf-8") as handle:
            value = handle.read().strip()
    except OSError:
        return ""
    if _MACHINE_ID.fullmatch(value):
        return value
    return ""
