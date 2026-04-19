#!/usr/bin/env python3
"""
BIS Druck-Agent (On-Prem)

Holt Druckauftraege ausgehend per HTTPS vom BIS-Server ab und sendet das
mitgelieferte ZPL via TCP/9100 an den lokalen Zebra-Drucker.

Vorteile:
- Nur ausgehende HTTPS-Verbindungen -> funktioniert hinter Firewall/NAT,
  kein VPN, kein Cloudflare-Connector im LAN noetig.
- Kombinierbar mit Direkt-Druck: Drucker im Server-LAN bleiben "Direkt",
  nur Drucker hinter dem Tunnel laufen ueber den Agent.

Konfiguration ueber Umgebungsvariablen oder .env-Datei:

    BIS_BASE_URL    z. B. https://bis.example.com
    BIS_AGENT_TOKEN Bearer-Token (einmalig im Admin-UI angezeigt)
    POLL_TIMEOUT    Sekunden zwischen Polls (Default: 5)
    LOG_FILE        optional, Pfad fuer Log-Datei
    HEARTBEAT_EVERY Heartbeat-Intervall in Sekunden (Default: 60)

Start (Linux):

    python3 print_agent.py

Start (Windows):

    python print_agent.py
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

try:
    import requests
except ImportError:
    print('FEHLER: Modul "requests" nicht installiert. Bitte zuerst:', file=sys.stderr)
    print('  pip install -r requirements.txt', file=sys.stderr)
    sys.exit(2)


def _load_dotenv(path: Path) -> None:
    """Minimaler .env-Loader (kein externes Paket noetig)."""
    if not path.exists():
        return
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(Path(__file__).resolve().parent / '.env')

BIS_BASE_URL = os.environ.get('BIS_BASE_URL', '').rstrip('/')
BIS_AGENT_TOKEN = os.environ.get('BIS_AGENT_TOKEN', '').strip()
POLL_TIMEOUT = float(os.environ.get('POLL_TIMEOUT', '5'))
HEARTBEAT_EVERY = float(os.environ.get('HEARTBEAT_EVERY', '60'))
LOG_FILE = os.environ.get('LOG_FILE', '').strip()

if not BIS_BASE_URL or not BIS_AGENT_TOKEN:
    print(
        'FEHLER: BIS_BASE_URL und BIS_AGENT_TOKEN muessen gesetzt sein '
        '(per Umgebungsvariable oder .env).',
        file=sys.stderr,
    )
    sys.exit(2)


def _build_logger() -> logging.Logger:
    log = logging.getLogger('bis-print-agent')
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    if LOG_FILE:
        fh = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding='utf-8')
        fh.setFormatter(fmt)
        log.addHandler(fh)
    return log


logger = _build_logger()

session = requests.Session()
session.headers.update({
    'Authorization': f'Bearer {BIS_AGENT_TOKEN}',
    'Content-Type': 'application/json',
    'User-Agent': 'BIS-Print-Agent/1.0',
})


def send_zpl(printer_ip: str, zpl: str, timeout: float = 10.0) -> None:
    """ZPL ueber rohes TCP/9100 an den Zebra-Drucker senden."""
    if not printer_ip:
        raise ValueError('Drucker-IP fehlt')
    payload = zpl if zpl.endswith('\n') else zpl + '\n'
    with socket.create_connection((printer_ip, 9100), timeout=timeout) as sock:
        sock.sendall(payload.encode('utf-8'))


def poll_once() -> bool:
    """Einmal pollen, ggf. einen Auftrag verarbeiten. Return True wenn ein Job."""
    url = f'{BIS_BASE_URL}/api/agent/poll'
    try:
        r = session.post(url, timeout=30)
    except requests.RequestException as e:
        logger.warning('Poll fehlgeschlagen: %s', e)
        return False
    if r.status_code == 401:
        logger.error('Token wurde abgelehnt (401). Bitte im BIS-Admin neuen Token erzeugen.')
        time.sleep(30)
        return False
    if r.status_code >= 500:
        logger.warning('Server-Fehler beim Poll: %s %s', r.status_code, r.text[:200])
        return False
    if not r.ok:
        logger.warning('Unerwartete Antwort: %s %s', r.status_code, r.text[:200])
        return False
    data = r.json()
    job = data.get('job')
    if not job:
        return False

    job_id = job['id']
    drucker_name = job.get('drucker_name') or '?'
    drucker_ip = job.get('drucker_ip') or ''
    zpl = job.get('zpl') or ''
    logger.info('Auftrag #%s an %s (%s) wird gedruckt ...', job_id, drucker_name, drucker_ip)
    try:
        send_zpl(drucker_ip, zpl)
    except Exception as e:
        msg = f'Druckfehler: {e}'
        logger.error('Auftrag #%s: %s', job_id, msg)
        try:
            session.post(
                f'{BIS_BASE_URL}/api/agent/jobs/{job_id}/error',
                json={'message': msg},
                timeout=10,
            )
        except requests.RequestException as exc:
            logger.warning('Konnte Fehlermeldung nicht uebertragen: %s', exc)
        return True
    try:
        session.post(
            f'{BIS_BASE_URL}/api/agent/jobs/{job_id}/done',
            json={},
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.warning(
            'Druck OK, aber done-Meldung an Server fehlgeschlagen (%s). '
            'Server wird Auftrag nach Lease-Ablauf erneut versenden.', exc,
        )
    else:
        logger.info('Auftrag #%s erledigt.', job_id)
    return True


def heartbeat() -> None:
    try:
        r = session.post(f'{BIS_BASE_URL}/api/agent/heartbeat', timeout=10)
        if r.ok:
            logger.debug('Heartbeat OK: %s', r.json())
        else:
            logger.warning('Heartbeat HTTP %s: %s', r.status_code, r.text[:200])
    except requests.RequestException as e:
        logger.warning('Heartbeat fehlgeschlagen: %s', e)


def main() -> int:
    logger.info('BIS Druck-Agent gestartet. Server=%s, Poll=%ss', BIS_BASE_URL, POLL_TIMEOUT)
    last_heartbeat = 0.0
    backoff = 1.0
    try:
        while True:
            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_EVERY:
                heartbeat()
                last_heartbeat = now
            try:
                had_job = poll_once()
            except Exception as e:
                logger.exception('Unerwarteter Fehler im Poll: %s', e)
                had_job = False
                backoff = min(backoff * 2, 60.0)
                time.sleep(backoff)
                continue
            backoff = 1.0
            if had_job:
                time.sleep(0.2)
            else:
                time.sleep(POLL_TIMEOUT)
    except KeyboardInterrupt:
        logger.info('Beendet (Ctrl+C).')
        return 0


if __name__ == '__main__':
    sys.exit(main())
