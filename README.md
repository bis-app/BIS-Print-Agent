# BIS Druck-Agent

Kleiner Python-Helfer fuer den Etiketten-Druck aus BIS, wenn der Server die
Drucker nicht direkt erreichen kann (z.&nbsp;B. weil BIS hinter einem
Cloudflare Tunnel laeuft und die Zebra-Drucker in einem anderen Standortnetz
stehen).

Der Agent baut nur **ausgehende HTTPS-Verbindungen** auf. Er holt
Druckauftraege per Long-/Short-Poll vom Server ab, sendet das mitgelieferte
ZPL via TCP/9100 an den lokalen Drucker und meldet das Ergebnis zurueck.
Damit ist weder ein VPN noch ein Inbound-Port noetig.

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
cp /pfad/zu/print_agent_client/.env.example .env
# .env editieren und BIS_BASE_URL + BIS_AGENT_TOKEN setzen
nano .env
```

### Erststart (manuell pruefen)

```bash
. venv/bin/activate
python print_agent.py
```

Erwartete Ausgabe: `BIS Druck-Agent gestartet. Server=...` Im Admin-UI
erscheint die Spalte "Letzter Heartbeat" mit aktuellem Zeitstempel.

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

> Falls eine venv genutzt wird, in der `.service`-Datei `ExecStart` auf
> `/opt/bis-print-agent/venv/bin/python` umstellen.

### Windows (NSSM)

[NSSM (Non-Sucking Service Manager)](https://nssm.cc/) installieren, dann:

```powershell
nssm install BIS-Print-Agent "C:\Python311\python.exe" "C:\bis-print-agent\print_agent.py"
nssm set BIS-Print-Agent AppDirectory "C:\bis-print-agent"
nssm set BIS-Print-Agent AppStdout "C:\bis-print-agent\agent.log"
nssm set BIS-Print-Agent AppStderr "C:\bis-print-agent\agent.log"
nssm set BIS-Print-Agent AppEnvironmentExtra "BIS_BASE_URL=https://bis.example.com" "BIS_AGENT_TOKEN=..."
nssm start BIS-Print-Agent
```

Alternativ kann eine `.env`-Datei neben `print_agent.py` liegen; sie wird
beim Start automatisch geladen.

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
sudo systemctl start bis-print-agent
```

`.env` und Token bleiben erhalten.
