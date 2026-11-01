"""Lesen und Schreiben der Agent-Konfiguration (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

# Auf dem Raspberry Pi liegt die Datei auf der Boot-Partition (FAT32), damit
# sie unter Windows ohne Zusatztools editierbar ist.
BOOT_ENV_CANDIDATES = (
    Path('/boot/firmware/bis-print-agent.env'),
    Path('/boot/bis-print-agent.env'),
)


@dataclass(frozen=True)
class AgentConfig:
    base_url: str
    token: str
    poll_timeout: float
    heartbeat_every: float
    log_file: str
    fullscreen: bool

    @property
    def ready(self) -> bool:
        return bool(self.base_url and self.token)


def resolve_env_path(client_dir: Path) -> Path:
    """Konfigurationsdatei: BIS_ENV_FILE, sonst Boot-Partition, sonst neben dem Skript."""
    override = os.environ.get('BIS_ENV_FILE', '').strip()
    if override:
        return Path(override).expanduser()
    if os.name != 'nt':
        for candidate in BOOT_ENV_CANDIDATES:
            if candidate.is_file():
                return candidate
    return client_dir / '.env'


def parse_env_lines(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _pick(env: Mapping[str, str], file_vals: dict[str, str], key: str, default: str = '') -> str:
    if key in env:
        return env[key]
    return file_vals.get(key, default)


def _as_float(raw: str, default: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _as_bool(raw: str) -> bool:
    return raw.strip().lower() in ('1', 'true', 'yes', 'ja')


def load_config(path: Path, environ: Mapping[str, str] | None = None) -> AgentConfig:
    """Datei laden. Gesetzte Umgebungsvariablen gewinnen, auch leere."""
    file_vals: dict[str, str] = {}
    if path.exists():
        file_vals = parse_env_lines(path.read_text(encoding='utf-8'))
    env = os.environ if environ is None else environ
    return AgentConfig(
        base_url=_pick(env, file_vals, 'BIS_BASE_URL').strip().rstrip('/'),
        token=_pick(env, file_vals, 'BIS_AGENT_TOKEN').strip(),
        poll_timeout=_as_float(_pick(env, file_vals, 'POLL_TIMEOUT', '5'), 5.0),
        heartbeat_every=_as_float(_pick(env, file_vals, 'HEARTBEAT_EVERY', '60'), 60.0),
        log_file=_pick(env, file_vals, 'LOG_FILE').strip(),
        fullscreen=_as_bool(_pick(env, file_vals, 'BIS_FULLSCREEN')),
    )


def is_base_url(value: str) -> bool:
    parts = urlparse(value.strip())
    return parts.scheme in ('http', 'https') and bool(parts.netloc) and parts.path in ('', '/')


def _quote_env(value: str) -> str:
    if value == '' or any(ch in value for ch in ' \t#"\''):
        escaped = value.replace('\\', '\\\\').replace('"', '\\"')
        return f'"{escaped}"'
    return value


def save_connection(path: Path, base_url: str, token: str) -> None:
    """BIS_BASE_URL und BIS_AGENT_TOKEN schreiben, uebrige Zeilen behalten."""
    if '\n' in base_url or '\r' in base_url or '\n' in token or '\r' in token:
        raise ValueError('Zugang enthaelt einen Zeilenumbruch.')
    text = path.read_text(encoding='utf-8') if path.exists() else ''
    updates = {
        'BIS_BASE_URL': base_url.strip().rstrip('/'),
        'BIS_AGENT_TOKEN': token.strip(),
    }
    seen: set[str] = set()
    out: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith('#') or '=' not in stripped:
            out.append(raw)
            continue
        key = stripped.split('=', 1)[0].strip()
        if key in updates:
            out.append(f'{key}={_quote_env(updates[key])}')
            seen.add(key)
        else:
            out.append(raw)
    missing = [key for key in ('BIS_BASE_URL', 'BIS_AGENT_TOKEN') if key not in seen]
    if missing and out and out[-1] != '':
        out.append('')
    for key in missing:
        out.append(f'{key}={_quote_env(updates[key])}')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(out) + '\n', encoding='utf-8')
    if os.name != 'nt':
        try:
            path.chmod(0o600)
        except OSError:
            pass
