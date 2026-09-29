"""Kapatma ve oturum sonlandırma. Etkin oturum polkit kuralıyla parola sormadan kapanır.
Kullanıcı servisi oturuma bağlı sayılmazsa polkit reddeder; o zaman sudoers ile izinli yardımcı çalışır."""

from __future__ import annotations

import logging
import os
import subprocess

log = logging.getLogger("etakit")

POWEROFF_HELPER = "/usr/libexec/etakit-kilit-poweroff"


def logout_user(run=None, environ: dict[str, str] | None = None) -> bool:
    """Grafik oturumu kapatır."""
    env = os.environ if environ is None else environ
    runner = run or subprocess.run
    commands = [["xfce4-session-logout", "--logout", "--fast"]]
    session_id = str(env.get("XDG_SESSION_ID") or "").strip()
    if session_id:
        commands.append(["loginctl", "terminate-session", session_id])
    uid = str(env.get("UID") or "").strip()
    if not uid:
        getuid = getattr(os, "getuid", None)
        if getuid is not None:
            try:
                uid = str(getuid())
            except OSError:
                uid = ""
    if uid:
        commands.append(["loginctl", "terminate-user", uid])
    for command in commands:
        try:
            result = runner(command, capture_output=True, timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if getattr(result, "returncode", 1) == 0:
            return True
    return False


def poweroff(run=None, helper: str = POWEROFF_HELPER) -> bool:
    runner = run or subprocess.run
    commands = [["systemctl", "poweroff"], ["loginctl", "poweroff"]]
    if os.path.exists(helper):
        commands.append(["sudo", "-n", helper])
    for command in commands:
        name = " ".join(command)
        try:
            result = runner(command, capture_output=True, text=True, timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("%s çalıştırılamadı: %s", name, exc)
            continue
        if getattr(result, "returncode", 1) == 0:
            log.info("%s kabul edildi", name)
            return True
        detail = " ".join(str(getattr(result, "stderr", "") or "").split())[:300]
        log.warning("%s reddedildi (%s): %s", name, getattr(result, "returncode", "?"), detail or "açıklama yok")
    return False
