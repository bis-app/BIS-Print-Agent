#!/usr/bin/env python3
"""
BIS Druck-Agent (On-Prem)

Holt Druckauftraege ausgehend per HTTPS vom BIS-Server ab und sendet das
mitgelieferte ZPL via TCP/9100 an den lokalen Zebra-Drucker.

Start als Fenster (Windows-App oder Raspberry Pi mit Display):

    python print_agent.py

Start als Dienst ohne Fenster:

    python print_agent.py --service

Konfiguration ueber Umgebungsvariablen oder .env neben diesem Skript:

    BIS_BASE_URL      z. B. https://bis.example.com
    BIS_AGENT_TOKEN   Bearer-Token (einmalig im Admin-UI angezeigt)
    POLL_TIMEOUT      Sekunden zwischen Polls (Default: 5)
    HEARTBEAT_EVERY   Heartbeat-Intervall in Sekunden (Default: 60)
    LOG_FILE          optional, Pfad fuer eine Log-Datei
    BIS_FULLSCREEN    1 = Vollbild (Raspberry Pi), sonst Fenster 320x240

Die Module agent_*.py liegen neben diesem Skript. Sie heissen nicht config.py,
damit sie das config-Paket der BIS-App nicht verdecken.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from agent_config import AgentConfig, load_config
from agent_instance import InstanceLock
from agent_loop import build_session, run_loop
from agent_network import get_backend
from agent_status import AgentStatus

CLIENT_DIR = Path(__file__).resolve().parent
ENV_PATH = CLIENT_DIR / '.env'


def lock_path() -> Path:
    """Gemeinsame Sperre fuer Dienst und Fenster.

    Unter Linux liegt sie in /tmp, damit der Desktop-Benutzer und ein
    Dienstkonto sie beide oeffnen koennen. Unter Windows neben dem Skript.
    """
    if os.name == 'nt':
        return CLIENT_DIR / 'print_agent.lock'
    return Path('/tmp/bis-print-agent.lock')


def setup_logging(log_file: str) -> logging.Logger:
    log = logging.getLogger('bis.print_agent')
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    log.addHandler(stream)
    if log_file:
        file_handler = RotatingFileHandler(
            log_file, maxBytes=2_000_000, backupCount=3, encoding='utf-8',
        )
        file_handler.setFormatter(fmt)
        log.addHandler(file_handler)
    return log


class AppContext:
    def __init__(self, config: AgentConfig, status: AgentStatus, backend, lock_ok: bool, log: logging.Logger) -> None:
        self.config = config
        self.status = status
        self.backend = backend
        self.lock_ok = lock_ok
        self.lock_state = 'ok' if lock_ok else 'busy'
        self.log = log
        self.env_path = ENV_PATH
        self.fullscreen = config.fullscreen
        self.stop = threading.Event()
        self.session = None
        self.thread: threading.Thread | None = None

    def replace_config(self, config: AgentConfig) -> None:
        self.config = config
        self.fullscreen = config.fullscreen
        if self.session is not None:
            self.session.headers['Authorization'] = f'Bearer {config.token}'

    def ensure_loop(self) -> None:
        if not self.lock_ok or not self.config.ready:
            return
        if self.thread is not None and self.thread.is_alive():
            return
        if self.session is None:
            try:
                self.session = build_session(self.config.token)
            except SystemExit as exc:
                self.log.error('%s', exc)
                return
        self.thread = threading.Thread(
            target=run_loop,
            args=(self.session, lambda: self.config, self.status, self.stop, self.log),
            name='bis-print-agent',
            daemon=True,
        )
        self.thread.start()


def _missing_credentials() -> int:
    print(
        'FEHLER: BIS_BASE_URL und BIS_AGENT_TOKEN muessen gesetzt sein '
        '(per Umgebungsvariable oder .env).',
        file=sys.stderr,
    )
    return 2


def run_service(config: AgentConfig, log: logging.Logger) -> int:
    if not config.ready:
        return _missing_credentials()
    lock = InstanceLock(lock_path())
    state = lock.acquire()
    if state != 'ok':
        log.error('Anderer Druck-Agent laeuft bereits.')
        return 1
    status = AgentStatus()
    stop = threading.Event()
    try:
        session = build_session(config.token)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        lock.release()
        return 2
    log.info('BIS Druck-Agent gestartet. Server=%s, Poll=%ss', config.base_url, config.poll_timeout)
    try:
        run_loop(session, lambda: config, status, stop, log)
        return 0
    except KeyboardInterrupt:
        log.info('Beendet (Ctrl+C).')
        stop.set()
        return 0
    finally:
        lock.release()


def run_window(config: AgentConfig, log: logging.Logger) -> int:
    lock = InstanceLock(lock_path())
    state = lock.acquire()
    if state == 'error':
        log.error('Sperrdatei konnte nicht angelegt werden.')
    elif state == 'busy':
        log.warning('Anderer Druck-Agent laeuft bereits. Fenster ohne Poller.')
    context = AppContext(
        config=config,
        status=AgentStatus(),
        backend=get_backend(),
        lock_ok=state == 'ok',
        log=log,
    )
    context.lock_state = state
    try:
        from agent_ui import run_ui
    except ImportError:
        print(
            'FEHLER: Tkinter fehlt. Auf dem Raspberry Pi: sudo apt install python3-tk',
            file=sys.stderr,
        )
        lock.release()
        return 2
    if context.lock_ok and config.ready:
        log.info('BIS Druck-Agent mit Fenster. Server=%s', config.base_url)
    try:
        return run_ui(context)
    except KeyboardInterrupt:
        context.stop.set()
        return 0
    finally:
        context.stop.set()
        lock.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='print_agent')
    parser.add_argument(
        '--service',
        action='store_true',
        help='Ohne Fenster laufen (Windows-Dienst oder systemd).',
    )
    args = parser.parse_args(argv)
    config = load_config(ENV_PATH)
    log = setup_logging(config.log_file)
    if args.service:
        return run_service(config, log)
    return run_window(config, log)


if __name__ == '__main__':
    sys.exit(main())
