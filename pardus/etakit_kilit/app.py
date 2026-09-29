"""Grafik oturumda tek kilit süreci."""

from __future__ import annotations

import logging
import os
import sys
import time

from etakit_kilit.client import DeviceClient
from etakit_kilit.config import Config, ConfigError
from etakit_kilit.limit import remember_login, restart_login, runtime_dir
from etakit_kilit.link import LinkWorker
from etakit_kilit.session import BoardSession
from etakit_kilit.store import StateStore

log = logging.getLogger("etakit")
_CONFIG_PATH = os.environ.get("ETAKIT_CONFIG", "/etc/etakit/kilit.conf")
_GREETER_USERS = frozenset({"lightdm", "gdm", "gdm3", "debian-gdm", "sddm", "lxdm"})


def is_user_session(environ: dict[str, str] | None = None, uid: int | None = None) -> bool:
    """Giriş ekranı (greeter) değil, açılmış kullanıcı oturumu."""
    env = os.environ if environ is None else environ
    session_class = env.get("XDG_SESSION_CLASS", "").strip().lower()
    if session_class == "greeter":
        return False
    user = (env.get("USER") or env.get("LOGNAME") or "").strip()
    if user in _GREETER_USERS:
        return False
    if session_class == "user":
        return True
    if uid is None:
        getuid = getattr(os, "getuid", None)
        uid = getuid() if getuid is not None else 1000
    return uid >= 1000


def settings_request_path(runtime: str | None = None) -> str:
    if not runtime:
        runtime = os.environ.get("XDG_RUNTIME_DIR") or ""
        if not runtime:
            getuid = getattr(os, "getuid", None)
            uid = getuid() if getuid else 0
            runtime = f"/tmp/etakit-{uid}"
    os.makedirs(runtime, mode=0o700, exist_ok=True)
    return os.path.join(runtime, "etakit-kilit.open-settings")


def write_settings_request(runtime: str | None = None) -> str:
    path = settings_request_path(runtime)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("1\n")
    return path


def consume_settings_request(runtime: str | None = None) -> bool:
    path = settings_request_path(runtime)
    try:
        os.remove(path)
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return True


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if "--settings" in sys.argv[1:]:
        return _open_settings_command()
    if not is_user_session():
        log.info("kullanıcı oturumu yok, kilit açılmadı")
        return 0
    hold = _hold_single_instance()
    if hold is None:
        log.info("kilit zaten çalışıyor")
        return 0

    config = _open_config()
    store = StateStore(config.state_dir or "/var/lib/etakit")
    opened = _ask_setup(config, store) if config.needs_setup() else (config, None)
    if opened is None:
        return 1
    config, registration = opened
    if config.needs_setup():
        log.info("sunucu adresi yok, kilit başlamadı")
        return 0
    return _serve(config, store, hold, registration)


def _open_settings_command() -> int:
    if not is_user_session():
        log.info("kullanıcı oturumu yok, ayarlar açılmadı")
        return 0
    hold = _hold_single_instance()
    if hold is None:
        write_settings_request()
        log.info("çalışan kilit ayarları açacak")
        return 0
    config = _open_config()
    store = StateStore(config.state_dir or "/var/lib/etakit")
    opened = _ask_setup(config, store)
    if opened is None:
        del hold
        return 1
    config, registration = opened
    if config is None or config.needs_setup():
        log.info("sunucu adresi yok, kilit başlamadı")
        del hold
        return 0
    return _serve(config, store, hold, registration)


def _ask_setup(config: Config, store: StateStore):
    try:
        from etakit_kilit.settings import run_setup

        updated, registration = run_setup(config, _CONFIG_PATH, store)
    except Exception:
        log.exception("ayar penceresi açılamadı")
        return None
    if updated is None:
        reloaded = _open_config()
        if reloaded.server_url and reloaded.server_url != config.server_url:
            return reloaded, None
        return config, None
    return updated, registration


def _serve(config: Config, store: StateStore, hold, registration) -> int:
    login_dir = runtime_dir()
    login_id = os.environ.get("XDG_SESSION_ID", "")
    login_at = remember_login(login_dir, login_id, time.time())
    session = BoardSession(
        config,
        time.monotonic(),
        config_ok=True,
        login_at=login_at,
        unix_clock=time.time,
        on_login_restart=lambda now: restart_login(login_dir, login_id, now),
    )
    client = DeviceClient(config.server_url, verify_tls=config.tls_verify)
    worker = LinkWorker(config, session, store, client)
    worker.start()
    if registration:
        worker.apply_registration(registration)

    def on_settings() -> None:
        nonlocal config
        opened = _ask_setup(config, store)
        if opened is None:
            return
        updated, saved = opened
        if updated is None or updated.needs_setup():
            return
        config = updated
        worker.retarget(config)
        if saved:
            worker.apply_registration(saved)

    try:
        from etakit_kilit.ui import run_ui
    except Exception:
        log.exception("ekran açılamadı")
        worker.stop()
        return 1

    try:
        return run_ui(session, poke=worker.poke, on_settings=on_settings)
    finally:
        worker.stop()
        del hold


def _open_config() -> Config:
    if not os.path.isfile(_CONFIG_PATH):
        return Config(server_url="", enrollment_key="")
    try:
        return Config.load(_CONFIG_PATH, required=False)
    except ConfigError as exc:
        log.error("%s", exc)
        return Config(server_url="", enrollment_key="")


def _hold_single_instance():
    import fcntl

    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        runtime = f"/tmp/etakit-{os.getuid()}"
        os.makedirs(runtime, mode=0o700, exist_ok=True)
    path = os.path.join(runtime, "etakit-kilit.lock")
    handle = open(path, "w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


if __name__ == "__main__":
    sys.exit(main())
