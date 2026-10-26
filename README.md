# BIS Druck-Agent

Kleiner Python-Helfer fuer den Etiketten-Druck aus BIS, wenn der Server die
Drucker nicht direkt erreichen kann (z.&nbsp;B. weil BIS hinter einem
Cloudflare Tunnel laeuft und die Zebra-Drucker in einem anderen Standortnetz
stehen).

Der Agent baut nur **ausgehende HTTPS-Verbindungen** auf. Er holt
Druckauftraege per Long-/Short-Poll vom Server ab, sendet das mitgelieferte
ZPL via TCP/9100 an den lokalen Drucker und meldet das Ergebnis zurueck.
Damit ist weder ein VPN noch ein Inbound-Port noetig.

Derselbe Client laeuft als Fenster (320x240, auf dem Raspberry Pi im Vollbild)
oder ohne Fenster als Dienst. Das Fenster zeigt den Status und eine
Einstellungsseite fuer BIS-Zugang und statische Netzwerkdaten. Texte werden
mit einer USB-Tastatur eingegeben, es gibt keine Bildschirmtastatur.

> Direkt-Druck (Server -> Drucker per TCP/9100) bleibt **parallel** moeglich.
> Pro Drucker entscheidet die Spalte `zebra_printers.agent_id`, wie gedruckt
> wird (`NULL` = direkt, sonst ueber den zugewiesenen Agent).

## 0. Server-Migration (einmalig auf dem BIS-Server)

Die noetigen Schema-Erweiterungen sind in
[`utils/database_schema_init.py`](../utils/database_schema_init.py)
hinterlegt und werden beim naechsten Start automatisch angelegt:

- neue Tabelle `print_agents` (Name, Standort, Token-Hash, last_seen, ...)
- neue Tabelle `print_jobs` (Druckwarteschlange mit Status/Lease)
- neue Spalte `zebra_printers.agent_id` (NULL = Direkt-TCP, sonst Agent)

Schritte beim Update:

1. Code aktualisieren (`git pull`).
2. BIS-Service einmal neu starten � die Schema-Migration laeuft idempotent
   und faellt auch bei mehreren Aufrufen sauber durch.
3. Funktion verifizieren:
   - `Adminbereich -> Etikettendrucker`: bei jedem bestehenden Drucker
     steht jetzt `Direkt` (Default beibehalten).
   - `Adminbereich -> Druck-Agents`: leere Liste sichtbar.
   - `Adminbereich -> Druck-Queue`: leere Liste sichtbar.
4. Optional: Tests laufen lassen
   (`pytest tests/test_print_dispatch.py tests/test_print_agent_api.py`).

Bestehende Drucker behalten ihr bisheriges Verhalten (Direkt-TCP), bis ihnen
ein Agent zugewiesen wird � ein Big-Bang-Umbau ist nicht noetig.

## 1. Token erzeugen (im BIS-Admin)

1. Im Browser BIS oeffnen, `Adminbereich -> Druck-Agents` (auch verlinkt im
   Etikettendrucker-Tab) aufrufen.
2. Neuen Agent mit eindeutigem Namen anlegen (z.&nbsp;B. `standort-a`).
3. Der angezeigte Token wird **nur einmal** dargestellt. Sofort kopieren und
   sicher hinterlegen. Bei Verlust kann er ueber "Token neu erzeugen"
   rotiert werden.

## 2. Drucker einem Agent zuweisen

Im Etikettendrucker-Tab beim Bearbeiten eines Druckers das Feld "Druck-Agent"
setzen (Default: `� Direkt (Server -> Drucker) �`). Sobald ein Agent
hinterlegt ist, geht jeder Druckauftrag fuer diesen Drucker ueber die
Warteschlange.

## 3. Agent installieren

### Voraussetzungen

- Python 3.9+ am Standort (z.&nbsp;B. auf einem Mini-PC im selben LAN wie die
  Drucker).
- Netzwerkzugang zu den Druckern (Port 9100/TCP).
- Ausgehende HTTPS-Verbindung zum BIS-Server (Port 443).

### Setup

```bash
# Verzeichnis und venv anlegen
mkdir -p /opt/bis-print-agent && cd /opt/bis-print-agent
python3 -m venv venv
. venv/bin/activate
pip install -r /pfad/zu/print_agent_client/requirements.txt
cp /pfad/zu/print_agent_client/print_agent.py .
cp /pfad/zu/print_agent_client/agent_*.py .
cp /pfad/zu/print_agent_client/.env.example .env
# .env editieren und BIS_BASE_URL + BIS_AGENT_TOKEN setzen
nano .env
```

`agent_*.py` muss neben `print_agent.py` liegen. Auf dem Raspberry Pi zusaetzlich
`sudo apt install python3-tk`. Tkinter gehoert nicht in die requirements, es
kommt aus der Python-Installation.

### Erststart (manuell pruefen)

```bash
. venv/bin/activate
python print_agent.py            # Fenster
python print_agent.py --service  # ohne Fenster, Log auf der Konsole
```

Mit Zugangdaten erscheint im Admin-UI die Spalte "Letzter Heartbeat".
Ohne `BIS_BASE_URL` oder Token bleibt das Fenster offen und zeigt die
Einstellungen. `--service` beendet sich in dem Fall sofort.

## 4. Als Dienst dauerhaft starten

### Linux (systemd)

```bash
sudo useradd -r -s /usr/sbin/nologin bis-agent
sudo chown -R bis-agent:bis-agent /opt/bis-print-agent
sudo cp /pfad/zu/print_agent_client/bis-print-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bis-print-agent
sudo journalctl -u bis-print-agent -f
```

Der Unit startet `print_agent.py --service` und oeffnet kein Fenster.

> Falls eine venv genutzt wird, in der `.service`-Datei `ExecStart` auf
> `/opt/bis-print-agent/venv/bin/python` umstellen.

### Windows (NSSM)

[NSSM (Non-Sucking Service Manager)](https://nssm.cc/) installieren, dann:

```powershell
nssm install BIS-Print-Agent "C:\Python311\python.exe" "C:\bis-print-agent\print_agent.py" "--service"
nssm set BIS-Print-Agent AppDirectory "C:\bis-print-agent"
nssm set BIS-Print-Agent AppStdout "C:\bis-print-agent\agent.log"
nssm set BIS-Print-Agent AppStderr "C:\bis-print-agent\agent.log"
nssm set BIS-Print-Agent AppEnvironmentExtra "BIS_BASE_URL=https://bis.example.com" "BIS_AGENT_TOKEN=..."
nssm start BIS-Print-Agent
```

Alternativ kann eine `.env`-Datei neben `print_agent.py` liegen; sie wird
beim Start automatisch geladen. Der Dienst aendert keine Netzwerkadapter
und braucht keine Administratorrechte.

Dienst und Fenster schliessen sich aus. Laeuft der Dienst, zeigt die App den
Hinweis, ihn zu beenden, und pollt nicht ein zweites Mal. Die Sperrdatei liegt
unter Windows neben dem Skript (`print_agent.lock`), unter Linux in
`/tmp/bis-print-agent.lock`.

### Windows (Fenster)

```powershell
cd C:\bis-print-agent
python print_agent.py
# ohne Konsole:
pythonw print_agent.py
```

Das Fenster ist 320x240. Unter Einstellungen liegen Server-URL, Token,
LAN/WLAN, IP-Adresse, Subnetzmaske und Gateway. Bei WLAN oeffnet "Netze"
die SSID-Liste und das Passwortfeld. Speichern schreibt den BIS-Zugang in
die `.env` und setzt die statische Adresse am Adapter. Dafuer muss die App
als Administrator gestartet werden. Ohne diese Rechte bleibt der Druck
nutzbar, das Netzwerk wird nicht geaendert.

### Raspberry Pi (Watterott RPi-Display)

Einrichtung fuer den Raspberry Pi 2 mit Watterott RPi-Display (2,8",
320x240): [raspberry-pi.md](raspberry-pi.md).

Das Display muss als normaler Bildschirm im Desktop erscheinen. Auf dem
aktuellen Raspberry Pi OS steht dafuer in `/boot/firmware/config.txt`:

```
dtoverlay=watterott-display,drm,rotate=180,swapxy
```

Autostart in der Desktop-Sitzung, nicht als systemd-Dienst (der Dienst
oeffnet kein Fenster und sperrt das Fenster):

```bash
mkdir -p ~/.config/autostart
cp /pfad/zu/print_agent_client/bis-print-agent.desktop ~/.config/autostart/
```

Die `.desktop`-Datei setzt `BIS_FULLSCREEN=1` selbst. Netzwerk speichern
geht ueber NetworkManager (`nmcli`). Der Desktop-Benutzer braucht dafuer
die Polkit-Berechtigung; der Prozess laeuft nicht als root. LAN setzt die
statische Adresse auf Ethernet und trennt WLAN. WLAN verbindet die
gewaehlte SSID und setzt dort die statische Adresse. Der Pi 2 hat kein
eingebautes WLAN.

## 5. Betrieb / Troubleshooting

- **`Token wurde abgelehnt (401)`** -> Token im BIS-Admin neu erzeugen und
  in `.env` eintragen, Dienst neu starten.
- **Heartbeat bleibt rot** -> Pruefen, ob der Agent ausgehend HTTPS auf den
  BIS-Server kann (z.&nbsp;B. `curl -I https://bis.example.com/health`).
- **Druckauftrag bleibt in `leased`** -> Agent ist abgestuerzt; nach Ablauf
  des Lease (60s) setzt der Server den Auftrag automatisch zurueck auf
  `pending`. Der Agent uebernimmt ihn beim naechsten Poll.
- **Auftrag in `error`** -> Im Admin unter `Druck-Queue` Fehlertext lesen.
  Mit "Erneut zustellen" wird er zurueck auf `pending` gesetzt; nach
  `PRINT_JOB_MAX_ATTEMPTS` Versuchen wird er endgueltig als `error`
  markiert.

## 6. Updaten

```bash
sudo systemctl stop bis-print-agent
cp /pfad/zu/neuem/print_agent.py /opt/bis-print-agent/
cp /pfad/zu/neuem/agent_*.py /opt/bis-print-agent/
sudo systemctl start bis-print-agent
```

`.env` und Token bleiben erhalten.
