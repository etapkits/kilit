"""Kayıt, durum, karekod ve komut yoklaması. Komut tahtanın açık tuttuğu bağlantıdan iner."""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import urllib.error

from etakit_kilit.client import ApiError, DeviceClient, device_base
from etakit_kilit.config import SYSTEM_CONFIG, Config, ConfigError, update_board_name
from etakit_kilit.policy import apply_server_policy, snapshot_policy
from etakit_kilit.power import poweroff as default_poweroff
from etakit_kilit.session import BoardSession
from etakit_kilit.store import StateStore

log = logging.getLogger("etakit")

# Sunucu onaylanmamış kapatma komutunu yeniden gönderir; tahta açılınca yine kapanmasın diye önce onay beklenir.
POWER_ACK_GRACE_SECONDS = 15.0


def _reach_reason(exc: BaseException) -> str:
    reason = getattr(exc, "reason", None)
    text = str(reason if reason is not None else exc).lower()
    if "certificate" in text or "ssl" in text:
        return "Sunucu sertifikası doğrulanamadı. Ayarlarda TLS doğrulamayı kapatıp kaydedin."
    if "getaddrinfo" in text or "name or service not known" in text or "nodename" in text:
        return "Sunucu adresi bulunamadı."
    if "timed out" in text or "timeout" in text:
        return "Sunucu yanıt vermedi."
    detail = str(reason if reason is not None else exc).strip().split("\n", 1)[0]
    if len(detail) > 80:
        detail = detail[:80]
    if detail:
        return f"Sunucuya ulaşılamıyor: {detail}"
    return "Sunucuya ulaşılamıyor."


def _http_reason(exc: ApiError) -> str:
    text = " ".join(exc.message.split())
    lowered = text.lower()
    if not text or text.startswith("<") or "html" in lowered[:40] or "sqlstate" in lowered or "base table" in lowered:
        return f"Sunucu hata döndürdü ({exc.status})."
    if len(text) > 140:
        text = text[:140]
    return text


class LinkWorker:
    def __init__(
        self,
        config: Config,
        session: BoardSession,
        store: StateStore,
        client: DeviceClient,
        poweroff=default_poweroff,
        clock=None,
        config_path: str | None = None,
    ):
        self.config = config
        self.config_path = config_path or os.environ.get("ETAKIT_CONFIG", SYSTEM_CONFIG)
        self.session = session
        self.store = store
        self.client = client
        self._poweroff = poweroff
        self.clock = clock or time.monotonic
        self._stop = threading.Event()
        self._poke = threading.Event()
        self._token_lock = threading.Lock()
        self.token = ""
        self._sent_state: str | None = None
        self._last_hb = 0.0
        self._last_policy = 0.0
        self._local_policy = snapshot_policy(config)
        self._last_power = 0.0
        self._power_since: float | None = None
        self._backoff = 1
        self._poll_backoff = 1
        self._threads: list[threading.Thread] = []
        saved = store.load_device()
        if saved.token and not store.token_belongs(config.server_url):
            store.clear_token()
            saved = store.load_device()
        if saved.token:
            self.token = saved.token
        session.on_token_loaded(saved.device_code, saved.name or config.board_name)

    def start(self) -> None:
        self._threads = [
            threading.Thread(target=self._reporter_loop, name="etakit-report", daemon=True),
            threading.Thread(target=self._command_loop, name="etakit-command", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._poke.set()
        for thread in self._threads:
            thread.join(timeout=2)

    def poke(self) -> None:
        self._poke.set()

    def retarget(self, config: Config) -> None:
        moved = device_base(config.server_url) != self.client.base or config.enrollment_key != self.config.enrollment_key
        self.config = config
        self.session.config = config
        self.client.retarget(config.server_url, config.tls_verify)
        if moved:
            self._drop_token()
            self._last_policy = 0
            self._backoff = 1
            self.poke()

    def flush_reports(self) -> bool:
        try:
            if not self._token():
                self._register()
            self._send_state()
            self._pull_policy()
            self._send_qr()
            self._send_acks()
        except ApiError as exc:
            if exc.status == 401 and self._token():
                log.warning("tahta jetonu reddedildi, yeniden kaydolunuyor")
                self._drop_token()
            elif exc.status == 401:
                log.warning("kayıt reddedildi: %s", exc.message)
                self.session.on_register_rejected(exc.message)
            else:
                log.warning("sunucu hatası: %s", exc.message)
                self.session.on_link_lost(self.clock(), _http_reason(exc))
            self._backoff = min(30, max(2, self._backoff * 2))
            return False
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log.warning("sunucuya ulaşılamıyor: %s", exc)
            self.session.on_link_lost(self.clock(), _reach_reason(exc))
            self._backoff = min(30, max(2, self._backoff * 2))
            return False
        finally:
            self._send_power()
        self._backoff = 1
        return True

    def poll_once(self) -> None:
        token = self._token()
        if not token:
            return
        wait = self.config.command_wait_seconds
        try:
            payload = self.client.poll_commands(token, wait)
        except ApiError as exc:
            if exc.status == 401:
                self._drop_token()
            else:
                self.session.on_link_lost(self.clock(), _http_reason(exc))
            self._poll_backoff = min(30, max(2, self._poll_backoff * 2))
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.session.on_link_lost(self.clock(), _reach_reason(exc))
            self._poll_backoff = min(30, max(2, self._poll_backoff * 2))
            raise
        self._poll_backoff = 1
        if not isinstance(payload, dict):
            payload = {}
        if "command" not in payload and "id" in payload and "type" in payload:
            command = payload
            payload = {}
        else:
            command = payload.get("command")
        name = str(payload.get("name") or "")
        self._keep_settings_name(name)
        self.session.on_link_ok(
            approval=str(payload.get("approval") or "") or None,
            device_code=str(payload.get("device_code") or "") or None,
            name=name or None,
        )
        if not isinstance(command, dict) or "id" not in command or "type" not in command:
            return
        command_id = str(command["id"])
        command_type = str(command["type"])
        log.info("komut %s %s", command_type, command_id)
        self.session.on_command(command_id, command_type, self.clock())
        self.poke()

    def _reporter_loop(self) -> None:
        while not self._stop.is_set():
            self._poke.clear()
            ok = self.flush_reports()
            delay = 1.0 if ok else float(self._backoff)
            self._poke.wait(delay)

    def _command_loop(self) -> None:
        while not self._stop.is_set():
            if not self._token():
                if self._stop.wait(1):
                    return
                continue
            try:
                self.poll_once()
            except (ApiError, urllib.error.URLError, TimeoutError, OSError):
                if self._stop.wait(self._poll_backoff):
                    return

    def _pull_policy(self) -> None:
        token = self._token()
        if not token:
            return
        if self._last_policy and (self.clock() - self._last_policy) < self.config.heartbeat_seconds:
            return
        try:
            result = self.client.fetch_settings(token)
        except (ApiError, urllib.error.URLError, TimeoutError, OSError) as exc:
            log.warning("kilit ayarı alınamadı, yerel ayar duruyor: %s", exc)
            self._last_policy = self.clock()
            return
        self._last_policy = self.clock()
        if not isinstance(result, dict):
            return
        apply_server_policy(self.config, self._local_policy, result)

    def _keep_settings_name(self, name: str) -> None:
        name = " ".join(name.split())
        if name == "" or name == self.config.board_name:
            return
        self.config.board_name = name
        try:
            update_board_name(self.config_path, name)
        except (ConfigError, OSError) as exc:
            log.warning("tahta adı ayarlara yazılamadı: %s", exc)

    def _register(self) -> None:
        self.client.retarget(self.config.server_url, self.config.tls_verify)
        if self._token() and not self.store.token_belongs(self.config.server_url):
            self._drop_token()
        hostname = socket.gethostname().strip()
        result = self.client.register(
            self.config.enrollment_key,
            self.store.machine_id(),
            hostname,
        )
        if str(result.get("device_token") or "") == "":
            raise ApiError(502, "Sunucu tahta jetonu vermedi.")
        self.apply_registration(result)
        log.info("tahta kaydoldu kod=%s onay=%s", result.get("device_code"), result.get("approval"))

    def apply_registration(self, result: dict) -> None:
        token = str(result.get("device_token") or "")
        if token == "":
            return
        device_code = str(result.get("device_code") or "")
        board_id = str(result.get("board_id") or "")
        name = str(result.get("name") or "")
        with self._token_lock:
            self.store.save_device(token, device_code, board_id, name, self.config.server_url)
            self.token = token
            self._sent_state = None
        self._keep_settings_name(name)
        self.session.on_registered(
            approval=str(result.get("approval") or "pending"),
            device_code=device_code,
            name=name,
            now=self.clock(),
        )

    def _send_state(self) -> None:
        token = self._token()
        if not token:
            return
        state = self.session.reported_state()
        due = state != self._sent_state or (self.clock() - self._last_hb) >= self.config.heartbeat_seconds
        if not due:
            return
        result = self.client.heartbeat(token, state)
        self._sent_state = state
        self._last_hb = self.clock()
        name = str(result.get("name") or "")
        code = str(result.get("device_code") or "")
        if name or code:
            saved = self.store.load_device()
            next_name = name or saved.name
            next_code = code or saved.device_code
            if next_name != saved.name or next_code != saved.device_code:
                self.store.save_device(token, next_code, saved.board_id, next_name, self.config.server_url)
        self._keep_settings_name(name)
        self.session.on_link_ok(
            approval=str(result.get("approval") or "") or None,
            device_code=code or None,
            name=name or None,
        )

    def _send_qr(self) -> None:
        payload = self.session.qr_to_report()
        token = self._token()
        if not payload or not token:
            return
        result = self.client.report_qr(token, payload)
        ttl = int(result.get("ttl_seconds") or 25)
        self.session.mark_qr_reported(payload, ttl)

    def _send_acks(self) -> None:
        token = self._token()
        if not token:
            return
        done: list[str] = []
        for command_id in self.session.peek_acks():
            self.client.ack(token, command_id)
            done.append(command_id)
        if done:
            self.session.drop_acks(done)

    def _send_power(self) -> None:
        if not self.session.wants_poweroff():
            self._power_since = None
            return
        now = self.clock()
        if self._power_since is None:
            self._power_since = now
        if self.session.peek_acks() and now - self._power_since < POWER_ACK_GRACE_SECONDS:
            return
        if now - self._last_power < 5:
            return
        self._last_power = now
        log.info("tahta kapatılıyor")
        if not self._poweroff():
            log.warning("kapatma komutu çalışmadı, yeniden denenecek")

    def _token(self) -> str:
        with self._token_lock:
            return self.token

    def _drop_token(self) -> None:
        with self._token_lock:
            self.token = ""
            self._sent_state = None
            self.store.clear_token()
        self.session.on_unauthorized()
