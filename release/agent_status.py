"""Thread-sicherer Status des Druck-Agenten fuer die Oberflaeche."""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class StatusSnapshot:
    server_ok: bool | None
    agent_name: str
    last_job_id: int | None
    last_printer: str
    last_result: str
    last_error: str
    last_contact: str


class AgentStatus:
    """Letzter Kontakt, letzter Auftrag und letzte Fehlzeile."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._server_ok: bool | None = None
        self._agent_name = ''
        self._last_job_id: int | None = None
        self._last_printer = ''
        self._last_result = ''
        self._last_error = ''
        self._last_contact = ''
        self._error_kind = ''
        self._poll_seq = 0

    def snapshot(self) -> StatusSnapshot:
        with self._lock:
            return StatusSnapshot(
                server_ok=self._server_ok,
                agent_name=self._agent_name,
                last_job_id=self._last_job_id,
                last_printer=self._last_printer,
                last_result=self._last_result,
                last_error=self._last_error,
                last_contact=self._last_contact,
            )

    def mark_online(self, when: str, agent_name: str | None = None) -> None:
        with self._lock:
            self._server_ok = True
            self._last_contact = when
            if agent_name:
                self._agent_name = agent_name
            if self._error_kind == 'netz':
                self._last_error = ''
                self._error_kind = ''

    def mark_offline(self, error: str, when: str) -> None:
        with self._lock:
            self._server_ok = False
            self._last_contact = when
            self._last_error = error
            self._error_kind = 'netz'

    def mark_job(
        self,
        job_id: int,
        printer: str,
        result: str,
        error: str = '',
        when: str = '',
    ) -> None:
        with self._lock:
            self._server_ok = True
            self._last_job_id = int(job_id)
            self._last_printer = printer
            self._last_result = result
            self._last_error = error
            self._error_kind = 'druck' if error else ''
            if when:
                self._last_contact = when

    def poll_seq(self) -> int:
        with self._lock:
            return self._poll_seq

    def note_poll(self) -> None:
        with self._lock:
            self._poll_seq += 1
