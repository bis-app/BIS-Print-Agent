"""Poll, Heartbeat und ZPL-Versand. Kein Fenster, kein Start beim Import."""

from __future__ import annotations

import logging
import socket
import time
from typing import Callable

from agent_status import AgentStatus

logger = logging.getLogger('bis.print_agent')

SendZpl = Callable[[str, str], None]


class TransportError(Exception):
    """HTTP-Schicht nicht erreichbar. Tests nutzen diese Klasse direkt."""


def _transport_types() -> tuple[type[BaseException], ...]:
    types: list[type[BaseException]] = [TransportError]
    try:
        import requests
    except ImportError:
        return tuple(types)
    types.append(requests.RequestException)
    return tuple(types)


def send_zpl(printer_ip: str, zpl: str, timeout: float = 10.0) -> None:
    """ZPL ueber rohes TCP/9100 an den Zebra-Drucker senden."""
    if not printer_ip:
        raise ValueError('Drucker-IP fehlt')
    payload = zpl if zpl.endswith('\n') else zpl + '\n'
    with socket.create_connection((printer_ip, 9100), timeout=timeout) as sock:
        sock.sendall(payload.encode('utf-8'))


def build_session(token: str):
    try:
        import requests
    except ImportError:
        raise SystemExit(
            'FEHLER: Modul "requests" nicht installiert. Bitte zuerst:\n'
            '  pip install -r requirements.txt'
        )
    session = requests.Session()
    session.headers.update({
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'User-Agent': 'BIS-Print-Agent/1.0',
    })
    return session


def poll_once(
    session,
    base_url: str,
    status: AgentStatus,
    send: SendZpl = send_zpl,
    now: str = '',
) -> tuple[bool, float]:
    """Einmal pollen. Liefert (Auftrag verarbeitet, Wartezeit in Sekunden)."""
    try:
        return _poll_once(session, base_url, status, send, now)
    finally:
        status.note_poll()


def _poll_once(
    session,
    base_url: str,
    status: AgentStatus,
    send: SendZpl,
    now: str,
) -> tuple[bool, float]:
    url = f'{base_url}/api/agent/poll'
    try:
        response = session.post(url, timeout=30)
    except _transport_types() as exc:
        logger.warning('Poll fehlgeschlagen: %s', exc)
        status.mark_offline('Server nicht erreichbar.', now)
        return False, 0.0
    if response.status_code == 401:
        logger.error('Token wurde abgelehnt (401). Bitte im BIS-Admin neuen Token erzeugen.')
        status.mark_offline('Token wurde abgelehnt.', now)
        return False, 30.0
    if response.status_code >= 500 or not response.ok:
        logger.warning('Unerwartete Antwort: %s %s', response.status_code, response.text[:200])
        status.mark_offline(f'Server antwortet mit {response.status_code}.', now)
        return False, 0.0
    job = (response.json() or {}).get('job')
    if not job:
        status.mark_online(now)
        return False, 0.0

    job_id = job['id']
    drucker_name = job.get('drucker_name') or '?'
    drucker_ip = job.get('drucker_ip') or ''
    zpl = job.get('zpl') or ''
    logger.info('Auftrag #%s an %s (%s) wird gedruckt ...', job_id, drucker_name, drucker_ip)
    try:
        send(drucker_ip, zpl)
    except Exception as exc:
        msg = f'Druckfehler: {exc}'
        logger.error('Auftrag #%s: %s', job_id, msg)
        status.mark_job(job_id, drucker_name, 'fehler', msg, when=now)
        try:
            session.post(
                f'{base_url}/api/agent/jobs/{job_id}/error',
                json={'message': msg},
                timeout=10,
            )
        except _transport_types() as post_exc:
            logger.warning('Konnte Fehlermeldung nicht uebertragen: %s', post_exc)
        return True, 0.0
    try:
        session.post(f'{base_url}/api/agent/jobs/{job_id}/done', json={}, timeout=10)
    except _transport_types() as exc:
        logger.warning(
            'Druck OK, aber done-Meldung an Server fehlgeschlagen (%s). '
            'Server wird Auftrag nach Lease-Ablauf erneut versenden.', exc,
        )
        status.mark_job(
            job_id, drucker_name, 'ok',
            'Druck ok, Rueckmeldung an den Server fehlgeschlagen.',
            when=now,
        )
        return True, 0.0
    logger.info('Auftrag #%s erledigt.', job_id)
    status.mark_job(job_id, drucker_name, 'ok', '', when=now)
    return True, 0.0


def heartbeat(session, base_url: str, status: AgentStatus, now: str = '') -> None:
    try:
        response = session.post(f'{base_url}/api/agent/heartbeat', timeout=10)
    except _transport_types() as exc:
        logger.warning('Heartbeat fehlgeschlagen: %s', exc)
        status.mark_offline('Server nicht erreichbar.', now)
        return
    if response.ok:
        payload = response.json() or {}
        name = str(payload.get('agent_name') or '')
        status.mark_online(now, agent_name=name or None)
        logger.debug('Heartbeat OK')
        return
    logger.warning('Heartbeat HTTP %s: %s', response.status_code, response.text[:200])
    if response.status_code == 401:
        status.mark_offline('Token wurde abgelehnt.', now)
    else:
        status.mark_offline(f'Server antwortet mit {response.status_code}.', now)


def run_loop(session, get_config: Callable, status: AgentStatus, stop_event, log: logging.Logger | None = None) -> None:
    """Blockiert, bis stop_event gesetzt wird. get_config liefert die aktuelle AgentConfig."""
    log = log or logger
    last_heartbeat = 0.0
    backoff = 1.0
    while not stop_event.is_set():
        cfg = get_config()
        if not cfg.ready:
            stop_event.wait(1.0)
            continue
        session.headers['Authorization'] = f'Bearer {cfg.token}'
        now_m = time.monotonic()
        stamp = time.strftime('%H:%M:%S')
        if now_m - last_heartbeat >= cfg.heartbeat_every:
            heartbeat(session, cfg.base_url, status, stamp)
            last_heartbeat = now_m
        try:
            had_job, retry_after = poll_once(session, cfg.base_url, status, now=stamp)
        except Exception as exc:
            log.exception('Unerwarteter Fehler im Poll: %s', exc)
            status.mark_offline('Unerwarteter Fehler im Poll.', stamp)
            backoff = min(backoff * 2, 60.0)
            stop_event.wait(backoff)
            continue
        backoff = 1.0
        if retry_after:
            stop_event.wait(retry_after)
        elif had_job:
            stop_event.wait(0.2)
        else:
            stop_event.wait(cfg.poll_timeout)
