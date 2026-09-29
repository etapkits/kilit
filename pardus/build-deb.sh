#!/bin/sh
# Pardus üzerinde okulun .deb paketini üretir.
# Kullanım:
# Sunucu adresi verilmezse tahta ilk açılışta ayar penceresinden sorar.
#   ./build-deb.sh --enrollment-key ANAHTAR
#   ./build-deb.sh --server-url http://192.168.1.20/etakit/web/public --enrollment-key ANAHTAR --emergency-pin 123456
set -eu

cd "$(dirname "$0")"

server_url=""
enrollment_key=""
emergency_pin=""
while [ $# -gt 0 ]; do
    case "$1" in
        --server-url)
            server_url=$2
            shift 2
            ;;
        --enrollment-key)
            enrollment_key=$2
            shift 2
            ;;
        --emergency-pin)
            emergency_pin=$2
            shift 2
            ;;
        *)
            echo "Bilinmeyen argüman: $1" >&2
            echo "Kullanım: ./build-deb.sh [--server-url ADRES] [--enrollment-key ANAHTAR] [--emergency-pin 123456]" >&2
            exit 1
            ;;
    esac
done

version=$(sed -n 's/^Version: //p' packaging/control)
package=$(sed -n 's/^Package: //p' packaging/control)
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

install -d "$stage/DEBIAN" \
    "$stage/usr/bin" \
    "$stage/usr/lib/etakit-kilit" \
    "$stage/usr/libexec" \
    "$stage/usr/lib/systemd/user" \
    "$stage/etc/xdg/autostart" \
    "$stage/etc/etakit" \
    "$stage/etc/X11/xorg.conf.d" \
    "$stage/etc/polkit-1/rules.d" \
    "$stage/etc/polkit-1/localauthority/50-local.d" \
    "$stage/etc/sudoers.d" \
    "$stage/usr/share/polkit-1/actions" \
    "$stage/usr/share/applications" \
    "$stage/etc/lightdm/lightdm.conf.d"

install -m 0755 bin/etakit-kilit "$stage/usr/bin/etakit-kilit"
install -m 0755 packaging/etakit-kilit-start "$stage/usr/libexec/etakit-kilit-start"
install -m 0755 packaging/etakit-kilit-session "$stage/usr/libexec/etakit-kilit-session"
ln -s etakit-kilit-session "$stage/usr/libexec/etakit-kilit-session-setup"
ln -s etakit-kilit-session "$stage/usr/libexec/etakit-kilit-session-cleanup"
ln -s etakit-kilit-session "$stage/usr/libexec/etakit-kilit-session-greeter"
install -m 0644 packaging/50-etakit-lightdm.conf "$stage/etc/lightdm/lightdm.conf.d/50-etakit-lightdm.conf"
install -m 0755 packaging/etakit-kilit-save-config "$stage/usr/libexec/etakit-kilit-save-config"
install -m 0755 packaging/etakit-kilit-poweroff "$stage/usr/libexec/etakit-kilit-poweroff"
install -m 0440 packaging/etakit-kilit.sudoers "$stage/etc/sudoers.d/etakit-kilit"
install -m 0644 packaging/49-etakit-poweroff.pkla "$stage/etc/polkit-1/localauthority/50-local.d/49-etakit-poweroff.pkla"
install -m 0644 packaging/org.etakit.kilit.policy "$stage/usr/share/polkit-1/actions/org.etakit.kilit.policy"
cp -a etakit_kilit "$stage/usr/lib/etakit-kilit/etakit_kilit"
find "$stage/usr/lib/etakit-kilit" -type d -name '__pycache__' -exec rm -rf {} +
install -m 0644 packaging/etakit-kilit.service "$stage/usr/lib/systemd/user/etakit-kilit.service"
install -m 0644 packaging/etakit-kilit.desktop "$stage/etc/xdg/autostart/etakit-kilit.desktop"
install -m 0644 packaging/etakit-kilit-settings.desktop "$stage/usr/share/applications/etakit-kilit-settings.desktop"
install -m 0644 packaging/50-etakit-kilit.conf "$stage/etc/X11/xorg.conf.d/50-etakit-kilit.conf"
install -m 0644 packaging/49-etakit-poweroff.rules "$stage/etc/polkit-1/rules.d/49-etakit-poweroff.rules"
install -m 0644 packaging/50-etakit-save-config.rules "$stage/etc/polkit-1/rules.d/50-etakit-save-config.rules"
install -m 0644 packaging/control "$stage/DEBIAN/control"
install -m 0755 packaging/postinst "$stage/DEBIAN/postinst"
install -m 0755 packaging/prerm "$stage/DEBIAN/prerm"
install -m 0755 packaging/postrm "$stage/DEBIAN/postrm"
install -m 0644 packaging/conffiles "$stage/DEBIAN/conffiles"

ETAKIT_STAGE="$stage" \
ETAKIT_SERVER_URL="$server_url" \
ETAKIT_ENROLLMENT_KEY="$enrollment_key" \
ETAKIT_EMERGENCY_PIN="$emergency_pin" \
python3 - <<'PY'
import os
import sys
from pathlib import Path

sys.path.insert(0, ".")
from etakit_kilit.config import hash_emergency_pin

source = Path("packaging/kilit.conf").read_text(encoding="utf-8").splitlines()
lines = []
for line in source:
    if line.startswith("server_url="):
        lines.append("server_url=" + os.environ["ETAKIT_SERVER_URL"])
    elif line.startswith("enrollment_key="):
        lines.append("enrollment_key=" + os.environ["ETAKIT_ENROLLMENT_KEY"])
    elif line.startswith("emergency_pin_hash="):
        continue
    else:
        lines.append(line)
pin = os.environ.get("ETAKIT_EMERGENCY_PIN", "")
if pin:
    lines.append("emergency_pin_hash=" + hash_emergency_pin(pin))
target = Path(os.environ["ETAKIT_STAGE"]) / "etc" / "etakit" / "kilit.conf"
target.write_text("\n".join(lines) + "\n", encoding="utf-8")
os.chmod(target, 0o666)
PY

for script in \
    "$stage/usr/bin/etakit-kilit" \
    "$stage/usr/libexec/etakit-kilit-start" \
    "$stage/usr/libexec/etakit-kilit-session" \
    "$stage/usr/libexec/etakit-kilit-save-config" \
    "$stage/usr/libexec/etakit-kilit-poweroff" \
    "$stage/etc/sudoers.d/etakit-kilit" \
    "$stage/etc/polkit-1/localauthority/50-local.d/49-etakit-poweroff.pkla" \
    "$stage/etc/polkit-1/rules.d/49-etakit-poweroff.rules" \
    "$stage/DEBIAN/postinst" \
    "$stage/DEBIAN/prerm" \
    "$stage/DEBIAN/postrm"
do
    sed -i 's/\r$//' "$script"
done

mkdir -p dist
output="dist/${package}_${version}_all.deb"
if dpkg-deb --help 2>/dev/null | grep -q root-owner-group; then
    dpkg-deb --root-owner-group --build "$stage" "$output"
else
    dpkg-deb --build "$stage" "$output"
fi

echo "$output"
