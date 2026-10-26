# Print-Agent auf Raspberry Pi 2 mit Watterott RPi-Display

Der Agent zeigt ein Fenster von **320×240** Punkten. Das Watterott
RPi-Display (2,8", 320×240, resistiver Touch) passt darauf, wenn es als
normaler Bildschirm im Desktop läuft. Tkinter zeichnet Buttons; ein rohes
SPI-Framebuffer ohne Desktop reicht nicht.

Der Pi holt Druckaufträge per ausgehendem HTTPS vom BIS-Server und schickt
das ZPL per TCP/9100 an die Zebra-Drucker im selben Netz. Es ist kein VPN
und kein offener Port auf dem Pi nötig.

Diese Anleitung gilt für den **Raspberry Pi 2** (1 GB RAM, 40-poliger
GPIO-Header) und das **Watterott RPi-Display** bzw. **RPi-Display B+**.
Das alte Installationsskript `rpi-display.sh` von Watterott nicht verwenden:
es schreibt noch nach `/boot/config.txt` und richtet ein veraltetes
X11-Layout ein.

## Was bereitliegen muss

- Raspberry Pi 2, microSD-Karte ab 16 GB, Netzteil 5 V / 2,5 A (Micro-USB)
- Watterott RPi-Display, direkt auf dem GPIO-Header (kein weiteres HAT:
  belegt sind SPI0, GPIO 18, 23, 24 und 25)
- USB-Tastatur. Die Oberfläche hat keine Bildschirmtastatur.
- Netzwerkkabel. Der Pi 2 hat WLAN nicht eingebaut. Ein USB-WLAN-Stick ist
  nur nötig, wenn die Verbindung später über das Menü „Netze“ laufen soll.
- Für die erste Einrichtung zusätzlich HDMI-Kabel und HDMI-Bildschirm.
  Sobald das TFT läuft, wird HDMI abgezogen.
- Zebra-Drucker im selben Netz, erreichbar auf Port 9100/TCP
- Im BIS-Admin ein Druck-Agent mit Token und die Zuordnung der Drucker.
  Schritte stehen in der [README](README.md), Abschnitte 1 und 2.

## 1. Betriebssystem schreiben

Im [Raspberry Pi Imager](https://www.raspberrypi.com/software/) das Image
**Raspberry Pi OS (32-bit)** mit Desktop wählen (Debian 13 / Trixie).
Nicht die Variante „Full“ (zu schwer für 1 GB RAM) und nicht „Lite“
(kein Desktop, das Fenster kann nicht starten).

64-Bit bootet auf dem ursprünglichen Pi 2 (BCM2836) nicht. Nur die seltene
Revision v1.2 (BCM2837) kann 64-Bit; 32-Bit ist für beide richtig.

Im Imager vor dem Schreiben festlegen:

- Hostname, z. B. `bis-druck`
- Benutzer und Passwort (in den Beispielen unten: `pi`)
- WLAN leer lassen
- Locale `de_DE.UTF-8`, Zeitzone `Europe/Berlin`, Tastatur Deutsch
- SSH aktivieren

Karte einlegen, Pi **ohne** Display-Platine mit HDMI, Tastatur und LAN
starten und die Einrichtung durchlaufen lassen.

## 2. System vorbereiten

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y python3-venv python3-tk network-manager
sudo raspi-config nonint do_boot_behaviour B4
sudo raspi-config nonint do_blanking 1
sudo reboot
```

`B4` startet den Desktop und meldet den Benutzer an, der `sudo` aufgerufen
hat. Fehlt dabei LightDM, bricht der Befehl ab; dann
`sudo apt install lightdm` und den Befehl erneut ausführen.

`do_blanking 1` schaltet das Abdunkeln unter X11 aus. Auf dem Pi 2 ist das
die übliche Sitzung. Geht das Bild trotzdem aus und
`echo $XDG_SESSION_TYPE` zeigt `wayland`, die Zeile mit `swayidle` aus
`/etc/xdg/labwc/autostart` in `~/.config/labwc/autostart` weglassen.

Der Pi 2 hat keine Echtzeituhr. HTTPS scheitert, wenn die Uhr weit daneben
liegt. Nach dem Neustart prüfen:

```bash
timedatectl
```

`System clock synchronized: yes` muss gesetzt sein, sobald das LAN steht.

## 3. Display aktivieren

Pi herunterfahren (`sudo poweroff`), vom Strom trennen und das
RPi-Display auf den Header stecken. Pin 1 der Platine sitzt auf Pin 1
des Pi (Ecke Richtung microSD).

Danach wieder mit HDMI starten und ans Ende von
`/boot/firmware/config.txt` anfügen:

```
dtoverlay=watterott-display,drm,rotate=180,swapxy
```

`drm` muss der erste Parameter sein. Damit meldet sich das Display als
normaler Bildschirm (Kernel-Treiber `mi0283qt`) inklusive Touch
(ADS7846 auf SPI CS1). Mit dem DRM-Treiber ist `rotate=180` das
Querformat 320×240, auf das das Agent-Fenster gebaut ist. `rotate=0`
ist dasselbe Format, aber kopfstehend. `90` und `270` ergeben 240×320
hochkant, darauf passt die Oberfläche nicht.

`swapxy` tauscht die Touch-Achsen und bleibt in der Zeile. Das Overlay
kennt kein `invx` und kein `invy`. Danach stimmt links/rechts, oben und
unten sind vertauscht: ein Tipp links oben trifft „Zurück“, rechts oben
„Speichern“. Das ist die Y-Achse.

Der Desktop auf Trixie ist Wayland (labwc). Der Touch läuft über
libinput, nicht über X. `xinput list` zeigt deshalb nur
`xwayland-pointer` und `xwayland-touch` und warnt vor einem
Xwayland-Server. Eine Transformationsmatrix per `xinput` ändert das
TFT nicht.

Die Spiegelung setzt eine udev-Regel. `ATTRS` und `KERNEL=="event*"`
sind nötig, sonst hängt die Eigenschaft am falschen Knoten und libinput
liest sie nicht:

```bash
sudo tee /etc/udev/rules.d/99-watterott-touch.rules >/dev/null <<'EOF'
SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="ADS7846 Touchscreen", ENV{LIBINPUT_CALIBRATION_MATRIX}="1 0 0 0 -1 1"
EOF
```

Die Matrix `1 0 0 0 -1 1` spiegelt nur Y. Weicht der Name ab, steht er
in `N: Name` von:

```bash
grep -E 'N: Name|H: Handlers' /proc/bus/input/devices
```

Diesen Wortlaut in `ATTRS{name}` eintragen. Danach neu starten und HDMI
abziehen. libinput liest die Matrix nur beim Öffnen des Geräts; der
Agent allein neu zu starten reicht nicht. Der Desktop muss auf dem TFT
erscheinen.

Die Hintergrundbeleuchtung geht beim DRM-Treiber erst an, wenn der Desktop
den Bildschirm benutzt; ein dunkles Display nur während SSH ist normal.

Kontrolle, falls das TFT dunkel bleibt:

```bash
dmesg | grep -i -E 'mi0283qt|ads7846|ili9341'
kmsprint
```

`kmsprint` muss einen Anschluss mit 320×240 zeigen. Fehlt der Treiber,
liegt die Zeile in `config.txt` falsch oder es ist noch die alte Datei
`/boot/config.txt` bearbeitet worden. Auf Trixie zählt nur
`/boot/firmware/config.txt`.

Touch prüfen, die Nummer aus `Handlers` einsetzen:

```bash
udevadm info /dev/input/event0 | grep CALIBRATION
```

Erwartet ist `LIBINPUT_CALIBRATION_MATRIX=1 0 0 0 -1 1`. Fehlt die
Zeile, passt `ATTRS{name}` nicht. Ein Tipp oben links muss danach im
Feld „Server-URL“ landen.

Sitzt das Bild auf HDMI statt auf dem TFT, HDMI abziehen und neu starten.

## 4. Agent installieren

Die Dateien liegen im BIS-Repository unter `print_agent_client/`. Vom
Windows-Rechner, im Ordner des Repositories:

```powershell
scp -r print_agent_client pi@bis-druck.local:/home/pi/print_agent_client
```

Auf dem Pi:

```bash
sudo mkdir -p /opt/bis-print-agent
sudo cp /home/pi/print_agent_client/print_agent.py /opt/bis-print-agent/
sudo cp /home/pi/print_agent_client/agent_*.py /opt/bis-print-agent/
sudo cp /home/pi/print_agent_client/requirements.txt /opt/bis-print-agent/
sudo cp /home/pi/print_agent_client/bis-print-agent-start.sh /opt/bis-print-agent/
sudo cp /home/pi/print_agent_client/.env.example /opt/bis-print-agent/.env
sudo chown -R pi:pi /opt/bis-print-agent
chmod +x /opt/bis-print-agent/bis-print-agent-start.sh
python3 -m venv /opt/bis-print-agent/venv
/opt/bis-print-agent/venv/bin/pip install -r /opt/bis-print-agent/requirements.txt
nano /opt/bis-print-agent/.env
```

In der `.env` eintragen:

```
BIS_BASE_URL=https://bis.example.com
BIS_AGENT_TOKEN=der-token-aus-dem-admin
BIS_FULLSCREEN=1
```

Die URL ohne Pfad am Ende. Der Token erscheint im BIS nur einmal
(`Adminbereich -> Druck-Agents`). `chmod 600 /opt/bis-print-agent/.env`.

Den systemd-Dienst **nicht** einrichten. `bis-print-agent.service` startet
den Agent ohne Fenster. Fenster und Dienst sperren sich gegenseitig; läuft
der Dienst, pollt das Fenster nicht.

## 5. Erster Start

Am Desktop auf dem TFT, oder aus einer SSH-Sitzung heraus auf dem
bereits laufenden Desktop. SSH zeichnet das Fenster nicht im Terminal
und startet den Desktop nicht; die automatische Anmeldung aus Abschnitt 2
muss schon laufen. `ssh -X` würde das Fenster auf den Windows-Rechner
legen, nicht auf das TFT.

```bash
export DISPLAY=:0
export XAUTHORITY=/home/pi/.Xauthority
cd /opt/bis-print-agent
./venv/bin/python print_agent.py
```

Das Fenster füllt das TFT. Die SSH-Sitzung muss offen bleiben; mit dem
Abmelden endet der Agent. Dauerhaft startet ihn der Autostart aus
Abschnitt 6. Ohne URL oder Token bleibt das Fenster in den
Einstellungen stehen. Mit gültigem Zugang erscheint im BIS unter
`Adminbereich -> Druck-Agents` ein Herzschlag.

Neu starten über eine zweite SSH-Sitzung. Es darf nur eine Instanz
laufen; die Sperre `/tmp/bis-print-agent.lock` gibt der Prozess beim
Beenden frei. Die Datei selbst löschen bringt nichts, solange der
Prozess noch lebt.

```bash
pkill -f /opt/bis-print-agent/bis-print-agent-start.sh
pkill -f /opt/bis-print-agent/print_agent.py
pgrep -af bis-print-agent || echo 'kein Agent mehr'
```

`pkill` trifft das Warteskript und das Fenster, egal ob sie aus dem
Autostart oder aus einer offenen SSH-Sitzung kommen. `pgrep` darf danach
nichts mehr zeigen. War der Dienst ohne Fenster doch eingerichtet, zusätzlich:

```bash
sudo systemctl disable --now bis-print-agent
```

Danach wie oben mit `DISPLAY=:0` neu starten. Ein Neustart des Pi macht
dasselbe: der Autostart öffnet das Fenster einmal.

Netzwerk am Pi prüfen, bevor ein Etikett gedruckt wird:

```bash
curl -I https://bis.example.com/health
nc -zv 192.168.1.50 9100
nmcli general permissions
```

Die IP im `nc`-Beispiel ist die des Druckers. In der Berechtigungsliste
muss `org.freedesktop.NetworkManager.settings.modify.system` auf `yes`
stehen. Das gilt für die lokale Desktop-Sitzung, nicht für ein reines
SSH-Login. Fehlt das Recht, meldet die Oberfläche beim Speichern
„Keine Berechtigung, das Netzwerk zu ändern.“ Der Druck selbst läuft
trotzdem, solange der Pi den Server und die Drucker erreicht.

Statische Adresse und WLAN werden in den Einstellungen gespeichert
(Felder IP, Maske, Gateway, bei WLAN zusätzlich „Netze“). LAN legt das
NetworkManager-Profil `bis-lan` auf Ethernet und trennt WLAN. WLAN legt
`bis-wlan` an. Dafür ist kein root nötig.

## 6. Autostart

```bash
mkdir -p /home/pi/.config/autostart
cp /home/pi/print_agent_client/bis-print-agent.desktop /home/pi/.config/autostart/
```

Die Datei startet `/opt/bis-print-agent/bis-print-agent-start.sh` mit
`BIS_FULLSCREEN=1`. Das Skript wartet bis zu 60 Sekunden auf eine
Default-Route und danach bis zu 30 Sekunden, bis die Uhr per NTP
synchron ist. Erst dann öffnet es das Fenster. Kommt weder Netz noch
Uhrzeit, startet das Fenster trotzdem, damit die Einstellungen erreichbar
bleiben. Ein Start von Hand über SSH wartet nicht. Nach einem Neustart
muss das Statusfenster ohne Anmeldung auf dem TFT liegen.

## 7. Aktualisieren

```bash
sudo cp /home/pi/print_agent_client/print_agent.py /opt/bis-print-agent/
sudo cp /home/pi/print_agent_client/agent_*.py /opt/bis-print-agent/
sudo cp /home/pi/print_agent_client/bis-print-agent-start.sh /opt/bis-print-agent/
sudo chown pi:pi /opt/bis-print-agent/print_agent.py /opt/bis-print-agent/agent_*.py /opt/bis-print-agent/bis-print-agent-start.sh
chmod +x /opt/bis-print-agent/bis-print-agent-start.sh
cp /home/pi/print_agent_client/bis-print-agent.desktop /home/pi/.config/autostart/
sudo reboot
```

`.env` und Token bleiben liegen. Vor dem Kopieren die neuen Dateien wie in
Abschnitt 4 auf den Pi legen.

## Störungen

| Beobachtung | Was tun |
| --- | --- |
| TFT bleibt schwarz, HDMI zeigt den Desktop | HDMI abziehen und neu starten. Overlay-Zeile und `kmsprint` prüfen. |
| Bild ist 240×320 oder liegt auf der Seite | `rotate=180` für Querformat. Kopfstehend: `rotate=0`. Nicht `90` oder `270`. |
| Touch: oben trifft unten, links/rechts stimmt | `swapxy` lassen. Y-Matrix aus Abschnitt 3 (`1 0 0 0 -1 1`), `ATTRS` und `event*`. `xinput` wirkt hier nicht. Nach der Regel neu starten. |
| `udevadm` zeigt keine `CALIBRATION` | Gerätenamen aus `N: Name` in die Regel übernehmen, neu starten. |
| Fenster sagt, der Dienst laufe | `sudo systemctl disable --now bis-print-agent`, Sperrdatei `/tmp/bis-print-agent.lock` ist danach frei. |
| `Token wurde abgelehnt (401)` | Token im BIS neu erzeugen, `.env` anpassen, Agent neu starten. |
| Direkt nach dem Einschalten „Server: nicht erreichbar“ | Der erste Poll lief vor Route oder Uhr. Die Zeile wechselt von selbst, sobald beides steht. Bleibt sie rot: `curl -I` gegen die BIS-URL und `timedatectl`. |
| Herzschlag bleibt aus | `curl -I` gegen die BIS-URL, Uhr mit `timedatectl` prüfen. |
| Auftrag bleibt auf `leased` | Agent war weg. Nach 60 Sekunden setzt der Server den Auftrag auf `pending`. |
| Auftrag auf `error` | Text unter `Adminbereich -> Druck-Queue`. „Erneut zustellen“ setzt ihn zurück. Drucker-IP und Port 9100 prüfen. |
| Netzwerk lässt sich nicht speichern | Am Gerät angemeldet sein, nicht nur per SSH. `nmcli general permissions` muss `settings.modify.system: yes` zeigen. |
