"""Okul paketindeki /etc/etakit/kilit.conf okuyucusu."""

from __future__ import annotations

import errno
import hashlib
import hmac
import os
import secrets
import subprocess
import tempfile
from dataclasses import dataclass

SYSTEM_CONFIG = "/etc/etakit/kilit.conf"


class ConfigError(Exception):
    pass


@dataclass
class Config:
    server_url: str
    enrollment_key: str
    idle_seconds: int = 600
    lock_countdown_seconds: int = 10
    offline_grace_seconds: int = 45
    heartbeat_seconds: int = 10
    command_wait_seconds: int = 20
    tls_verify: bool = True
    state_dir: str = "/var/lib/etakit"
    emergency_pin_hash: str = ""
    emergency_seconds: int = 300
    session_seconds: int = 0
    emergency_max_attempts: int = 5
    emergency_lockout_seconds: int = 300
    setup_complete: bool = False
    device_code: str = ""
    board_name: str = ""

    def is_ready(self) -> bool:
        return not _is_unset_server(self.server_url) and bool(self.enrollment_key.strip())

    def needs_setup(self) -> bool:
        return not self.is_ready()

    @classmethod
    def load(cls, path: str, *, required: bool = True) -> "Config":
        if not os.path.isfile(path):
            raise ConfigError(f"Ayar dosyası yok: {path}")

        values: dict[str, str] = {}
        with open(path, encoding="utf-8") as handle:
            for lineno, raw in enumerate(handle, 1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    raise ConfigError(f"{path}:{lineno} okunamadı")
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")

        server_url = values.get("server_url", "").rstrip("/")
        enrollment_key = values.get("enrollment_key", "")
        if server_url and not server_url.startswith(("http://", "https://")):
            raise ConfigError("server_url http:// veya https:// ile başlamalı")

        pin_hash = values.get("emergency_pin_hash", "")
        plain_pin = values.get("emergency_pin", "")
        if pin_hash:
            if not _valid_pin_hash(pin_hash):
                raise ConfigError("emergency_pin_hash geçersiz")
        elif plain_pin:
            pin_hash = hash_emergency_pin(plain_pin)

        config = cls(
            server_url=server_url,
            enrollment_key=enrollment_key,
            idle_seconds=_integer(values, "idle_seconds", 600, minimum=15),
            lock_countdown_seconds=_integer(values, "lock_countdown_seconds", 10, minimum=0),
            offline_grace_seconds=_integer(values, "offline_grace_seconds", 45, minimum=5),
            heartbeat_seconds=_integer(values, "heartbeat_seconds", 10, minimum=5),
            command_wait_seconds=_integer(values, "command_wait_seconds", 20, minimum=0),
            tls_verify=_boolean(values.get("tls_verify"), default=True),
            state_dir=values.get("state_dir") or "/var/lib/etakit",
            emergency_pin_hash=pin_hash,
            emergency_seconds=_integer(values, "emergency_seconds", 300, minimum=30),
            session_seconds=_integer(values, "session_seconds", 0, minimum=0),
            emergency_max_attempts=_integer(values, "emergency_max_attempts", 5, minimum=1),
            emergency_lockout_seconds=_integer(values, "emergency_lockout_seconds", 300, minimum=30),
            setup_complete=_boolean(values.get("setup_complete"), default=False),
            device_code=values.get("device_code", "").strip().upper(),
            board_name=" ".join(values.get("board_name", "").split()),
        )
        if required and not config.is_ready():
            raise ConfigError("server_url ve enrollment_key gerekli")
        return config

    def render(self, emergency_pin: str = "") -> str:
        url = self.server_url.strip().rstrip("/")
        key = self.enrollment_key.strip()
        if _is_unset_server(url):
            raise ConfigError("Sunucu adresi gerekli")
        if not url.startswith(("http://", "https://")):
            raise ConfigError("Sunucu adresi http:// veya https:// ile başlamalı")
        if not key:
            raise ConfigError("Kayıt anahtarı gerekli")
        pin_hash = self.emergency_pin_hash
        if emergency_pin:
            pin_hash = hash_emergency_pin(emergency_pin)
        if self.setup_complete and not pin_hash:
            raise ConfigError("Acil parola gerekli")
        lines = [
            "# Etakit kilit ayarları",
            f"server_url={url}",
            f"enrollment_key={key}",
            f"idle_seconds={self.idle_seconds}",
            f"lock_countdown_seconds={self.lock_countdown_seconds}",
            f"offline_grace_seconds={self.offline_grace_seconds}",
            f"heartbeat_seconds={self.heartbeat_seconds}",
            f"command_wait_seconds={self.command_wait_seconds}",
            f"tls_verify={'yes' if self.tls_verify else 'no'}",
            f"state_dir={self.state_dir or '/var/lib/etakit'}",
            f"emergency_seconds={self.emergency_seconds}",
            f"emergency_max_attempts={self.emergency_max_attempts}",
            f"emergency_lockout_seconds={self.emergency_lockout_seconds}",
            f"setup_complete={'yes' if self.setup_complete else 'no'}",
            f"device_code={self.device_code.strip().upper()}",
            f"board_name={' '.join(self.board_name.split())}",
        ]
        if pin_hash:
            lines.append(f"emergency_pin_hash={pin_hash}")
        return "\n".join(lines) + "\n"


def hash_emergency_pin(pin: str) -> str:
    if not pin.isdigit() or not 4 <= len(pin) <= 8:
        raise ConfigError("Acil parola 4 ile 8 rakam olmalı")
    salt = secrets.token_bytes(16)
    rounds = 100_000
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, rounds)
    return f"pbkdf2_sha256${rounds}${salt.hex()}${digest.hex()}"


def verify_emergency_pin(pin: str, stored: str) -> bool:
    if not _valid_pin_hash(stored) or not pin.isdigit():
        return False
    _kind, rounds_text, salt_hex, digest_hex = stored.split("$")
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        pin.encode("utf-8"),
        bytes.fromhex(salt_hex),
        int(rounds_text),
    )
    return hmac.compare_digest(digest, bytes.fromhex(digest_hex))


def _valid_pin_hash(stored: str) -> bool:
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        return False
    try:
        rounds = int(parts[1])
        bytes.fromhex(parts[2])
        bytes.fromhex(parts[3])
    except ValueError:
        return False
    return rounds > 0


def _integer(values: dict[str, str], key: str, default: int, minimum: int) -> int:
    raw = values.get(key, "")
    if raw == "":
        return default
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} sayı olmalı") from exc
    return max(minimum, parsed)


def _is_unset_server(url: str) -> bool:
    text = url.strip().rstrip("/")
    if text == "":
        return True
    if not text.startswith(("http://", "https://")):
        return True
    host = text.split("://", 1)[1].split("/")[0].split(":")[0].lower()
    return host == "etakit.okul.local"


def install_config_text(text: str, path: str, *, only_if_unconfigured: bool = False) -> None:
    with tempfile.TemporaryDirectory() as directory:
        sample = os.path.join(directory, "kilit.conf")
        with open(sample, "w", encoding="utf-8") as handle:
            handle.write(text)
        parsed = Config.load(sample)
    if only_if_unconfigured and os.path.isfile(path):
        current = _try_load(path)
        if current is not None and not current.needs_setup():
            raise ConfigError("Ayarlar zaten kayıtlı")
    _atomic_write(path, parsed.render())


def update_board_name(path: str, name: str) -> bool:
    """Ayarlardaki tahta adını günceller. Sunucu adresi ve kayıt anahtarı durur."""
    name = " ".join(name.split())
    if name == "" or not os.path.isfile(path):
        return False
    current = _try_load(path)
    if current is None or current.board_name == name:
        return False
    current.board_name = name
    try:
        text = current.render()
    except ConfigError:
        return False
    try:
        _atomic_write(path, text)
    except OSError as exc:
        if path != SYSTEM_CONFIG or exc.errno not in (errno.EACCES, errno.EPERM):
            raise
        _persist_board_name(name)
    return True


def update_connection(path: str, server_url: str, enrollment_key: str, tls_verify: bool) -> bool:
    """Kayıtlı ayarda sunucu adresini, kayıt anahtarını ve TLS seçimini değiştirir."""
    if not os.path.isfile(path):
        return False
    current = _try_load(path)
    if current is None:
        raise ConfigError("Ayar dosyası okunamadı")
    url = server_url.strip().rstrip("/")
    key = enrollment_key.strip()
    if _is_unset_server(url):
        raise ConfigError("Sunucu adresi gerekli")
    if not url.startswith(("http://", "https://")):
        raise ConfigError("Sunucu adresi http:// veya https:// ile başlamalı")
    if not key:
        raise ConfigError("Kayıt anahtarı gerekli")
    if current.server_url == url and current.enrollment_key == key and current.tls_verify == tls_verify:
        return False
    current.server_url = url
    current.enrollment_key = key
    current.tls_verify = tls_verify
    text = current.render()
    try:
        _atomic_write(path, text)
    except OSError as exc:
        if path != SYSTEM_CONFIG or exc.errno not in (errno.EACCES, errno.EPERM):
            raise
        _persist_connection(url, key, tls_verify)
    return True


def replace_config(path: str, text: str) -> None:
    """Kayıtlı ayarın tamamını, Kaydet düğmesindeki değerlerle değiştirir."""
    try:
        install_config_text(text, path, only_if_unconfigured=False)
        return
    except OSError as exc:
        if path != SYSTEM_CONFIG or exc.errno not in (errno.EACCES, errno.EPERM):
            raise
    _persist_replace(text)


def persist_config(path: str, text: str) -> None:
    try:
        install_config_text(text, path, only_if_unconfigured=True)
        return
    except OSError as exc:
        if path != SYSTEM_CONFIG or exc.errno not in (errno.EACCES, errno.EPERM):
            raise
    _persist_privileged(text)


def _try_load(path: str) -> Config | None:
    try:
        return Config.load(path, required=False)
    except ConfigError:
        return None


def _atomic_write(path: str, text: str) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    try:
        os.makedirs(directory, mode=0o755, exist_ok=True)
    except OSError as exc:
        if exc.errno not in (errno.EACCES, errno.EPERM, errno.EEXIST):
            raise
    try:
        fd, tmp = tempfile.mkstemp(prefix=".kilit.", dir=directory)
    except OSError as exc:
        if exc.errno not in (errno.EACCES, errno.EPERM):
            raise
        _overwrite(path, text)
        return
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp, 0o666)
        except OSError:
            pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _share_config(path)


def _overwrite(path: str, text: str) -> None:
    """Dizin yazılamıyorsa, kullanıcının yazabildiği ayar dosyasının üstüne yazar."""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    _share_config(path)


def _share_config(path: str) -> None:
    try:
        os.chmod(path, 0o666)
    except OSError:
        pass


def _persist_board_name(name: str) -> None:
    helper = os.environ.get("ETAKIT_SAVE_HELPER", "/usr/libexec/etakit-kilit-save-config")
    if not os.path.isfile(helper):
        raise ConfigError("Tahta adı ayarlara yazılamadı")
    fd, temp_path = tempfile.mkstemp(prefix="etakit-kilit-", suffix=".name")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(name)
        result = subprocess.run(
            ["pkexec", helper, "--board-name", temp_path],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ConfigError(detail or "Tahta adı ayarlara yazılamadı")


def _persist_connection(server_url: str, enrollment_key: str, tls_verify: bool) -> None:
    helper = os.environ.get("ETAKIT_SAVE_HELPER", "/usr/libexec/etakit-kilit-save-config")
    if not os.path.isfile(helper):
        raise ConfigError("Sunucu adresi ayarlara yazılamadı")
    fd, temp_path = tempfile.mkstemp(prefix="etakit-kilit-", suffix=".server")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"server_url={server_url}\n")
            handle.write(f"enrollment_key={enrollment_key}\n")
            handle.write(f"tls_verify={'yes' if tls_verify else 'no'}\n")
        result = subprocess.run(
            ["pkexec", helper, "--connection", temp_path],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ConfigError(detail or "Sunucu adresi ayarlara yazılamadı")


def _persist_replace(text: str) -> None:
    helper = os.environ.get("ETAKIT_SAVE_HELPER", "/usr/libexec/etakit-kilit-save-config")
    if not os.path.isfile(helper):
        raise ConfigError("Ayarlar kaydedilemedi")
    fd, temp_path = tempfile.mkstemp(prefix="etakit-kilit-", suffix=".conf")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            pass
        result = subprocess.run(
            ["pkexec", helper, "--replace", temp_path],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ConfigError(detail or "Ayarlar kaydedilemedi")


def _persist_privileged(text: str) -> None:
    helper = os.environ.get("ETAKIT_SAVE_HELPER", "/usr/libexec/etakit-kilit-save-config")
    if not os.path.isfile(helper):
        raise ConfigError("Ayar dosyasına yazma izni yok")
    fd, temp_path = tempfile.mkstemp(prefix="etakit-kilit-", suffix=".conf")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            pass
        result = subprocess.run(
            ["pkexec", helper, temp_path],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ConfigError(detail or "Ayarlar kaydedilemedi")


def _boolean(raw: str | None, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "yes", "true", "evet", "on"}:
        return True
    if normalized in {"0", "no", "false", "hayir", "hayır", "off"}:
        return False
    raise ConfigError("tls_verify yes veya no olmalı")
