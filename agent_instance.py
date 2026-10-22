"""Eine Instanz: Dienst und Fenster pollen nicht gleichzeitig."""

from __future__ import annotations

import os
from pathlib import Path


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh = None

    def acquire(self) -> str:
        """'ok', 'busy' (andere Instanz) oder 'error' (Datei nicht anlegbar)."""
        try:
            if self.path.parent:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            # 0666, damit Dienst- und Desktop-Benutzer dieselbe Sperre nehmen koennen.
            old_umask = os.umask(0)
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o666)
            finally:
                os.umask(old_umask)
            self._fh = os.fdopen(fd, 'r+b')
        except OSError:
            self._fh = None
            return 'error'
        try:
            self._fh.seek(0, os.SEEK_END)
            if self._fh.tell() == 0:
                self._fh.write(b'\0')
                self._fh.flush()
            self._fh.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return 'ok'
        except OSError:
            self._close_silent()
            return 'busy'

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            self._fh.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._close_silent()

    def _close_silent(self) -> None:
        fh = self._fh
        self._fh = None
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass
