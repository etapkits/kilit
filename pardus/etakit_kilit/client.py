"""Tahtanın sunucuya açtığı bağlantı. Tahtaya kapı açılmaz."""

from __future__ import annotations

import json
import os
import socket
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from etakit_kilit import __version__


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


_gai_lock = threading.Lock()
_original_getaddrinfo = socket.getaddrinfo


class DeviceClient:
    def __init__(self, server_url: str, verify_tls: bool = True):
        self.base = device_base(server_url)
        self.verify_tls = verify_tls
        self._ssl: ssl.SSLContext | None = None
        self._ssl_verified: bool | None = None

    def retarget(self, server_url: str, verify_tls: bool) -> None:
        self.base = device_base(server_url)
        self.verify_tls = verify_tls
        self._ssl = None
        self._ssl_verified = None

    def register(
        self,
        enrollment_key: str,
        machine_id: str,
        hostname: str,
        name: str = "",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "enrollment_key": enrollment_key,
            "machine_id": machine_id,
        }
        if hostname:
            body["hostname"] = hostname
        label = " ".join(name.split())
        if label:
            body["name"] = label
        return self._request("POST", "/register", None, body, timeout=15)

    def rename(self, token: str, name: str) -> dict[str, Any]:
        return self._request("POST", "/name", token, {"name": " ".join(name.split())}, timeout=15)

    def fetch_settings(self, token: str) -> dict[str, Any]:
        return self._request("GET", "/settings", token, None, timeout=8)

    def heartbeat(self, token: str, state: str) -> dict[str, Any]:
        return self._request("POST", "/heartbeat", token, {"state": state}, timeout=8)

    def report_qr(self, token: str, code: str) -> dict[str, Any]:
        return self._request("POST", "/qr", token, {"code": code}, timeout=8)

    def poll_commands(self, token: str, wait: int) -> dict[str, Any]:
        return self._request("GET", "/commands", token, None, timeout=max(8, wait + 10), query={"wait": wait})

    def ack(self, token: str, command_id: str) -> None:
        self._request("POST", f"/commands/{command_id}/ack", token, {}, timeout=8)

    def _request(
        self,
        method: str,
        path: str,
        token: str | None,
        body: dict[str, Any] | None,
        timeout: int,
        query: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        url = self.base + path
        if query:
            pairs = "&".join(f"{key}={value}" for key, value in query.items())
            url = f"{url}?{pairs}"

        data = None
        headers = {
            "Accept": "application/json",
            "User-Agent": f"etakit-kilit/{__version__}",
        }
        if token:
            headers["Authorization"] = "Bearer " + token
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        verify = self.verify_tls
        ipv4 = False
        while True:
            try:
                raw = self._read(request, timeout, verify, ipv4)
                break
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                raise ApiError(exc.code, _message(raw, exc.reason)) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if verify and url.startswith("https://") and _tls_failure(exc):
                    verify = False
                    self.verify_tls = False
                    self._ssl = None
                    continue
                if not ipv4 and _unreachable(exc):
                    ipv4 = True
                    continue
                raise

        if raw.strip() == "":
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError(502, "Sunucu yanıtı okunamadı.") from exc
        if not isinstance(parsed, dict):
            raise ApiError(502, "Sunucu yanıtı okunamadı.")
        return parsed

    def _read(self, request: urllib.request.Request, timeout: int, verify: bool, ipv4: bool) -> str:
        opener = urllib.request.build_opener(_proxy_handler(request.full_url))
        if request.full_url.startswith("https://"):
            opener.add_handler(urllib.request.HTTPSHandler(context=self._context(verify)))
        if not ipv4:
            with opener.open(request, timeout=timeout) as response:
                return response.read().decode("utf-8")
        with _gai_lock:
            socket.getaddrinfo = _ipv4_getaddrinfo
            try:
                with opener.open(request, timeout=timeout) as response:
                    return response.read().decode("utf-8")
            finally:
                socket.getaddrinfo = _original_getaddrinfo

    def _context(self, verify: bool) -> ssl.SSLContext:
        if self._ssl is None or self._ssl_verified != verify:
            if verify:
                self._ssl = ssl.create_default_context()
            else:
                self._ssl = ssl._create_unverified_context()
            self._ssl_verified = verify
        return self._ssl


def device_base(server_url: str) -> str:
    url = server_url.strip().rstrip("/")
    for suffix in ("/api/device/register", "/api/device"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    return url.rstrip("/") + "/api/device"


def _proxy_handler(url: str) -> urllib.request.ProxyHandler:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return urllib.request.ProxyHandler({})
    _load_system_proxy()
    return urllib.request.ProxyHandler(urllib.request.getproxies())


def _load_system_proxy() -> None:
    path = "/etc/environment"
    if not os.path.isfile(path):
        return
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.lower() not in {"http_proxy", "https_proxy", "no_proxy", "all_proxy"}:
            continue
        if key not in os.environ and key.upper() not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


def _failure_text(exc: BaseException) -> str:
    reason = getattr(exc, "reason", None)
    return str(reason if reason is not None else exc).lower()


def _tls_failure(exc: BaseException) -> bool:
    text = _failure_text(exc)
    return any(word in text for word in ("ssl", "certificate", "tls", "handshake", "eof", "protocol"))


def _unreachable(exc: BaseException) -> bool:
    text = _failure_text(exc)
    return any(
        word in text
        for word in ("network is unreachable", "no route to host", "errno 101", "errno 113")
    )


def _message(raw: str, fallback: object) -> str:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict) and parsed.get("message"):
        return str(parsed["message"])
    text = raw.strip()
    if text:
        return text
    return str(fallback or "Sunucu hatası")
