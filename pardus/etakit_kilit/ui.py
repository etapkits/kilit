"""Tam ekran kilit, karekod ve köşedeki Kilitle düğmesi."""

from __future__ import annotations

import logging
import math
import time

from etakit_kilit.app import consume_settings_request
from etakit_kilit.idle import idle_seconds
from etakit_kilit.limit import format_session_left
from etakit_kilit.session import BoardSession, View

log = logging.getLogger("etakit")


_CORNER_WIDTH = 90
_CORNER_HEIGHT = 36
_CORNER_TIME_WIDTH = 72


def corner_width(show_time: bool) -> int:
    if not show_time:
        return _CORNER_WIDTH
    return _CORNER_TIME_WIDTH + 8 + _CORNER_WIDTH


def corner_origin(
    work_x: int,
    work_y: int,
    work_width: int,
    work_height: int,
    width: int = _CORNER_WIDTH,
    height: int = _CORNER_HEIGHT,
) -> tuple[int, int]:
    """Kilitle düğmesini görev çubuğunun üstünde, sağ alta koyar."""
    margin = 8
    return (
        work_x + work_width - width - margin,
        work_y + work_height - height - margin,
    )


def qr_pixel_size(screen_height: int) -> int:
    """Karekod, ekran klavyesinin kapladığı alt bölgenin üstünde kalsın."""
    above = 250
    limit = int(screen_height * 0.60) - above
    return max(200, min(280, limit))

_CSS = """
window.lock {
    background-color: #0b1220;
}
window.banner {
    background-color: #7f1d1d;
}
window.corner {
    background-color: #0b1220;
}
label.title {
    font-size: 28pt;
    font-weight: 700;
    color: #e2e8f0;
}
label.name {
    font-size: 18pt;
    color: #94a3b8;
}
label.code {
    font-size: 22pt;
    font-weight: 700;
    color: #94a3b8;
}
label.hero {
    font-size: 64pt;
    font-weight: 700;
    color: #f8fafc;
}
label.quiet {
    font-size: 16pt;
    color: #94a3b8;
}
label.status {
    font-size: 20pt;
    color: #e2e8f0;
}
label.warn {
    font-size: 20pt;
    color: #fca5a5;
}
label.banner-text {
    font-size: 26pt;
    font-weight: 700;
    color: #fff7ed;
}
label.corner-time {
    color: #f8fafc;
    font-size: 10pt;
    font-weight: 700;
    padding: 0 4px;
}
button.corner-button {
    background-color: #0b1220;
    color: #f8fafc;
    font-size: 10pt;
    font-weight: 700;
    border-radius: 8px;
    border: 1px solid #38bdf8;
    padding: 7px 14px;
}
button.corner-button:hover {
    background-color: #1e293b;
}
label.pad-title {
    font-size: 28pt;
    font-weight: 700;
    color: #f8fafc;
}
label.pad-dots {
    font-size: 32pt;
    color: #e2e8f0;
}
label.pad-status {
    font-size: 16pt;
    color: #fca5a5;
}
button.pad-key {
    background-color: #1e293b;
    color: #f8fafc;
    font-size: 28pt;
    font-weight: 700;
    border-radius: 18px;
    min-width: 96px;
    min-height: 80px;
}
button.pad-key:hover {
    background-color: #334155;
}
"""


def run_ui(session: BoardSession, poke=None, on_settings=None) -> int:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk

    screen = _Screen(session, poke, on_settings, Gtk, Gdk, GLib)
    screen.start()
    Gtk.main()
    screen.release()
    return 0


class _Screen:
    def __init__(self, session: BoardSession, poke, on_settings, gtk, gdk, glib):
        self.session = session
        self.poke = poke or (lambda: None)
        self.on_settings = on_settings
        self.gtk = gtk
        self.gdk = gdk
        self.glib = glib
        self.grabbed = False
        self.qr_module = _load_qrcode()
        self.matrix_payload = ""
        self.matrix = None
        self.last_payload = ""
        self._press_source = None
        self._keypad_open = False
        self._pin_digits = ""
        self._settings_open = False
        self._logout_after = 0.0

        provider = gtk.CssProvider()
        provider.load_from_data(_CSS.encode("utf-8"))
        gtk.StyleContext.add_provider_for_screen(
            gdk.Screen.get_default(),
            provider,
            gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        self.lock_window = gtk.Window(type=gtk.WindowType.TOPLEVEL)
        self.lock_window.set_title("Etakit")
        self.lock_window.set_decorated(False)
        self.lock_window.set_keep_above(True)
        self.lock_window.set_skip_taskbar_hint(True)
        self.lock_window.set_skip_pager_hint(True)
        self.lock_window.set_accept_focus(True)
        _pin_above(self.lock_window)
        self.lock_window.get_style_context().add_class("lock")
        self.lock_window.connect("delete-event", self._refuse_close)
        self.lock_window.connect("key-press-event", self._swallow)
        self.lock_window.connect("key-release-event", self._swallow)

        box = gtk.Box(orientation=gtk.Orientation.VERTICAL, spacing=8)
        box.set_valign(gtk.Align.START)
        box.set_halign(gtk.Align.CENTER)
        box.set_margin_top(16)
        self.title = gtk.Label(label="Etakit")
        self.title.get_style_context().add_class("title")
        self.title_press = gtk.EventBox()
        self.title_press.set_visible_window(False)
        self.title_press.add(self.title)
        self.title_press.connect("button-press-event", self._title_pressed)
        self.title_press.connect("button-release-event", self._title_released)
        self.name = gtk.Label(label="")
        self.name.get_style_context().add_class("name")
        self.code_caption = gtk.Label(label="Tahta kodu")
        self.code_caption.get_style_context().add_class("quiet")
        self.code = gtk.Label(label="")
        self.code.get_style_context().add_class("code")
        self.qr = gtk.DrawingArea()
        screen = self.gdk.Screen.get_default()
        height = screen.get_height() if screen is not None else 1080
        qr_size = qr_pixel_size(height)
        self.qr.set_size_request(qr_size, qr_size)
        self.qr.connect("draw", self._draw_qr)
        self.status = gtk.Label(label="")
        self.status.set_line_wrap(True)
        self.status.set_max_width_chars(42)
        self.status.set_justify(gtk.Justification.CENTER)
        for widget in (
            self.title_press,
            self.name,
            self.code_caption,
            self.code,
            self.qr,
            self.status,
        ):
            box.pack_start(widget, False, False, 0)
        self.keypad = self._build_keypad(gtk)
        self.stack = gtk.Stack()
        self.stack.add_named(box, "lock")
        self.stack.add_named(self.keypad, "emergency")
        self.lock_window.add(self.stack)

        self.banner = gtk.Window(type=gtk.WindowType.TOPLEVEL)
        self.banner.set_decorated(False)
        self.banner.set_keep_above(True)
        self.banner.set_skip_taskbar_hint(True)
        self.banner.set_skip_pager_hint(True)
        self.banner.set_accept_focus(False)
        _pin_above(self.banner)
        self.banner.get_style_context().add_class("banner")
        self.banner.connect("delete-event", self._refuse_close)
        self.banner_label = gtk.Label(label="")
        self.banner_label.get_style_context().add_class("banner-text")
        self.banner_label.set_margin_top(18)
        self.banner_label.set_margin_bottom(18)
        self.banner.add(self.banner_label)

        self.corner = gtk.Window(type=gtk.WindowType.TOPLEVEL)
        self.corner.set_decorated(False)
        self.corner.set_keep_above(True)
        self.corner.set_skip_taskbar_hint(True)
        self.corner.set_skip_pager_hint(True)
        self.corner.set_accept_focus(False)
        _pin_above(self.corner)
        self.corner.get_style_context().add_class("corner")
        self.corner.connect("delete-event", self._refuse_close)
        self.button = gtk.Button(label="Kilitle")
        self.button.get_style_context().add_class("corner-button")
        self.button.set_size_request(_CORNER_WIDTH, _CORNER_HEIGHT)
        self.button.connect("clicked", self._on_lock_clicked)
        self.time_label = gtk.Label(label="")
        self.time_label.get_style_context().add_class("corner-time")
        self.time_label.set_size_request(_CORNER_TIME_WIDTH, _CORNER_HEIGHT)
        row = gtk.Box(orientation=gtk.Orientation.HORIZONTAL, spacing=8)
        row.pack_start(self.time_label, False, False, 0)
        row.pack_start(self.button, False, False, 0)
        self.corner.add(row)

    def start(self) -> None:
        import signal

        self.lock_window.show_all()
        self._cover_lock()
        self._apply(self.session.view())
        self.glib.timeout_add(200, self._tick)
        self.glib.idle_add(self._try_grab)
        try:
            for signum in (signal.SIGTERM, signal.SIGINT):
                self.glib.unix_signal_add(self.glib.PRIORITY_DEFAULT, signum, self._quit)
        except (AttributeError, TypeError):
            pass

    def release(self) -> None:
        self._ungrab()

    def _tick(self) -> bool:
        now = time.monotonic()
        if self.session.view().show_lock_button:
            idle = idle_seconds()
            if idle is not None:
                self.session.note_x_idle(idle, now)
        self.session.tick(now)
        if self.session.session_expired():
            self._logout_if_due(now)
        if self._settings_open:
            return True
        if self.session.view().grab:
            consume_settings_request()
        elif consume_settings_request():
            self._on_settings_clicked(None)
            if self._settings_open:
                return True
        view = self.session.view()
        if view.qr_payload and view.qr_payload != self.last_payload:
            self.poke()
        self.last_payload = view.qr_payload
        self._apply(view)
        if self._keypad_open:
            self._refresh_keypad()
        return True

    def _apply(self, view: View) -> None:
        if view.grab:
            if not self.lock_window.get_visible():
                self.lock_window.show()
                self.lock_window.present()
            self._cover_lock()
            if not self.grabbed:
                self.lock_window.present()
                self._try_grab()
            if self._keypad_open:
                self.stack.set_visible_child_name("emergency")
            else:
                self.stack.set_visible_child_name("lock")
        else:
            self._keypad_open = False
            self._ungrab()
            self.lock_window.hide()
            self._lock_bounds = None

        self._sync_lock_contents(view)

        if view.show_countdown:
            self.banner_label.set_text(view.countdown_text)
            if not self.banner.get_visible():
                self._place_banner()
                self.banner.show_all()
        else:
            self.banner.hide()

        if view.show_lock_button:
            text = format_session_left(view.session_left)
            self.time_label.set_text(text)
            show_time = text != ""
            if not self.corner.get_visible():
                self.corner.show_all()
            self.time_label.set_visible(show_time)
            self._place_corner(show_time)
        else:
            self.corner.hide()

    def _sync_lock_contents(self, view: View) -> None:
        self.title.show()
        self.name.set_text(view.board_name)
        self.name.set_visible(view.board_name != "")
        if view.board_name:
            self.name.get_style_context().add_class("hero")
            self.code.get_style_context().remove_class("hero")
        else:
            self.name.get_style_context().remove_class("hero")
            self.code.get_style_context().add_class("hero")
        self.code.set_text(view.device_code)
        show_code = view.device_code != "" and view.phase != "shutting_down"
        self.code.set_visible(show_code)
        self.code_caption.set_visible(show_code)
        self.status.set_text(view.message)
        self.status.show()
        context = self.status.get_style_context()
        context.remove_class("status")
        context.remove_class("warn")
        context.add_class("warn" if view.warn else "status")
        show_qr = view.qr_payload != ""
        self.qr.set_visible(show_qr)
        if show_qr:
            self._remember_matrix(view.qr_payload)
            self.qr.queue_draw()

    def _remember_matrix(self, payload: str) -> None:
        if payload == self.matrix_payload:
            return
        self.matrix_payload = payload
        self.matrix = None
        if self.qr_module is None:
            return
        qr = self.qr_module.QRCode(error_correction=self.qr_module.constants.ERROR_CORRECT_M, border=2, box_size=1)
        qr.add_data(payload)
        qr.make(fit=True)
        self.matrix = qr.get_matrix()

    def _draw_qr(self, widget, cr) -> bool:
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        size = min(width, height)
        cx = width / 2
        cy = height / 2
        radius = (size / 2) - 8
        ratio = self.session.view().qr_ratio
        cr.set_line_width(8)
        cr.set_source_rgb(0.20, 0.28, 0.40)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()
        if ratio > 0:
            cr.set_source_rgb(0.22, 0.74, 0.97)
            cr.arc(cx, cy, radius, -math.pi / 2, (-math.pi / 2) + (ratio * 2 * math.pi))
            cr.stroke()

        if not self.matrix:
            return False
        pad = 36
        box = size - (pad * 2)
        count = len(self.matrix)
        if count == 0 or box <= 0:
            return False
        cell = box / count
        origin_x = cx - (box / 2)
        origin_y = cy - (box / 2)
        cr.set_source_rgb(1, 1, 1)
        cr.rectangle(origin_x, origin_y, box, box)
        cr.fill()
        cr.set_source_rgb(0.04, 0.07, 0.12)
        for y, row in enumerate(self.matrix):
            for x, on in enumerate(row):
                if on:
                    cr.rectangle(origin_x + (x * cell), origin_y + (y * cell), cell + 0.4, cell + 0.4)
        cr.fill()
        return False

    def _on_settings_clicked(self, _button) -> None:
        if self.on_settings is None or self._settings_open:
            return
        self._settings_open = True
        self._ungrab()
        self.lock_window.hide()
        try:
            self.on_settings()
        finally:
            self._settings_open = False
            self._apply(self.session.view())

    def _on_lock_clicked(self, _button) -> None:
        self.session.user_lock(time.monotonic())
        self.poke()
        self._apply(self.session.view())

    def _build_keypad(self, gtk):
        box = gtk.Box(orientation=gtk.Orientation.VERTICAL, spacing=18)
        box.set_valign(gtk.Align.CENTER)
        box.set_halign(gtk.Align.CENTER)
        title = gtk.Label(label="Acil açılış")
        title.get_style_context().add_class("pad-title")
        self.pad_dots = gtk.Label(label="")
        self.pad_dots.get_style_context().add_class("pad-dots")
        self.pad_status = gtk.Label(label="")
        self.pad_status.set_line_wrap(True)
        self.pad_status.set_max_width_chars(36)
        self.pad_status.set_justify(gtk.Justification.CENTER)
        self.pad_status.get_style_context().add_class("pad-status")
        grid = gtk.Grid(column_spacing=12, row_spacing=12)
        grid.set_halign(gtk.Align.CENTER)
        labels = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "sil", "0", "tamam"]
        for index, label in enumerate(labels):
            button = gtk.Button(label=label)
            button.get_style_context().add_class("pad-key")
            button.set_size_request(110, 84)
            button.connect("clicked", self._pad_pressed, label)
            grid.attach(button, index % 3, index // 3, 1, 1)
            if label == "tamam":
                self.pad_confirm = button
        cancel = gtk.Button(label="Vazgeç")
        cancel.get_style_context().add_class("pad-key")
        cancel.connect("clicked", lambda *_args: self._close_keypad())
        for widget in (title, self.pad_dots, self.pad_status, grid, cancel):
            box.pack_start(widget, False, False, 0)
        return box

    def _title_pressed(self, *_args) -> bool:
        if self._press_source is not None or self._keypad_open:
            return True
        self._press_source = self.glib.timeout_add(3000, self._open_keypad)
        return True

    def _title_released(self, *_args) -> bool:
        if self._press_source is not None:
            self.glib.source_remove(self._press_source)
            self._press_source = None
        return True

    def _open_keypad(self) -> bool:
        self._press_source = None
        self._pin_digits = ""
        self._keypad_open = True
        self.pad_status.set_text("")
        self._refresh_keypad()
        if not self.session.config.emergency_pin_hash:
            self.pad_status.set_text("Acil parola tanımlı değil.")
        self.stack.set_visible_child_name("emergency")
        self.keypad.show_all()
        return False

    def _close_keypad(self) -> None:
        self._keypad_open = False
        self._pin_digits = ""
        self.stack.set_visible_child_name("lock")

    def _pad_pressed(self, _button, label: str) -> None:
        if label == "sil":
            self._pin_digits = self._pin_digits[:-1]
            self._refresh_keypad()
            return
        if label == "tamam":
            self._submit_pin()
            return
        if len(self._pin_digits) >= 8:
            return
        self._pin_digits += label
        self._refresh_keypad()

    def _submit_pin(self) -> None:
        result = self.session.try_emergency_pin(self._pin_digits, time.monotonic())
        self._pin_digits = ""
        if result == "ok":
            self._close_keypad()
            self.poke()
            self._apply(self.session.view())
            return
        if result == "unconfigured":
            self.pad_status.set_text("Acil parola tanımlı değil.")
        elif result == "denied":
            self.pad_status.set_text("Parola yanlış.")
        self._refresh_keypad()

    def _refresh_keypad(self) -> None:
        self.pad_dots.set_text("●" * len(self._pin_digits) if self._pin_digits else " ")
        left = self.session.emergency_lockout_left(time.monotonic())
        self.pad_confirm.set_sensitive(left == 0 and bool(self.session.config.emergency_pin_hash))
        if left > 0:
            self.pad_status.set_text(f"Çok fazla deneme. {left} saniye sonra yeniden deneyin.")

    def _cover_lock(self) -> None:
        bounds = _monitor_bounds(self.lock_window.get_display())
        if bounds is None:
            if not getattr(self, "_lock_fullscreen", False):
                self.lock_window.fullscreen()
                self._lock_fullscreen = True
            return
        if bounds == getattr(self, "_lock_bounds", None):
            return
        self._lock_bounds = bounds
        x, y, width, height = bounds
        self.lock_window.move(x, y)
        self.lock_window.resize(width, height)

    def _place_banner(self) -> None:
        bounds = _monitor_bounds(self.banner.get_display())
        if bounds is None:
            return
        x, y, width, _height = bounds
        self.banner.move(x, y)
        self.banner.resize(width, 96)

    def _place_corner(self, show_time: bool = False) -> None:
        display = self.corner.get_display()
        monitor = display.get_primary_monitor() if display is not None else None
        if monitor is None and display is not None and display.get_n_monitors() > 0:
            monitor = display.get_monitor(0)
        if monitor is None:
            return
        work = monitor.get_workarea() if hasattr(monitor, "get_workarea") else monitor.get_geometry()
        width = corner_width(show_time)
        self.corner.resize(width, _CORNER_HEIGHT)
        self.corner.move(*corner_origin(work.x, work.y, work.width, work.height, width, _CORNER_HEIGHT))

    def _logout_if_due(self, now: float) -> None:
        if now < self._logout_after:
            return
        self._logout_after = now + 2
        from etakit_kilit.power import logout_user

        logout_user()

    def _try_grab(self) -> bool:
        if not self.session.view().grab:
            return False
        window = self.lock_window.get_window()
        if window is None:
            return False
        seat = self.lock_window.get_display().get_default_seat()
        if seat is None:
            return False
        capabilities = self.gdk.SeatCapabilities.ALL
        try:
            status = seat.grab(window, capabilities, True, None, None, None)
        except TypeError:
            status = seat.grab(window, capabilities, True, None, None, None, None)
        self.grabbed = status == self.gdk.GrabStatus.SUCCESS
        if not self.grabbed:
            log.warning("klavye ve dokunmatik tutuşu alınamadı, yeniden denenecek")
        return False

    def _ungrab(self) -> None:
        if not self.grabbed:
            return
        display = self.lock_window.get_display()
        seat = display.get_default_seat() if display is not None else None
        if seat is not None:
            seat.ungrab()
        self.grabbed = False

    def _refuse_close(self, *_args) -> bool:
        return True

    def _swallow(self, *_args) -> bool:
        return True

    def _quit(self) -> bool:
        self.gtk.main_quit()
        return False


def _pin_above(window) -> None:
    stick = getattr(window, "stick", None)
    if stick is not None:
        stick()
    setter = getattr(window, "set_override_redirect", None)
    if setter is not None:
        setter(True)
        return

    def on_realize(widget):
        gdk_window = widget.get_window()
        if gdk_window is None:
            return
        gdk_setter = getattr(gdk_window, "set_override_redirect", None)
        if gdk_setter is not None:
            gdk_setter(True)

    window.connect("realize", on_realize)


def _monitor_bounds(display):
    if display is None:
        return None
    count = display.get_n_monitors()
    if count <= 0:
        return None
    min_x = min_y = 10**9
    max_x = max_y = -(10**9)
    for index in range(count):
        geom = display.get_monitor(index).get_geometry()
        min_x = min(min_x, geom.x)
        min_y = min(min_y, geom.y)
        max_x = max(max_x, geom.x + geom.width)
        max_y = max(max_y, geom.y + geom.height)
    return min_x, min_y, max_x - min_x, max_y - min_y


def _load_qrcode():
    try:
        import qrcode
    except ImportError:
        log.error("python3-qrcode yok; kilit açık kalır, karekod çizilmez")
        return None
    return qrcode
