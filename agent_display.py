"""Idle-Zustand und Display-Helligkeit. Kein Tkinter."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger('bis.print_agent')

BLANK_AFTER = 30.0
_POWER_ROOTS = (Path('/sys/class/backlight'), Path('/sys/class/leds'))
_warned = False


class IdleBlank:
    """Wartet timeout Sekunden. Der erste Kontakt im Schlaf wird geschluckt."""

    def __init__(self, timeout: float = BLANK_AFTER) -> None:
        self.timeout = timeout
        self.asleep = False
        self._last: float | None = None

    def activity(self, now: float) -> bool:
        """True: Ereignis nur zum Wecken, nicht weitergeben."""
        if self.asleep:
            self.asleep = False
            self._last = now
            return True
        self._last = now
        return False

    def tick(self, now: float) -> bool:
        """True: jetzt einschlafen."""
        if self.asleep:
            return False
        if self._last is None:
            self._last = now
        if now - self._last >= self.timeout:
            self.asleep = True
            return True
        return False

    def remaining(self, now: float) -> float:
        if self.asleep or self._last is None:
            return self.timeout
        return max(0.0, self.timeout - (now - self._last))


def brightness_for(on: bool, max_brightness: str) -> str:
    """Zielwert für eine sysfs-brightness-Datei."""
    if not on:
        return '0'
    text = max_brightness.strip()
    if not text or text == '0':
        return '1'
    return text


def find_brightness(roots: list[Path]) -> Path | None:
    """Erste beschreibbare brightness unter den angegebenen Wurzeln."""
    for root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            path = child / 'brightness'
            if path.is_file() and os.access(path, os.W_OK):
                return path
    return None


def set_brightness(path: Path, on: bool) -> str:
    """Helligkeit schreiben. max_brightness liegt daneben, falls vorhanden."""
    maximum = ''
    max_path = path.parent / 'max_brightness'
    if max_path.is_file():
        maximum = max_path.read_text(encoding='utf-8')
    level = brightness_for(on, maximum)
    path.write_text(level, encoding='utf-8')
    return level


def set_display_power(on: bool, roots: list[Path] | None = None) -> bool:
    """Panel an oder aus. True, wenn ein Weg gegriffen hat."""
    search = list(_POWER_ROOTS if roots is None else roots)
    path = find_brightness(search)
    if path is not None:
        try:
            set_brightness(path, on)
            return True
        except OSError:
            logger.warning('Helligkeit %s nicht beschreibbar', path)
    if _wlopm(on) or _xset(on):
        return True
    global _warned
    if not _warned:
        _warned = True
        logger.info('Display laesst sich nicht abschalten. Die Flaeche bleibt schwarz.')
    return False


def _wlopm(on: bool) -> bool:
    if shutil.which('wlopm') is None:
        return False
    mode = '--on' if on else '--off'
    return _run(['wlopm', mode, '*'])


def _xset(on: bool) -> bool:
    if not os.environ.get('DISPLAY') or shutil.which('xset') is None:
        return False
    mode = 'on' if on else 'off'
    return _run(['xset', 'dpms', 'force', mode])


def _run(argv: list[str]) -> bool:
    try:
        result = subprocess.run(
            argv,
            check=False,
            timeout=3,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0
