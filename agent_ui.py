"""Kleines Statusfenster, 320x240, fuer Windows und das 2,4"-Display am Pi."""

from __future__ import annotations

import threading
import time
import tkinter as tk
import tkinter.font as tkfont

from agent_config import AgentConfig, is_base_url, save_connection
from agent_network import IfConfig, NetworkError, NetworkState, validate_printer, validate_static

WIDTH = 320
HEIGHT = 240
BG = '#f3f0e8'
FG = '#1c1c1c'
ACCENT = '#1f4b6e'
OK = '#1d6b3a'
BAD = '#8d2f2f'
MUTED = '#5c584f'
FIELD = '#ffffff'


class AgentWindow:
    def __init__(self, root: tk.Tk, context) -> None:
        self.root = root
        self.context = context
        self._busy = False
        base = tkfont.nametofont('TkDefaultFont').copy()
        base.configure(size=8)
        self.font = base
        self.font_b = base.copy()
        self.font_b.configure(weight='bold', size=9)
        self.mode = 'lan'
        self._ssid = ''
        self._lan = IfConfig()
        self._wlan = IfConfig()
        self._seen_lan = IfConfig()
        self._seen_wlan = IfConfig()
        self._filling = False

        root.title('BIS Druck')
        root.configure(bg=BG)
        root.geometry(f'{WIDTH}x{HEIGHT}')
        root.resizable(False, False)
        if context.fullscreen:
            root.attributes('-fullscreen', True)
        root.protocol('WM_DELETE_WINDOW', self.close)

        self.shell = tk.Frame(root, width=WIDTH, height=HEIGHT, bg=BG)
        self.shell.pack_propagate(False)
        self.shell.pack()

        self.status_page = tk.Frame(self.shell, bg=BG)
        self.settings_page = tk.Frame(self.shell, bg=BG)
        self.wlan_page = tk.Frame(self.shell, bg=BG)
        self._build_status()
        self._build_settings()
        self._build_wlan()
        self.show_status()
        self._tick()

    def close(self) -> None:
        self.context.stop.set()
        self.root.destroy()

    def show_status(self) -> None:
        self._show(self.status_page)

    def show_settings(self) -> None:
        self._fill_connection()
        self._show(self.settings_page)
        snapshot = self._snapshot()
        self._set_message('Netzwerk wird gelesen …', MUTED)

        def done(state, error):
            if self._snapshot() != snapshot:
                if self.msg_var.get() == 'Netzwerk wird gelesen …':
                    self._set_message('', MUTED)
                return
            self._loaded_network(state, error)

        self._run_bg(self._load_network, done)

    def _show(self, page: tk.Frame) -> None:
        for frame in (self.status_page, self.settings_page, self.wlan_page):
            frame.pack_forget()
        page.pack(fill='both', expand=True)

    def _build_status(self) -> None:
        page = self.status_page
        head = tk.Frame(page, bg=BG)
        head.pack(fill='x', padx=8, pady=(6, 2))
        tk.Label(head, text='BIS Druck', font=self.font_b, bg=BG, fg=FG).pack(side='left')
        self.clock_var = tk.StringVar(value='--:--')
        tk.Label(head, textvariable=self.clock_var, font=self.font, bg=BG, fg=MUTED).pack(side='right')
        self.server_var = tk.StringVar(value='Server: warte')
        self.server_lbl = tk.Label(page, textvariable=self.server_var, font=self.font, bg=BG, fg=MUTED, anchor='w')
        self.server_lbl.pack(fill='x', padx=8)
        self.agent_var = tk.StringVar(value='Agent: —')
        self.job_var = tk.StringVar(value='Auftrag: —')
        self.printer_var = tk.StringVar(value='Drucker: —')
        self.error_var = tk.StringVar(value='—')
        for var in (self.agent_var, self.job_var, self.printer_var):
            tk.Label(page, textvariable=var, font=self.font, bg=BG, fg=FG, anchor='w').pack(fill='x', padx=8)
        tk.Label(
            page, textvariable=self.error_var, font=self.font, bg=BG, fg=BAD,
            anchor='w', justify='left', wraplength=300,
        ).pack(fill='x', padx=8, pady=(2, 4))
        tk.Button(
            page, text='Einstellungen', font=self.font_b, command=self.show_settings,
            bg=ACCENT, fg='white', activebackground=ACCENT, activeforeground='white',
        ).pack(fill='x', padx=8, pady=6, ipady=4)

    def _build_settings(self) -> None:
        page = self.settings_page
        self.url_var = tk.StringVar()
        self.token_var = tk.StringVar()
        self.ip_var = tk.StringVar()
        self.mask_var = tk.StringVar()
        self.gw_var = tk.StringVar()
        self.dns_var = tk.StringVar()
        self.dhcp_var = tk.BooleanVar(value=False)
        self.printer_net_var = tk.BooleanVar(value=False)
        self.msg_var = tk.StringVar()
        form = tk.Frame(page, bg=BG)
        form.pack(fill='x', padx=6, pady=2)
        self._row(form, 'Server-URL', self.url_var)
        self._row(form, 'Token', self.token_var)
        modes = tk.Frame(form, bg=BG)
        modes.pack(fill='x', pady=1)
        self.lan_btn = tk.Button(modes, text='LAN', font=self.font_b, command=lambda: self._set_mode('lan'))
        self.wlan_btn = tk.Button(modes, text='WLAN', font=self.font_b, command=lambda: self._set_mode('wlan'))
        self.lan_btn.pack(side='left', expand=True, fill='x', padx=(0, 2), ipady=1)
        self.wlan_btn.pack(side='left', expand=True, fill='x', padx=(2, 0), ipady=1)
        self.printer_chk = tk.Checkbutton(
            form, text='Druckernetz', variable=self.printer_net_var, command=self._on_printer,
            font=self.font, bg=BG, fg=FG, activebackground=BG, activeforeground=FG,
            selectcolor=FIELD, anchor='w', highlightthickness=0,
        )
        self.dhcp_chk = tk.Checkbutton(
            form, text='DHCP', variable=self.dhcp_var, command=self._on_dhcp,
            font=self.font, bg=BG, fg=FG, activebackground=BG, activeforeground=FG,
            selectcolor=FIELD, anchor='w', highlightthickness=0,
        )
        self.printer_chk.pack(fill='x')
        self.dhcp_chk.pack(fill='x')
        self.ip_row, self.ip_entry = self._row(form, 'IP-Adresse', self.ip_var)
        self.mask_row, self.mask_entry = self._row(form, 'Subnetzmaske', self.mask_var)
        self.gw_row, self.gw_entry = self._row(form, 'Gateway', self.gw_var)
        self.dns_row, self.dns_entry = self._row(form, 'DNS-Server', self.dns_var)
        self.msg_lbl = tk.Label(
            page, textvariable=self.msg_var, font=self.font, bg=BG, fg=MUTED,
            anchor='w', justify='left', wraplength=300, height=1,
        )
        self.msg_lbl.pack(fill='x', padx=6)
        buttons = tk.Frame(page, bg=BG)
        buttons.pack(fill='x', padx=6, pady=2)
        tk.Button(buttons, text='Zurück', font=self.font, command=self.show_status).pack(side='left', ipady=2)
        self.netze_btn = tk.Button(buttons, text='Netze', font=self.font, command=self.show_wlan)
        self.netze_btn.pack(side='left', padx=4, ipady=2)
        self.save_btn = tk.Button(
            buttons, text='Speichern', font=self.font_b, command=self.save,
            bg=ACCENT, fg='white', activebackground=ACCENT, activeforeground='white',
        )
        self.save_btn.pack(side='right', ipady=2)
        self._set_mode('lan')
        self._lock_address_fields()

    def _build_wlan(self) -> None:
        page = self.wlan_page
        tk.Label(page, text='WLAN', font=self.font_b, bg=BG, fg=FG).pack(anchor='w', padx=6, pady=(4, 0))
        self.ssid_list = tk.Listbox(page, font=self.font, height=6, exportselection=False, bg=FIELD)
        self.ssid_list.pack(fill='both', expand=True, padx=6, pady=2)
        self.pass_var = tk.StringVar()
        self._row(page, 'Passwort', self.pass_var, secret=True)
        self.wlan_msg = tk.StringVar()
        tk.Label(
            page, textvariable=self.wlan_msg, font=self.font, bg=BG, fg=MUTED,
            anchor='w', justify='left', wraplength=300,
        ).pack(fill='x', padx=6)
        buttons = tk.Frame(page, bg=BG)
        buttons.pack(fill='x', padx=6, pady=2)
        tk.Button(buttons, text='Zurück', font=self.font, command=self._back_from_wlan).pack(side='left', ipady=2)
        tk.Button(buttons, text='Suchen', font=self.font, command=self._search_wifi).pack(side='left', padx=4, ipady=2)
        tk.Button(
            buttons, text='Übernehmen', font=self.font_b, command=self._take_ssid,
            bg=ACCENT, fg='white', activebackground=ACCENT, activeforeground='white',
        ).pack(side='right', ipady=2)

    def _row(self, parent, label: str, variable: tk.StringVar, secret: bool = False) -> tuple[tk.Frame, tk.Entry]:
        row = tk.Frame(parent, bg=BG)
        row.pack(fill='x', pady=0)
        tk.Label(row, text=label, font=self.font, bg=BG, fg=FG, width=13, anchor='w').pack(side='left')
        entry = tk.Entry(row, textvariable=variable, font=self.font, bg=FIELD, show='*' if secret else '')
        entry.pack(side='left', fill='x', expand=True)
        return row, entry

    def _printer_on(self) -> bool:
        return self.mode == 'lan' and bool(self.printer_net_var.get())

    def _layout_fields(self) -> None:
        office = not self._printer_on()
        if self.mode == 'lan':
            self.printer_chk.pack(fill='x', before=self.ip_row)
        else:
            self.printer_chk.pack_forget()
        if office:
            self.dhcp_chk.pack(fill='x', before=self.ip_row)
            self.gw_row.pack(fill='x', after=self.mask_row)
            self.dns_row.pack(fill='x', after=self.gw_row)
        else:
            self.dhcp_chk.pack_forget()
            self.gw_row.pack_forget()
            self.dns_row.pack_forget()
        self._lock_address_fields()

    def _lock_address_fields(self) -> None:
        locked = 'disabled' if self.dhcp_var.get() and not self._printer_on() else 'normal'
        for entry in (self.ip_entry, self.mask_entry, self.gw_entry, self.dns_entry):
            entry.configure(state=locked)

    def _on_dhcp(self) -> None:
        if self._filling:
            return
        if self.dhcp_var.get() and not self._printer_on():
            seen = self._seen_lan if self.mode == 'lan' else self._seen_wlan
            self.ip_var.set(seen.ip)
            self.mask_var.set(seen.mask)
            self.gw_var.set(seen.gateway)
            self.dns_var.set(seen.dns)
        self._lock_address_fields()

    def _on_printer(self) -> None:
        if not self._filling:
            self._layout_fields()

    def _form_config(self) -> IfConfig:
        if self._printer_on():
            return IfConfig(
                ip=self.ip_var.get().strip(),
                mask=self.mask_var.get().strip(),
                printer_net=True,
            )
        return IfConfig(
            dhcp=bool(self.dhcp_var.get()),
            ip=self.ip_var.get().strip(),
            mask=self.mask_var.get().strip(),
            gateway=self.gw_var.get().strip(),
            dns=self.dns_var.get().strip(),
        )

    def _stash(self) -> None:
        cfg = self._form_config()
        if self.mode == 'lan':
            self._lan = cfg
        else:
            self._wlan = cfg

    def _fill_side(self) -> None:
        cfg = self._lan if self.mode == 'lan' else self._wlan
        self._filling = True
        try:
            self.printer_net_var.set(bool(cfg.printer_net))
            self.dhcp_var.set(bool(cfg.dhcp))
            self.ip_var.set(cfg.ip)
            self.mask_var.set(cfg.mask)
            self.gw_var.set(cfg.gateway)
            self.dns_var.set(cfg.dns)
            self._layout_fields()
        finally:
            self._filling = False

    def _snapshot(self) -> tuple:
        self._stash()
        return (self.mode, self._lan, self._wlan, self._ssid)

    def _set_mode(self, mode: str) -> None:
        if mode != self.mode:
            self._stash()
            self.mode = mode
        on = {'bg': ACCENT, 'fg': 'white', 'activebackground': ACCENT, 'activeforeground': 'white'}
        off = {'bg': '#e4dfd4', 'fg': FG, 'activebackground': '#e4dfd4', 'activeforeground': FG}
        self.lan_btn.configure(**(on if mode == 'lan' else off))
        self.wlan_btn.configure(**(on if mode == 'wlan' else off))
        self.netze_btn.configure(state='normal' if mode == 'wlan' else 'disabled')
        self._fill_side()

    def _set_message(self, text: str, color: str = MUTED) -> None:
        self.msg_var.set(text)
        self.msg_lbl.configure(fg=color)

    def _fill_connection(self) -> None:
        cfg = self.context.config
        self.url_var.set(cfg.base_url)
        self.token_var.set('')

    def _tick(self) -> None:
        self.clock_var.set(time.strftime('%H:%M'))
        if self.context.lock_state == 'busy':
            self.server_var.set('Dienst läuft bereits')
            self.server_lbl.configure(fg=BAD)
            self.agent_var.set('Bitte den Dienst beenden.')
            self.job_var.set('Auftrag: —')
            self.printer_var.set('Drucker: —')
            self.error_var.set('Es wird nicht ein zweites Mal gedruckt.')
        elif self.context.lock_state == 'error':
            self.server_var.set('Sperre nicht möglich')
            self.server_lbl.configure(fg=BAD)
            self.agent_var.set('Lock-Datei nicht beschreibbar.')
            self.job_var.set('Auftrag: —')
            self.printer_var.set('Drucker: —')
            self.error_var.set('Es wird nicht gedruckt.')
        else:
            snap = self.context.status.snapshot()
            if not self.context.config.ready and snap.server_ok is None:
                self.server_var.set('Server: nicht eingerichtet')
                self.server_lbl.configure(fg=BAD)
            elif snap.server_ok is True:
                self.server_var.set('Server: verbunden')
                self.server_lbl.configure(fg=OK)
            elif snap.server_ok is False:
                self.server_var.set('Server: nicht erreichbar')
                self.server_lbl.configure(fg=BAD)
            else:
                self.server_var.set('Server: warte')
                self.server_lbl.configure(fg=MUTED)
            name = snap.agent_name or '—'
            self.agent_var.set(f'Agent: {name}')
            if snap.last_job_id is None:
                self.job_var.set('Auftrag: —')
            else:
                result = 'OK' if snap.last_result == 'ok' else 'Fehler'
                self.job_var.set(f'Auftrag: #{snap.last_job_id} {result}')
            self.printer_var.set(f'Drucker: {snap.last_printer or "—"}')
            self.error_var.set(snap.last_error or '—')
        self.root.after(1000, self._tick)

    def _run_bg(self, work, done) -> None:
        def runner():
            try:
                result = work()
                error = None
            except Exception as exc:
                result = None
                error = exc
            self.root.after(0, lambda: done(result, error))
        threading.Thread(target=runner, daemon=True).start()

    def _load_network(self):
        return self.context.backend.read()

    def _loaded_network(self, state, error) -> None:
        if error is not None:
            self._set_message(str(error), BAD)
            return
        self._apply_state(state)
        self._set_message('', MUTED)

    def _apply_state(self, state: NetworkState) -> None:
        self._filling = True
        try:
            self._lan = state.lan
            self._wlan = state.wlan
            self._seen_lan = state.lan
            self._seen_wlan = state.wlan
            self._ssid = state.ssid
            self._fill_side()
        finally:
            self._filling = False

    def show_wlan(self) -> None:
        if self.mode != 'wlan':
            return
        self._show(self.wlan_page)
        self._search_wifi()

    def _search_wifi(self) -> None:
        self.wlan_msg.set('Suche …')
        self.ssid_list.delete(0, 'end')

        def work():
            return self.context.backend.scan()

        def done(names, error):
            if error is not None:
                self.wlan_msg.set(str(error))
                return
            for name in names or []:
                self.ssid_list.insert('end', name)
            if self._ssid:
                names = list(self.ssid_list.get(0, 'end'))
                if self._ssid in names:
                    self.ssid_list.selection_set(names.index(self._ssid))
            self.wlan_msg.set('' if names else 'Kein Netz gefunden.')

        self._run_bg(work, done)

    def _take_ssid(self) -> None:
        selected = self.ssid_list.curselection()
        if not selected:
            self.wlan_msg.set('Bitte ein WLAN wählen.')
            return
        self._ssid = self.ssid_list.get(selected[0])
        self._back_from_wlan()
        self._set_message(f'WLAN: {self._ssid}', MUTED)

    def _back_from_wlan(self) -> None:
        self._show(self.settings_page)

    def save(self) -> None:
        if self._busy:
            return
        url = self.url_var.get().strip().rstrip('/')
        typed_token = self.token_var.get().strip()
        token = typed_token or self.context.config.token
        if not is_base_url(url):
            self._set_message('Server-URL ist ungültig.', BAD)
            return
        if not token:
            self._set_message('Token fehlt.', BAD)
            return
        self._stash()
        lan = self._lan
        wlan = self._wlan
        if lan.printer_net:
            try:
                validate_printer(lan.ip, lan.mask)
            except NetworkError as exc:
                self._set_message(str(exc), BAD)
                return
            if wlan.is_empty():
                self._set_message('Für das Druckernetz braucht das WLAN einen Zugang.', BAD)
                return
        elif not lan.is_empty() and not lan.dhcp:
            try:
                validate_static(lan.ip, lan.mask, lan.gateway, lan.dns)
            except NetworkError as exc:
                self._set_message(str(exc), BAD)
                return
        if not wlan.is_empty():
            if not wlan.dhcp:
                try:
                    validate_static(wlan.ip, wlan.mask, wlan.gateway, wlan.dns)
                except NetworkError as exc:
                    self._set_message(str(exc), BAD)
                    return
            if not self._ssid:
                self._set_message('Bitte ein WLAN wählen.', BAD)
                return
        state = NetworkState(lan=lan, wlan=wlan, ssid=self._ssid)
        password = self.pass_var.get()
        self._busy = True
        self.save_btn.configure(state='disabled')
        self._set_message('Wird gespeichert …', MUTED)

        def work():
            save_connection(self.context.env_path, url, token)
            cfg = AgentConfig(
                base_url=url,
                token=token,
                poll_timeout=self.context.config.poll_timeout,
                heartbeat_every=self.context.config.heartbeat_every,
                log_file=self.context.config.log_file,
                fullscreen=self.context.config.fullscreen,
            )
            self.context.replace_config(cfg)
            allowed, reason = self.context.backend.can_apply()
            if not allowed:
                return reason, None
            self.context.backend.apply(state, password)
            try:
                fresh = self.context.backend.read()
            except NetworkError:
                fresh = None
            return '', fresh

        def done(result, error):
            self._busy = False
            self.save_btn.configure(state='normal')
            if error is None or isinstance(error, NetworkError):
                self.token_var.set('')
                self.context.ensure_loop()
            if error is not None:
                self._set_message(str(error), BAD)
                return
            text, fresh = result if isinstance(result, tuple) else (result, None)
            if fresh is not None:
                self._apply_state(fresh)
            if text:
                self._set_message(f'Zugang gespeichert. {text}', BAD)
                return
            self.pass_var.set('')
            self._set_message('Gespeichert.', OK)

        self._run_bg(work, done)


def run_ui(context) -> int:
    try:
        root = tk.Tk()
    except tk.TclError:
        print(
            'FEHLER: Kein Bildschirm verfuegbar. Als Dienst: python print_agent.py --service',
            flush=True,
        )
        return 2
    window = AgentWindow(root, context)
    if context.lock_ok and not context.config.ready:
        window.show_settings()
    elif context.lock_ok:
        context.ensure_loop()
    root.mainloop()
    return 0
