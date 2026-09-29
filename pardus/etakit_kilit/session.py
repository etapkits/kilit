"""Kilit, geri sayım, boşta kalma ve sunucu kopması."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from enum import Enum

from etakit_kilit.config import Config
from etakit_kilit.config import verify_emergency_pin
from etakit_kilit.limit import remaining_seconds
from etakit_kilit.payload import make_qr_payload


class Phase(str, Enum):
    LOCKED = "locked"
    UNLOCKED = "unlocked"
    COUNTDOWN = "countdown"
    SHUTTING_DOWN = "shutting_down"


class Approval(str, Enum):
    UNKNOWN = "unknown"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class View:
    phase: str
    grab: bool
    show_lock_button: bool
    show_countdown: bool
    device_code: str
    board_name: str
    qr_payload: str
    qr_ratio: float
    message: str
    warn: bool
    countdown_text: str
    session_left: int | None = None


class BoardSession:
    def __init__(
        self,
        config: Config,
        now: float,
        config_ok: bool = True,
        login_at: float = 0,
        unix_clock=None,
        on_login_restart=None,
    ):
        self.config = config
        self.login_at = login_at
        self.unix_clock = unix_clock or time.time
        self.on_login_restart = on_login_restart
        self.lock = threading.RLock()
        self.now = now
        self.config_ok = config_ok
        self.config_error = ""
        self.phase = Phase.LOCKED
        self.approval = Approval.UNKNOWN
        self.online = False
        self.device_code = ""
        self.board_name = ""
        self.register_error = ""
        self.link_error = ""
        self.qr_payload = ""
        self.qr_started = now
        self.qr_rotate_after = 20.0
        self._qr_needs_report = False
        self.offline_since: float | None = None
        self.countdown_until: float | None = None
        self._lock_ack: str | None = None
        self._acks: list[str] = []
        self._poweroff = False
        self.last_input = now
        self._idle_anchor: float | None = None
        self._saw_idle = False
        self._emergency_failures = 0
        self._emergency_lockout_until: float | None = None
        self._emergency_until: float | None = None

    def set_config_error(self, message: str) -> None:
        with self.lock:
            self.config_ok = False
            self.config_error = message

    def tick(self, now: float) -> None:
        with self.lock:
            self.now = now
            self._expire_offline()
            self._maybe_finish_countdown()
            self._maybe_rotate_qr()
            self._maybe_finish_emergency()
            self._maybe_idle()

    def note_x_idle(self, idle_seconds: float, now: float) -> None:
        """X boşta sayacı kilitliyken de büyür. Açılınca bu süre sıfırlanır."""
        with self.lock:
            self.now = now
            if self.phase != Phase.UNLOCKED:
                self._idle_anchor = None
                return
            self._saw_idle = True
            if self._idle_anchor is None:
                self._idle_anchor = idle_seconds
                self.last_input = now
                return
            if idle_seconds + 0.05 < self._idle_anchor:
                self.last_input = now - max(0.0, idle_seconds)
                self._idle_anchor = 0.0
                return
            self.last_input = now - max(0.0, idle_seconds - self._idle_anchor)

    def on_token_loaded(self, device_code: str, name: str = "") -> None:
        with self.lock:
            if device_code:
                self.device_code = device_code
            if name:
                self.board_name = name

    def on_registered(self, approval: str, device_code: str, name: str, now: float) -> None:
        with self.lock:
            self.now = now
            self.online = True
            self.offline_since = None
            self.register_error = ""
            self.link_error = ""
            self._set_approval(approval)
            if device_code:
                self.device_code = device_code
            if name:
                self.board_name = name
            self._ensure_qr()

    def on_link_ok(
        self,
        approval: str | None = None,
        device_code: str | None = None,
        name: str | None = None,
    ) -> None:
        with self.lock:
            self.online = True
            self.offline_since = None
            self.register_error = ""
            self.link_error = ""
            if device_code:
                self.device_code = device_code
            if name:
                self.board_name = name
            if approval:
                self._set_approval(approval)
            self._ensure_qr()

    def on_link_lost(self, now: float | None = None, reason: str = "") -> None:
        with self.lock:
            if now is not None:
                self.now = now
            if self.online or self.offline_since is None:
                self.offline_since = self.now
            self.online = False
            if reason:
                self.link_error = reason
            elif not self.link_error:
                self.link_error = "Sunucuya ulaşılamıyor."

    def on_unauthorized(self) -> None:
        with self.lock:
            self.online = False
            if self.offline_since is None:
                self.offline_since = self.now
            self.approval = Approval.UNKNOWN

    def on_register_rejected(self, message: str) -> None:
        with self.lock:
            self.online = False
            if self.offline_since is None:
                self.offline_since = self.now
            self.register_error = message or "Kayıt anahtarı geçersiz."

    def on_command(self, command_id: str, command_type: str, now: float) -> None:
        with self.lock:
            self.now = now
            self.online = True
            self.offline_since = None
            if self.approval != Approval.APPROVED:
                self._acks.append(command_id)
                return
            if command_type == "unlock":
                self._unlock(command_id)
            elif command_type == "lock":
                self._request_lock(command_id)
            elif command_type == "shutdown":
                self._shutdown(command_id)
            else:
                self._acks.append(command_id)

    def user_lock(self, now: float) -> None:
        with self.lock:
            self.now = now
            if self.phase == Phase.SHUTTING_DOWN or self.phase == Phase.LOCKED:
                return
            ack = self._lock_ack
            self._lock_ack = None
            self._engage_lock(ack)

    def try_emergency_pin(self, pin: str, now: float) -> str:
        with self.lock:
            self.now = now
            if not self.config.emergency_pin_hash:
                return "unconfigured"
            if self.emergency_lockout_left(now) > 0:
                return "locked"
            self._emergency_lockout_until = None
            if not verify_emergency_pin(pin, self.config.emergency_pin_hash):
                self._emergency_failures += 1
                if self._emergency_failures >= self.config.emergency_max_attempts:
                    self._emergency_failures = 0
                    self._emergency_lockout_until = now + self.config.emergency_lockout_seconds
                    return "locked"
                return "denied"
            self._emergency_failures = 0
            self._unlock("")
            self._emergency_until = now + self.config.emergency_seconds
            self.offline_since = now
            return "ok"

    def emergency_lockout_left(self, now: float) -> int:
        with self.lock:
            if self._emergency_lockout_until is None:
                return 0
            return max(0, math.ceil(self._emergency_lockout_until - now))

    def reported_state(self) -> str:
        with self.lock:
            if self.phase == Phase.SHUTTING_DOWN:
                return "shutting_down"
            if self.phase == Phase.LOCKED:
                return "locked"
            return "unlocked"

    def qr_to_report(self) -> str:
        with self.lock:
            if self._qr_needs_report and self._qr_allowed():
                return self.qr_payload
            return ""

    def mark_qr_reported(self, payload: str, ttl_seconds: int) -> None:
        with self.lock:
            if payload != self.qr_payload:
                return
            self._qr_needs_report = False
            self.qr_rotate_after = max(8.0, float(ttl_seconds) - 5.0)

    def peek_acks(self) -> list[str]:
        with self.lock:
            return list(self._acks)

    def drop_acks(self, command_ids: list[str]) -> None:
        with self.lock:
            done = set(command_ids)
            self._acks = [command_id for command_id in self._acks if command_id not in done]

    def wants_poweroff(self) -> bool:
        with self.lock:
            return self._poweroff

    def session_expired(self) -> bool:
        with self.lock:
            return remaining_seconds(self.config.session_seconds, self.login_at, self.unix_clock()) == 0

    def view(self) -> View:
        with self.lock:
            payload, ratio = self._visible_qr()
            countdown_text = ""
            if self.phase == Phase.COUNTDOWN and self.countdown_until is not None:
                left = max(0, math.ceil(self.countdown_until - self.now))
                countdown_text = f"Tahta {left} saniye içinde kilitlenecek."
            message, warn = self._message()
            return View(
                phase=self.phase.value,
                grab=self.phase in (Phase.LOCKED, Phase.SHUTTING_DOWN),
                show_lock_button=self.phase == Phase.UNLOCKED,
                show_countdown=self.phase == Phase.COUNTDOWN,
                device_code=self.device_code,
                board_name=self.board_name,
                qr_payload=payload,
                qr_ratio=ratio,
                message=message,
                warn=warn,
                countdown_text=countdown_text,
                session_left=remaining_seconds(self.config.session_seconds, self.login_at, self.unix_clock()),
            )

    def _set_approval(self, approval: str) -> None:
        try:
            parsed = Approval(approval)
        except ValueError:
            return
        self.approval = parsed
        if self._emergency_active():
            return
        if parsed != Approval.APPROVED and self.phase in (Phase.UNLOCKED, Phase.COUNTDOWN):
            ack = self._lock_ack
            self._lock_ack = None
            self._engage_lock(ack)

    def _message(self) -> tuple[str, bool]:
        if not self.config_ok:
            return (self.config_error or "Kurulum eksik. /etc/etakit/kilit.conf dosyasını düzenleyin.", True)
        if self.phase == Phase.SHUTTING_DOWN:
            return ("Tahta kapanıyor.", False)
        if self.register_error and self.approval == Approval.UNKNOWN:
            return (self.register_error, True)
        if self.approval == Approval.UNKNOWN:
            if self.link_error:
                return (self.link_error, True)
            return ("Sunucuya bağlanılıyor.", False)
        if not self.online:
            if self.link_error:
                return (self.link_error, True)
            return ("Sunucuya ulaşılamıyor. Tahta kilitli kalır.", True)
        if self.approval == Approval.PENDING:
            return ("Yönetici onayı bekleniyor.", False)
        if self.approval == Approval.REJECTED:
            return ("Bu tahta reddedildi.", True)
        return ("Kilidi açmak için karekodu okutun.", False)

    def _visible_qr(self) -> tuple[str, float]:
        if not self._qr_allowed() or not self.qr_payload:
            return "", 0.0
        span = self.qr_rotate_after if self.qr_rotate_after > 0 else 1.0
        elapsed = max(0.0, self.now - self.qr_started)
        ratio = max(0.0, min(1.0, 1.0 - (elapsed / span)))
        return self.qr_payload, ratio

    def _qr_allowed(self) -> bool:
        return self.phase == Phase.LOCKED and self.approval == Approval.APPROVED and self.device_code != ""

    def _ensure_qr(self) -> None:
        if self._qr_allowed() and not self.qr_payload:
            self._new_qr()

    def _maybe_rotate_qr(self) -> None:
        if not self._qr_allowed():
            return
        if not self.qr_payload or (self.now - self.qr_started) >= self.qr_rotate_after:
            self._new_qr()

    def _new_qr(self) -> None:
        if not self._qr_allowed():
            return
        try:
            self.qr_payload = make_qr_payload(self.device_code)
        except ValueError:
            return
        self.qr_started = self.now
        self._qr_needs_report = True

    def _emergency_active(self) -> bool:
        return (
            self.phase == Phase.UNLOCKED
            and self._emergency_until is not None
            and self.now < self._emergency_until
        )

    def _expire_offline(self) -> None:
        if self._emergency_active():
            return
        if self.online or self.offline_since is None:
            return
        if self.phase != Phase.UNLOCKED:
            return
        if self.now - self.offline_since >= self.config.offline_grace_seconds:
            self._engage_lock(None)

    def _maybe_finish_countdown(self) -> None:
        if self.phase != Phase.COUNTDOWN or self.countdown_until is None:
            return
        if self.now >= self.countdown_until:
            ack = self._lock_ack
            self._lock_ack = None
            self._engage_lock(ack)

    def _maybe_finish_emergency(self) -> None:
        if self._emergency_until is None or self.phase != Phase.UNLOCKED:
            return
        if self.now >= self._emergency_until:
            self._emergency_until = None
            self._engage_lock(None)

    def _maybe_idle(self) -> None:
        if self._emergency_active():
            return
        if self.phase != Phase.UNLOCKED or not self._saw_idle:
            return
        if self.now - self.last_input >= self.config.idle_seconds:
            self._engage_lock(None)

    def _request_lock(self, command_id: str) -> None:
        if self.phase == Phase.SHUTTING_DOWN:
            self._acks.append(command_id)
            return
        if self.phase == Phase.LOCKED:
            self._acks.append(command_id)
            return
        if self.config.lock_countdown_seconds <= 0:
            self._engage_lock(command_id)
            return
        if self._lock_ack:
            self._acks.append(self._lock_ack)
        self._lock_ack = command_id
        self.phase = Phase.COUNTDOWN
        self.countdown_until = self.now + self.config.lock_countdown_seconds

    def _unlock(self, command_id: str) -> None:
        if self.phase == Phase.SHUTTING_DOWN:
            self._acks.append(command_id)
            return
        if self._lock_ack:
            self._acks.append(self._lock_ack)
        self.phase = Phase.UNLOCKED
        self.countdown_until = None
        self._emergency_until = None
        self._lock_ack = None
        self.qr_payload = ""
        self._qr_needs_report = False
        if command_id:
            self._acks.append(command_id)
        self.last_input = self.now
        self._idle_anchor = None
        self._saw_idle = False
        self.login_at = self.unix_clock()
        if self.on_login_restart:
            try:
                self.on_login_restart(self.login_at)
            except OSError:
                pass

    def _shutdown(self, command_id: str) -> None:
        if self._lock_ack:
            self._acks.append(self._lock_ack)
        self.phase = Phase.SHUTTING_DOWN
        self.countdown_until = None
        self._lock_ack = None
        self.qr_payload = ""
        self._qr_needs_report = False
        self._acks.append(command_id)
        self._poweroff = True

    def _engage_lock(self, ack_id: str | None) -> None:
        self.phase = Phase.LOCKED
        self.countdown_until = None
        self._emergency_until = None
        self._lock_ack = None
        self._idle_anchor = None
        self.last_input = self.now
        self.qr_payload = ""
        self._qr_needs_report = False
        if ack_id:
            self._acks.append(ack_id)
        self._new_qr()
