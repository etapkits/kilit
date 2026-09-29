"""Sunucu adresi kaydedilene kadar normal ayar penceresi."""

from __future__ import annotations

import re
import socket
import urllib.error

from etakit_kilit.client import ApiError, DeviceClient, device_base
from etakit_kilit.config import SYSTEM_CONFIG, Config, ConfigError, persist_config, replace_config
from etakit_kilit.store import StateStore

_NAME = re.compile(r"^[\w][\w ._/()-]{0,79}$")


def run_setup(
    config: Config,
    path: str = SYSTEM_CONFIG,
    store: StateStore | None = None,
) -> tuple[Config | None, dict | None]:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    gi.require_version("GLib", "2.0")
    from gi.repository import GLib

    Gtk.init([])
    screen = _Setup(config, path, store, Gtk)
    screen.loop = GLib.MainLoop()
    screen.window.show_all()
    screen.window.present()
    screen.loop.run()
    screen.window.destroy()
    return screen.result, screen.registration


class _Setup:
    def __init__(self, config: Config, path: str, store: StateStore | None, gtk):
        self.config = config
        self.path = path
        self.store = store if store is not None else StateStore(config.state_dir or "/var/lib/etakit")
        self.gtk = gtk
        self.result: Config | None = None
        self.registration: dict | None = None
        self.loop = None
        saved = self.store.load_device()
        saved_name = saved.name or config.board_name
        saved_code = saved.device_code or config.device_code

        self.window = gtk.Window(type=gtk.WindowType.TOPLEVEL)
        self.window.set_title("Etakit ayarları")
        self.window.set_default_size(640, 520)
        self.window.set_position(gtk.WindowPosition.CENTER)
        self.window.set_resizable(True)
        self.window.connect("delete-event", self._close)

        root = gtk.Box(orientation=gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_top(18)
        root.set_margin_bottom(18)
        root.set_margin_start(18)
        root.set_margin_end(18)

        title = gtk.Label()
        title.set_halign(gtk.Align.START)
        title.set_markup("<span size='x-large' weight='bold'>Etakit kurulumu</span>")
        hint = gtk.Label(
            label="Tahta adını yazın, örneğin 10-A. Kod sunucuda kendiliğinden verilir. Sunucu adresi kaydedilmeden kilit açılmaz."
        )
        hint.set_halign(gtk.Align.START)
        hint.set_line_wrap(True)
        hint.set_max_width_chars(52)

        self.server = gtk.Entry()
        self.server.set_placeholder_text("http://192.168.1.20/etakit/web/public")
        if not _placeholder(config.server_url):
            self.server.set_text(config.server_url)
        self.key = gtk.Entry()
        self.key.set_text(config.enrollment_key)
        self.board_name = gtk.Entry()
        self.board_name.set_max_length(80)
        self.board_name.set_text(saved_name)
        self.board_name.set_placeholder_text("10-A")
        self.code = gtk.Label(label=saved_code or "Sunucu verecek")
        self.code.set_halign(gtk.Align.START)
        self.code.set_selectable(True)
        self.tls = gtk.CheckButton(label="TLS sertifikasını doğrula")
        self.tls.set_active(config.tls_verify)
        self.idle = _minutes(gtk, config.idle_seconds, 1, 180)
        self.countdown = _seconds(gtk, config.lock_countdown_seconds, 0, 120)
        self.offline = _seconds(gtk, config.offline_grace_seconds, 5, 300)
        self.pin = gtk.Entry()
        self.pin.set_visibility(False)
        purpose = getattr(gtk, "InputPurpose", None)
        if purpose is not None:
            self.pin.set_input_purpose(purpose.PIN)
        if config.emergency_pin_hash:
            self.pin.set_placeholder_text("Kayıtlı parola korunur")
        else:
            self.pin.set_placeholder_text("4-8 rakam, isteğe bağlı")
        self.emergency_minutes = _minutes(gtk, config.emergency_seconds, 1, 60)

        grid = gtk.Grid(column_spacing=12, row_spacing=10)
        fields = (
            ("Tahta adı", self.board_name),
            ("Tahta kodu", self.code),
            ("Sunucu adresi", self.server),
            ("Kayıt anahtarı", self.key),
            ("Bağlantı", self.tls),
            ("Boşta kalınca kilitle (dakika)", self.idle),
            ("Kilit geri sayımı (saniye)", self.countdown),
            ("Bağlantı kopunca bekle (saniye)", self.offline),
            ("Acil parola", self.pin),
            ("Acil açılış süresi (dakika)", self.emergency_minutes),
        )
        for row, (label_text, widget) in enumerate(fields):
            label = gtk.Label(label=label_text)
            label.set_halign(gtk.Align.END)
            widget.set_hexpand(True)
            grid.attach(label, 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)

        self.error = gtk.Label(label="")
        self.error.set_halign(gtk.Align.START)
        self.error.set_line_wrap(True)
        self.error.set_max_width_chars(52)
        self.error.set_selectable(True)

        save = gtk.Button(label="Kaydet")
        save.get_style_context().add_class("suggested-action")
        save.connect("clicked", self._save)
        self.window.set_default(save)

        root.pack_start(title, False, False, 0)
        root.pack_start(hint, False, False, 0)
        root.pack_start(grid, True, True, 0)
        root.pack_start(self.error, False, False, 0)
        root.pack_start(save, False, False, 0)
        self.window.add(root)

    def _save(self, *_args) -> None:
        try:
            draft = self._draft()
            name = " ".join(self.board_name.get_text().split())
            current = " ".join((self.store.load_device().name or self.config.board_name).split())
            if name and name != current and _NAME.fullmatch(name) is None:
                self._show_error("Tahta adı geçersiz. Örnek: 10-A")
                return
            wanted = draft.server_url.strip().rstrip("/")
            server_changed = wanted != self.config.server_url.strip().rstrip("/") or (
                draft.enrollment_key.strip() != self.config.enrollment_key.strip()
            )
            if server_changed:
                self.store.clear_token()
            draft.board_name = name or current
            self._store_draft(draft)
            if name and name != current:
                self.registration = self._claim_name(draft, name)
                if self.registration is None:
                    return
                draft.device_code = str(self.registration.get("device_code") or draft.device_code)
            self._finish(draft)
        except ConfigError as exc:
            self._show_error(str(exc))
        except OSError as exc:
            self._show_error(f"Ayarlar kaydedilemedi: {exc}")
        except Exception as exc:
            self._show_error(f"Ayarlar kaydedilemedi: {exc}")

    def _store_draft(self, draft: Config) -> None:
        text = draft.render(self.pin.get_text().strip())
        try:
            persist_config(self.path, text)
        except ConfigError as exc:
            if "zaten kayıtlı" not in str(exc):
                raise
            replace_config(self.path, text)

    def _finish(self, draft: Config) -> None:
        loaded = Config.load(self.path, required=False)
        loaded.session_seconds = draft.session_seconds
        wanted = draft.server_url.strip().rstrip("/")
        if (
            loaded.server_url != wanted
            or loaded.enrollment_key != draft.enrollment_key.strip()
            or loaded.idle_seconds != draft.idle_seconds
            or loaded.tls_verify != draft.tls_verify
        ):
            self._show_error("Ayarlar kaydedilemedi")
            self.result = None
            return
        self.result = loaded if loaded.server_url else draft
        if self.result is None or self.result.needs_setup():
            self._show_error("Sunucu adresi gerekli")
            self.result = None
            return
        self._leave()

    def _show_error(self, text: str) -> None:
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.error.set_markup(f"<span color='#b91c1c' weight='bold'>{safe}</span>")

    def _draft(self) -> Config:
        return Config(
            server_url=self.server.get_text().strip(),
            enrollment_key=self.key.get_text().strip(),
            idle_seconds=int(self.idle.get_value()) * 60,
            lock_countdown_seconds=int(self.countdown.get_value()),
            offline_grace_seconds=int(self.offline.get_value()),
            heartbeat_seconds=self.config.heartbeat_seconds,
            command_wait_seconds=self.config.command_wait_seconds,
            tls_verify=self.tls.get_active(),
            state_dir=self.config.state_dir or "/var/lib/etakit",
            emergency_pin_hash=self.config.emergency_pin_hash,
            emergency_seconds=int(self.emergency_minutes.get_value()) * 60,
            session_seconds=self.config.session_seconds,
            emergency_max_attempts=self.config.emergency_max_attempts,
            emergency_lockout_seconds=self.config.emergency_lockout_seconds,
            device_code=self.config.device_code,
            board_name=self.config.board_name,
        )

    def _claim_name(self, draft: Config, name: str) -> dict | None:
        client = DeviceClient(draft.server_url, verify_tls=draft.tls_verify)
        saved = self.store.load_device()
        same_server = saved.token and self.store.token_belongs(draft.server_url) and device_base(
            draft.server_url
        ) == device_base(self.config.server_url)
        try:
            if same_server:
                result = client.rename(saved.token, name)
                result["device_token"] = saved.token
                result.setdefault("board_id", saved.board_id)
                result.setdefault("device_code", saved.device_code)
            else:
                result = client.register(
                    draft.enrollment_key,
                    self.store.machine_id(),
                    socket.gethostname().strip(),
                    name,
                )
        except ApiError as exc:
            if exc.status == 409:
                self._show_error("Bu tahta adı kullanımda.")
            else:
                self._show_error(exc.message or "Bu tahta adı doğrulanamadı.")
            return None
        except (urllib.error.URLError, TimeoutError, OSError):
            self._show_error("Bu tahta adı doğrulanamadı. Sunucuya ulaşılamıyor.")
            return None
        token = str(result.get("device_token") or "")
        if token == "":
            self._show_error("Bu tahta adı doğrulanamadı.")
            return None
        self.store.save_device(
            token,
            str(result.get("device_code") or saved.device_code),
            str(result.get("board_id") or saved.board_id),
            str(result.get("name") or name),
            draft.server_url,
        )
        return result

    def _close(self, *_args) -> bool:
        self.result = None
        self._leave()
        return True

    def _leave(self) -> None:
        self.window.hide()
        loop = self.loop
        if loop is not None and loop.is_running():
            loop.quit()


def _placeholder(url: str) -> bool:
    host = url.strip().rstrip("/").split("://")[-1].split("/")[0].split(":")[0].lower()
    return host == "etakit.okul.local"


def _minutes(gtk, seconds: int, low: int, high: int):
    spin = gtk.SpinButton.new_with_range(low, high, 1)
    spin.set_value(max(low, min(high, seconds // 60)))
    return spin


def _seconds(gtk, seconds: int, low: int, high: int):
    spin = gtk.SpinButton.new_with_range(low, high, 1)
    spin.set_value(max(low, min(high, seconds)))
    return spin
