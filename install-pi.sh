#!/bin/bash
# Installiert oder aktualisiert den BIS Druck-Agenten auf einem Raspberry Pi.
# Aufruf als Desktop-Benutzer: sudo bash install-pi.sh
# Holt nur den Ordner release/ aus dem GitHub-Repo. .env und venv bleiben erhalten.

set -euo pipefail

REPO_TARBALL="https://github.com/bis-app/BIS-Print-Agent/archive/refs/heads/main.tar.gz"
ARCHIVE_DIR="BIS-Print-Agent-main"
PREFIX="/opt/bis-print-agent"

if [ "$(id -u)" -ne 0 ]; then
    echo "Bitte mit sudo ausfuehren: sudo bash install-pi.sh" >&2
    exit 1
fi

if [ -z "${SUDO_USER:-}" ] || [ "$SUDO_USER" = root ]; then
    echo "Bitte als Desktop-Benutzer starten: sudo bash install-pi.sh" >&2
    exit 1
fi

APP_USER="$SUDO_USER"
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
if [ -z "$APP_HOME" ] || [ ! -d "$APP_HOME" ]; then
    echo "Home-Verzeichnis von $APP_USER nicht gefunden." >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
    python3-venv \
    python3-tk \
    python3-pip \
    network-manager \
    ca-certificates \
    curl \
    rsync

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

curl -fsSL -o "$tmpdir/src.tar.gz" "$REPO_TARBALL"
tar -xzf "$tmpdir/src.tar.gz" -C "$tmpdir"

src="$tmpdir/$ARCHIVE_DIR/release"
if [ ! -f "$src/print_agent.py" ]; then
    echo "Im Tarball fehlt release/print_agent.py. Liegt der Ordner release auf main?" >&2
    exit 1
fi

mkdir -p "$PREFIX"
# venv und .env nicht loeschen und nicht ueberschreiben.
rsync -a --delete \
    --exclude venv \
    --exclude .env \
    "$src/" "$PREFIX/"

if [ ! -f "$PREFIX/.env" ]; then
    cp "$PREFIX/.env.example" "$PREFIX/.env"
fi
chmod 600 "$PREFIX/.env"
chmod +x "$PREFIX/bis-print-agent-start.sh"
chown -R "$APP_USER:" "$PREFIX"

if [ ! -x "$PREFIX/venv/bin/python" ]; then
    sudo -u "$APP_USER" python3 -m venv "$PREFIX/venv"
fi
sudo -u "$APP_USER" "$PREFIX/venv/bin/pip" install -r "$PREFIX/requirements.txt"

autostart="$APP_HOME/.config/autostart"
sudo -u "$APP_USER" mkdir -p "$autostart"
sudo -u "$APP_USER" cp "$PREFIX/bis-print-agent.desktop" "$autostart/bis-print-agent.desktop"

if systemctl cat bis-print-agent.service >/dev/null 2>&1; then
    systemctl disable --now bis-print-agent.service || true
fi

# Laufende Instanz beenden, damit die Sperre frei wird. pkill trifft sich nicht selbst.
pkill -f "$PREFIX/bis-print-agent-start.sh" || true
pkill -f "$PREFIX/print_agent.py" || true
i=0
while [ "$i" -lt 10 ]; do
    if ! pgrep -f "$PREFIX/print_agent.py" >/dev/null \
        && ! pgrep -f "$PREFIX/bis-print-agent-start.sh" >/dev/null; then
        break
    fi
    i=$((i + 1))
    sleep 0.5
done

xauth="$APP_HOME/.Xauthority"
if [ -f "$xauth" ]; then
    # Eigene Shell, damit das Fenster nach dem Ende dieses Skripts weiterlaeuft.
    sudo -u "$APP_USER" bash -c \
        'export DISPLAY=:0 XAUTHORITY="$1"; nohup "$2" >/dev/null 2>&1 & disown' \
        bash "$xauth" "$PREFIX/bis-print-agent-start.sh"
    echo "Fenster wird in der Desktop-Sitzung gestartet."
else
    echo "Keine X-Sitzung gefunden. Der Autostart oeffnet das Fenster beim naechsten Login."
    echo "Einmal neu starten: sudo reboot"
fi

if grep -q 'ersetze_mich_mit_dem_token_aus_dem_admin_ui' "$PREFIX/.env"; then
    echo "URL und Token eintragen: nano $PREFIX/.env"
fi

echo "Fertig: $PREFIX"
