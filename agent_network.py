"""Statische IPv4-Konfiguration: NetworkManager auf dem Pi, netsh/PowerShell unter Windows."""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable

Runner = Callable[..., tuple[int, str, str]]

CON_LAN = 'bis-lan'
CON_WLAN = 'bis-wlan'


class NetworkError(Exception):
    pass


@dataclass(frozen=True)
class NetworkState:
    mode: str
    ip: str
    mask: str
    gateway: str
    ssid: str
    interface: str = ''


def is_ipv4(value: str) -> bool:
    if not isinstance(value, str):
        return False
    parts = value.split('.')
    if len(parts) != 4:
        return False
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return False
    return all(str(number) == part and 0 <= number <= 255 for number, part in zip(numbers, parts))


def is_netmask(value: str) -> bool:
    if not is_ipv4(value):
        return False
    packed = struct.unpack('!I', socket.inet_aton(value))[0]
    if packed == 0:
        return False
    inverted = (~packed) & 0xFFFFFFFF
    return (inverted & (inverted + 1)) == 0


def prefix_to_mask(prefix: int) -> str:
    if prefix < 0 or prefix > 32:
        raise ValueError('Ungültiger Präfix.')
    if prefix == 0:
        packed = 0
    else:
        packed = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
    return socket.inet_ntoa(struct.pack('!I', packed))


def mask_to_prefix(mask: str) -> int:
    if not is_netmask(mask):
        raise ValueError('Ungültige Subnetzmaske.')
    packed = struct.unpack('!I', socket.inet_aton(mask))[0]
    return bin(packed).count('1')


def validate_static(ip: str, mask: str, gateway: str) -> int:
    """Liefert den Präfix oder wirft NetworkError."""
    if not is_ipv4(ip):
        raise NetworkError('IP-Adresse ist ungültig.')
    if not is_netmask(mask):
        raise NetworkError('Subnetzmaske ist ungültig.')
    if not is_ipv4(gateway):
        raise NetworkError('Gateway ist ungültig.')
    prefix = mask_to_prefix(mask)
    if prefix < 1 or prefix > 30:
        raise NetworkError('Subnetzmaske ist ungültig.')
    return prefix


def _public_error(text: str, secret: str = '') -> str:
    cleaned = ' '.join((text or '').split())
    if secret:
        cleaned = cleaned.replace(secret, '***')
    if not cleaned:
        cleaned = 'Netzwerkänderung fehlgeschlagen.'
    if len(cleaned) > 160:
        cleaned = cleaned[:159] + '…'
    return cleaned


def _default_run(args: list[str], env: dict[str, str] | None = None) -> tuple[int, str, str]:
    merged = None
    if env is not None:
        merged = os.environ.copy()
        merged.update(env)
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=40,
            encoding='utf-8',
            errors='replace',
            env=merged,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, '', 'Zeitüberschreitung.'
    except FileNotFoundError:
        return 127, '', 'Programm nicht gefunden.'
    except OSError as exc:
        return 127, '', str(exc)
    return proc.returncode, proc.stdout or '', proc.stderr or ''


def _unescape_nmcli(value: str) -> str:
    return value.replace('\\:', ':').replace('\\\\', '\\')


def parse_nmcli_devices(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        parts = line.split(':')
        if len(parts) < 3 or not parts[0] or parts[0] == '--':
            continue
        rows.append({'device': parts[0], 'type': parts[1], 'state': parts[2]})
    return rows


def parse_nmcli_ip(text: str) -> tuple[str, int | None, str]:
    ip = ''
    prefix: int | None = None
    gateway = ''
    for line in text.splitlines():
        key, _, value = line.partition(':')
        value = value.strip()
        if key.startswith('IP4.ADDRESS') and not ip and '/' in value:
            addr, _, pref = value.partition('/')
            ip = addr.strip()
            if pref.isdigit():
                prefix = int(pref)
        elif key == 'IP4.GATEWAY' and value and not gateway:
            gateway = value
    return ip, prefix, gateway


def parse_ssid_list(text: str) -> list[str]:
    seen: list[str] = []
    for line in text.splitlines():
        name = _unescape_nmcli(line).strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def parse_active_ssid(text: str) -> str:
    for line in text.splitlines():
        if line.startswith('yes:'):
            return _unescape_nmcli(line[4:]).strip()
    return ''


def parse_connection_field(text: str) -> str:
    for line in text.splitlines():
        if 'GENERAL.CONNECTION' in line:
            value = line.split(':', 1)[-1].strip()
            if value and value != '--':
                return _unescape_nmcli(value)
    return ''


def parse_netsh_networks(text: str) -> list[str]:
    found: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith('SSID') or stripped.startswith('BSSID') or ':' not in stripped:
            continue
        value = stripped.split(':', 1)[1].strip()
        if value and value not in found:
            found.append(value)
    return found


def parse_netsh_interfaces_ssid(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('SSID') and not stripped.startswith('BSSID') and ':' in stripped:
            return stripped.split(':', 1)[1].strip()
    return ''


def xml_escape(value: str) -> str:
    return (
        value.replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
        .replace("'", '&apos;')
    )


def build_wlan_profile_xml(ssid: str, password: str) -> str:
    safe_ssid = xml_escape(ssid)
    safe_password = xml_escape(password)
    return (
        '<?xml version="1.0"?>\n'
        '<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">\n'
        f'  <name>{safe_ssid}</name>\n'
        '  <SSIDConfig><SSID>\n'
        f'    <name>{safe_ssid}</name>\n'
        '  </SSID></SSIDConfig>\n'
        '  <connectionType>ESS</connectionType>\n'
        '  <connectionMode>auto</connectionMode>\n'
        '  <MSM><security>\n'
        '    <authEncryption>\n'
        '      <authentication>WPA2PSK</authentication>\n'
        '      <encryption>AES</encryption>\n'
        '      <useOneX>false</useOneX>\n'
        '    </authEncryption>\n'
        '    <sharedKey>\n'
        '      <keyType>passPhrase</keyType>\n'
        '      <protected>false</protected>\n'
        f'      <keyMaterial>{safe_password}</keyMaterial>\n'
        '    </sharedKey>\n'
        '  </security></MSM>\n'
        '</WLANProfile>\n'
    )


def _first_device(devices: list[dict[str, str]], kind: str) -> str:
    connected = [
        row['device'] for row in devices
        if row['type'] == kind and row['state'] == 'connected'
    ]
    if connected:
        return connected[0]
    for row in devices:
        if row['type'] == kind:
            return row['device']
    return ''


class NmcliBackend:
    def __init__(self, run: Runner | None = None) -> None:
        self._runner = run or _default_run

    def _run(self, args: list[str], env: dict[str, str] | None = None) -> tuple[int, str, str]:
        return self._runner(args, env)

    def can_apply(self) -> tuple[bool, str]:
        code, out, err = self._run(['nmcli', 'general', 'permissions'])
        if code == 127:
            return False, 'NetworkManager (nmcli) wurde nicht gefunden.'
        if code != 0:
            return False, _public_error(err or out or 'NetworkManager ist nicht erreichbar.')
        for line in out.splitlines():
            if 'settings.modify.system' in line and line.strip().endswith('yes'):
                return True, ''
        return False, 'Keine Berechtigung, das Netzwerk zu ändern.'

    def _devices(self) -> list[dict[str, str]]:
        code, out, err = self._run(['nmcli', '-t', '-f', 'DEVICE,TYPE,STATE', 'device', 'status'])
        if code != 0:
            raise NetworkError(_public_error(err or out or 'Netzwerk konnte nicht gelesen werden.'))
        return parse_nmcli_devices(out)

    def read(self) -> NetworkState:
        devices = self._devices()
        eth = _first_device(devices, 'ethernet')
        wifi = _first_device(devices, 'wifi')
        wifi_up = any(row['device'] == wifi and row['state'] == 'connected' for row in devices) if wifi else False
        eth_up = any(row['device'] == eth and row['state'] == 'connected' for row in devices) if eth else False
        mode = 'wlan' if wifi_up and not eth_up else 'lan'
        iface = wifi if mode == 'wlan' else eth
        ip = ''
        mask = ''
        gateway = ''
        ssid = ''
        if iface:
            code, show, err = self._run([
                'nmcli', '-t', '-f', 'IP4.ADDRESS,IP4.GATEWAY', 'device', 'show', iface,
            ])
            if code == 0:
                ip, prefix, gateway = parse_nmcli_ip(show)
                if prefix is not None and 0 <= prefix <= 32:
                    mask = prefix_to_mask(prefix)
        if wifi:
            code, wout, _err = self._run([
                'nmcli', '-t', '-f', 'ACTIVE,SSID', 'device', 'wifi', 'list', 'ifname', wifi,
            ])
            if code == 0:
                ssid = parse_active_ssid(wout)
        return NetworkState(mode=mode, ip=ip, mask=mask, gateway=gateway, ssid=ssid, interface=iface)

    def prepare_wifi_scan(self) -> None:
        self._run(['nmcli', 'radio', 'wifi', 'on'])

    def scan(self) -> list[str]:
        self.prepare_wifi_scan()
        code, out, err = self._run(['nmcli', '-t', '-f', 'SSID', 'device', 'wifi', 'list', '--rescan', 'yes'])
        if code != 0:
            raise NetworkError(_public_error(err or out or 'WLAN-Suche fehlgeschlagen.'))
        return parse_ssid_list(out)

    def _must(self, args: list[str], secret: str = '') -> str:
        code, out, err = self._run(args)
        if code != 0:
            raise NetworkError(_public_error(err or out, secret))
        return out

    def _connection_names(self) -> set[str]:
        code, out, _err = self._run(['nmcli', '-t', '-f', 'NAME', 'connection', 'show'])
        if code != 0:
            return set()
        return {_unescape_nmcli(line).strip() for line in out.splitlines() if line.strip()}

    def _active_ssid(self, device: str) -> str:
        if not device:
            return ''
        code, out, _err = self._run([
            'nmcli', '-t', '-f', 'ACTIVE,SSID', 'device', 'wifi', 'list', 'ifname', device,
        ])
        if code != 0:
            return ''
        return parse_active_ssid(out)

    def _quiesce(self, device: str) -> None:
        if not device:
            return
        code, out, _err = self._run(['nmcli', '-t', '-f', 'GENERAL.CONNECTION', 'device', 'show', device])
        if code == 0:
            name = parse_connection_field(out)
            if name:
                self._run(['nmcli', 'connection', 'modify', name, 'connection.autoconnect', 'no'])
        self._run(['nmcli', 'device', 'disconnect', device])

    def apply(self, state: NetworkState, password: str) -> None:
        allowed, reason = self.can_apply()
        if not allowed:
            raise NetworkError(reason)
        prefix = validate_static(state.ip, state.mask, state.gateway)
        address = f'{state.ip}/{prefix}'
        devices = self._devices()
        eth = _first_device(devices, 'ethernet')
        wifi = _first_device(devices, 'wifi')
        names = self._connection_names()
        if state.mode == 'lan':
            if not eth:
                raise NetworkError('Kein LAN-Adapter gefunden.')
            if CON_LAN in names:
                self._must([
                    'nmcli', 'connection', 'modify', CON_LAN,
                    'connection.interface-name', eth,
                    'ipv4.method', 'manual',
                    'ipv4.addresses', address,
                    'ipv4.gateway', state.gateway,
                    'connection.autoconnect', 'yes',
                ])
            else:
                self._must([
                    'nmcli', 'connection', 'add', 'type', 'ethernet',
                    'ifname', eth, 'con-name', CON_LAN,
                    'ipv4.method', 'manual',
                    'ipv4.addresses', address,
                    'ipv4.gateway', state.gateway,
                ])
            self._must(['nmcli', 'connection', 'up', CON_LAN])
            self._quiesce(wifi)
            return
        if not wifi:
            raise NetworkError('Kein WLAN-Adapter gefunden.')
        if not state.ssid:
            raise NetworkError('WLAN-Netz fehlt.')
        if not password and (state.ssid != self._active_ssid(wifi) or CON_WLAN not in names):
            raise NetworkError('WLAN-Passwort fehlt.')
        args = [
            'connection.interface-name', wifi,
            '802-11-wireless.ssid', state.ssid,
            'ipv4.method', 'manual',
            'ipv4.addresses', address,
            'ipv4.gateway', state.gateway,
            'connection.autoconnect', 'yes',
        ]
        if password:
            args += ['802-11-wireless-security.key-mgmt', 'wpa-psk', '802-11-wireless-security.psk', password]
        if CON_WLAN in names:
            self._must(['nmcli', 'connection', 'modify', CON_WLAN, *args], secret=password)
        else:
            if not password:
                raise NetworkError('WLAN-Passwort fehlt.')
            self._must([
                'nmcli', 'connection', 'add', 'type', 'wifi',
                'ifname', wifi, 'con-name', CON_WLAN, 'ssid', state.ssid,
                'ipv4.method', 'manual',
                'ipv4.addresses', address,
                'ipv4.gateway', state.gateway,
                'wifi-sec.key-mgmt', 'wpa-psk',
                'wifi-sec.psk', password,
            ], secret=password)
        self._must(['nmcli', 'connection', 'up', CON_WLAN], secret=password)
        self._quiesce(eth)


def _adapter_kind(name: str, media: str) -> str:
    blob = f'{media} {name}'.lower()
    if '802.11' in blob or 'wifi' in blob or 'wi-fi' in blob or 'wlan' in blob or 'wireless' in blob:
        return 'wlan'
    return 'lan'


def _is_up(status: str, ip: str) -> bool:
    if (status or '').lower() in ('up', 'aktiv'):
        return True
    return bool(ip)


def parse_windows_adapters(payload) -> list[dict]:
    if payload is None:
        return []
    if isinstance(payload, dict):
        payload = [payload]
    rows = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        prefix = item.get('Prefix')
        rows.append({
            'name': str(item.get('Name') or ''),
            'status': str(item.get('Status') or ''),
            'kind': _adapter_kind(str(item.get('Name') or ''), str(item.get('MediaType') or '')),
            'ip': str(item.get('IP') or ''),
            'prefix': int(prefix) if isinstance(prefix, int) or (isinstance(prefix, str) and prefix.isdigit()) else None,
            'gateway': str(item.get('Gateway') or ''),
        })
    return [row for row in rows if row['name']]


def _pick_adapter(adapters: list[dict], kind: str) -> dict | None:
    typed = [row for row in adapters if row['kind'] == kind]
    up = [row for row in typed if _is_up(row['status'], row['ip'])]
    if up:
        return up[0]
    return typed[0] if typed else None


_PS_ADAPTERS = r'''
$ErrorActionPreference = 'Stop'
$adapters = @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue | ForEach-Object {
  $ip = Get-NetIPAddress -InterfaceAlias $_.Name -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike '169.254.*' } |
    Select-Object -First 1
  $gw = Get-NetRoute -InterfaceAlias $_.Name -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
    Select-Object -First 1
  [PSCustomObject]@{
    Name = $_.Name
    Status = [string]$_.Status
    MediaType = [string]$_.MediaType
    IP = $(if ($null -ne $ip) { $ip.IPAddress } else { '' })
    Prefix = $(if ($null -ne $ip) { [int]$ip.PrefixLength } else { $null })
    Gateway = $(if ($null -ne $gw) { [string]$gw.NextHop } else { '' })
  }
})
$adapters | ConvertTo-Json -Compress
'''

_PS_STATIC = r'''
$ErrorActionPreference = 'Stop'
$alias = $env:BIS_IF_ALIAS
$prefix = [int]$env:BIS_PREFIX
Get-NetIPAddress -InterfaceAlias $alias -AddressFamily IPv4 -ErrorAction SilentlyContinue |
  Where-Object { $_.IPAddress -notlike '169.254.*' } |
  Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
Get-NetRoute -InterfaceAlias $alias -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
  Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
Set-NetIPInterface -InterfaceAlias $alias -Dhcp Disabled -ErrorAction SilentlyContinue
New-NetIPAddress -InterfaceAlias $alias -IPAddress $env:BIS_IP -PrefixLength $prefix -DefaultGateway $env:BIS_GW | Out-Null
'''

_PS_ADAPTER_ACTION = r'''
$ErrorActionPreference = 'Stop'
if ($env:BIS_IF_ACTION -eq 'enable') {
  Enable-NetAdapter -Name $env:BIS_IF_ALIAS -Confirm:$false
} else {
  Disable-NetAdapter -Name $env:BIS_IF_ALIAS -Confirm:$false
}
'''


def is_windows_admin() -> bool:
    if os.name != 'nt':
        return False
    import ctypes
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


class WindowsBackend:
    def __init__(self, run: Runner | None = None, admin: bool | None = None) -> None:
        self._runner = run or _default_run
        self._admin_flag = admin

    def _is_admin(self) -> bool:
        if self._admin_flag is None:
            return is_windows_admin()
        return self._admin_flag

    def _run(self, args: list[str], env: dict[str, str] | None = None) -> tuple[int, str, str]:
        return self._runner(args, env)

    def can_apply(self) -> tuple[bool, str]:
        if not self._is_admin():
            return False, 'Die App muss als Administrator gestartet werden, um das Netzwerk zu ändern.'
        return True, ''

    def _ps(self, script: str, extra: dict[str, str] | None = None) -> str:
        code, out, err = self._run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
            extra,
        )
        if code != 0:
            raise NetworkError(_public_error(err or out))
        return out

    def _adapters(self) -> list[dict]:
        out = self._ps(_PS_ADAPTERS)
        raw = out.strip() or '[]'
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise NetworkError('Netzwerkadapter konnten nicht gelesen werden.') from exc
        return parse_windows_adapters(payload)

    def _current_ssid(self) -> str:
        code, out, _err = self._run(['netsh', 'wlan', 'show', 'interfaces'])
        if code != 0:
            return ''
        return parse_netsh_interfaces_ssid(out)

    def read(self) -> NetworkState:
        adapters = self._adapters()
        eth = _pick_adapter(adapters, 'lan')
        wifi = _pick_adapter(adapters, 'wlan')
        ssid = self._current_ssid()
        wifi_up = bool(wifi and _is_up(wifi['status'], wifi['ip']))
        eth_up = bool(eth and _is_up(eth['status'], eth['ip']))
        mode = 'wlan' if wifi_up and not eth_up else 'lan'
        chosen = wifi if mode == 'wlan' else eth
        if chosen is None:
            chosen = eth or wifi
        ip = mask = gateway = ''
        interface = ''
        if chosen:
            interface = chosen['name']
            ip = chosen['ip']
            gateway = chosen['gateway']
            if chosen['prefix'] is not None:
                mask = prefix_to_mask(chosen['prefix'])
        return NetworkState(
            mode=mode, ip=ip, mask=mask, gateway=gateway, ssid=ssid, interface=interface,
        )

    def prepare_wifi_scan(self) -> None:
        adapters = []
        try:
            adapters = self._adapters()
        except NetworkError:
            return
        wifi = _pick_adapter(adapters, 'wlan')
        if not wifi:
            return
        if (wifi['status'] or '').lower() in ('disabled', 'deaktiviert', 'not present'):
            self._run(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command', _PS_ADAPTER_ACTION],
                {'BIS_IF_ALIAS': wifi['name'], 'BIS_IF_ACTION': 'enable'},
            )

    def scan(self) -> list[str]:
        self.prepare_wifi_scan()
        code, out, err = self._run(['netsh', 'wlan', 'show', 'networks'])
        if code != 0:
            raise NetworkError(_public_error(err or out or 'WLAN-Suche fehlgeschlagen.'))
        return parse_netsh_networks(out)

    def _set_adapter(self, name: str, enabled: bool) -> None:
        self._ps(_PS_ADAPTER_ACTION, {
            'BIS_IF_ALIAS': name,
            'BIS_IF_ACTION': 'enable' if enabled else 'disable',
        })

    def _set_static(self, name: str, ip: str, prefix: int, gateway: str) -> None:
        self._ps(_PS_STATIC, {
            'BIS_IF_ALIAS': name,
            'BIS_IP': ip,
            'BIS_PREFIX': str(prefix),
            'BIS_GW': gateway,
        })

    def _connect_wifi(self, ssid: str, password: str) -> None:
        xml = build_wlan_profile_xml(ssid, password)
        handle = None
        path = ''
        try:
            handle, path = tempfile.mkstemp(prefix='bis-wlan-', suffix='.xml')
            os.write(handle, xml.encode('utf-8'))
            os.close(handle)
            handle = None
            code, out, err = self._run(['netsh', 'wlan', 'add', 'profile', f'filename={path}', 'user=all'])
            if code != 0:
                raise NetworkError(_public_error(err or out, password))
        finally:
            if handle is not None:
                os.close(handle)
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass
        code, out, err = self._run(['netsh', 'wlan', 'connect', f'name={ssid}', f'ssid={ssid}'])
        if code != 0:
            raise NetworkError(_public_error(err or out, password))

    def apply(self, state: NetworkState, password: str) -> None:
        allowed, reason = self.can_apply()
        if not allowed:
            raise NetworkError(reason)
        prefix = validate_static(state.ip, state.mask, state.gateway)
        adapters = self._adapters()
        eth = _pick_adapter(adapters, 'lan')
        wifi = _pick_adapter(adapters, 'wlan')
        if state.mode == 'lan':
            if not eth:
                raise NetworkError('Kein LAN-Adapter gefunden.')
            self._set_adapter(eth['name'], True)
            self._set_static(eth['name'], state.ip, prefix, state.gateway)
            if wifi and _is_up(wifi['status'], wifi['ip']):
                self._set_adapter(wifi['name'], False)
            return
        if not wifi:
            raise NetworkError('Kein WLAN-Adapter gefunden.')
        if not state.ssid:
            raise NetworkError('WLAN-Netz fehlt.')
        current = self._current_ssid()
        if not password and state.ssid != current:
            raise NetworkError('WLAN-Passwort fehlt.')
        self._set_adapter(wifi['name'], True)
        if password:
            self._connect_wifi(state.ssid, password)
        else:
            code, out, err = self._run(['netsh', 'wlan', 'connect', f'name={state.ssid}', f'ssid={state.ssid}'])
            if code != 0:
                raise NetworkError(_public_error(err or out or 'WLAN-Passwort fehlt.'))
        self._set_static(wifi['name'], state.ip, prefix, state.gateway)
        if eth and _is_up(eth['status'], eth['ip']):
            self._set_adapter(eth['name'], False)


def get_backend():
    if os.name == 'nt':
        return WindowsBackend()
    return NmcliBackend()
