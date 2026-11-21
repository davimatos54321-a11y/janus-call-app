# -*- coding: utf-8 -*-
import json
import os
import threading
import time
from datetime import datetime

import urllib3
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput


DATA_FILE = "janus_call_data.json"
DEFAULT_SIGNAL_URL = "https://httpbin.org/post"


class JanusStorage:
    def __init__(self, filepath=DATA_FILE):
        self.filepath = filepath
        self.data = self._load()

    def _default_data(self):
        return {
            "endpoint_url": DEFAULT_SIGNAL_URL,
            "contacts": [
                {"name": "Base Central", "target": "1001"},
                {"name": "Operador 02", "target": "1002"},
            ],
            "history": [],
        }

    def _load(self):
        if not os.path.exists(self.filepath):
            data = self._default_data()
            self.save(data)
            return data
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            base = self._default_data()
            base.update({k: v for k, v in loaded.items() if k in base})
            return base
        except Exception:
            data = self._default_data()
            self.save(data)
            return data

    def save(self, data=None):
        if data is not None:
            self.data = data
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def add_contact(self, name, target):
        name = name.strip() or f"Canal {target.strip()}"
        target = target.strip()
        if not target:
            return
        for c in self.data["contacts"]:
            if c["target"] == target:
                c["name"] = name
                self.save()
                return
        self.data["contacts"].insert(0, {"name": name, "target": target})
        self.save()

    def add_history(self, target, mode, status_msg):
        entry = {
            "timestamp": datetime.now().strftime("%d/%m %H:%M:%S"),
            "target": target,
            "mode": mode,
            "status": status_msg,
        }
        self.data["history"].insert(0, entry)
        self.data["history"] = self.data["history"][:30]
        self.save()


class SignalClient:
    def __init__(self):
        self.http = urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=4.0, read=6.0),
            retries=False,
        )

    def send_signal(self, url, payload):
        url = (url or "").strip()
        if not url:
            return False, "Sem endpoint configurado (modo local)"
        try:
            encoded = json.dumps(payload).encode("utf-8")
            resp = self.http.request(
                "POST",
                url,
                body=encoded,
                headers={"Content-Type": "application/json"},
            )
            if 200 <= resp.status < 300:
                return True, f"Sinal OK (HTTP {resp.status})"
            return False, f"Servidor respondeu HTTP {resp.status}"
        except Exception as exc:
            return False, f"Modo local/offline ({type(exc).__name__})"


class JanusCallRoot(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", padding=16, spacing=12, **kwargs)
        self.storage = JanusStorage()
        self.signal = SignalClient()

        self.call_state = "IDLE"  # IDLE, CALLING, ACTIVE
        self.current_target = ""
        self.current_mode = "VOZ"
        self.elapsed_seconds = 0
        self.timer_ev = None

        self._build_ui()
        self._refresh_contacts()
        self._refresh_history()

    def _build_ui(self):
        # Cabeçalho
        header = BoxLayout(orientation="horizontal", size_hint_y=None, height=56, spacing=8)
        header.add_widget(
            Label(
                text="JANUS CALL",
                font_size="26sp",
                bold=True,
                color=(0.30, 0.72, 1.0, 1),
                halign="left",
                valign="middle",
            )
        )
        self.timer_label = Label(
            text="00:00",
            font_size="22sp",
            bold=True,
            color=(0.45, 0.88, 1.0, 1),
            size_hint_x=None,
            width=90,
        )
        header.add_widget(self.timer_label)
        self.add_widget(header)

        # Painel de status
        self.status_box = Label(
            text="ESTADO: PRONTO",
            font_size="18sp",
            bold=True,
            color=(0.55, 0.85, 1.0, 1),
            size_hint_y=None,
            height=44,
        )
        self.add_widget(self.status_box)

        # Destino + nome do contato
        self.target_input = TextInput(
            text=self.storage.data["contacts"][0]["target"] if self.storage.data["contacts"] else "",
            hint_text="ID, ramal ou endereço de destino",
            multiline=False,
            font_size="20sp",
            size_hint_y=None,
            height=62,
            background_color=(0.11, 0.16, 0.24, 1),
            foreground_color=(0.95, 0.98, 1.0, 1),
            cursor_color=(0.35, 0.78, 1.0, 1),
        )
        self.add_widget(self.target_input)

        contact_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=56, spacing=8)
        self.name_input = TextInput(
            hint_text="Nome do contato (opcional)",
            multiline=False,
            font_size="17sp",
            background_color=(0.11, 0.16, 0.24, 1),
            foreground_color=(0.95, 0.98, 1.0, 1),
            cursor_color=(0.35, 0.78, 1.0, 1),
        )
        contact_row.add_widget(self.name_input)

        save_btn = Button(
            text="SALVAR",
            font_size="17sp",
            bold=True,
            size_hint_x=None,
            width=110,
            background_normal="",
            background_color=(0.12, 0.42, 0.78, 1),
            color=(1, 1, 1, 1),
        )
        save_btn.bind(on_press=self.on_save_contact)
        contact_row.add_widget(save_btn)
        self.add_widget(contact_row)

        # Modo de comunicação: VOZ / DADOS
        mode_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=56, spacing=8)
        self.mode_voz_btn = Button(
            text="MODO VOZ",
            font_size="18sp",
            bold=True,
            background_normal="",
            background_color=(0.12, 0.48, 0.92, 1),
            color=(1, 1, 1, 1),
        )
        self.mode_voz_btn.bind(on_press=lambda *_: self.set_mode("VOZ"))

        self.mode_dados_btn = Button(
            text="MODO DADOS",
            font_size="18sp",
            bold=True,
            background_normal="",
            background_color=(0.16, 0.22, 0.32, 1),
            color=(0.75, 0.88, 1.0, 1),
        )
        self.mode_dados_btn.bind(on_press=lambda *_: self.set_mode("DADOS"))

        mode_row.add_widget(self.mode_voz_btn)
        mode_row.add_widget(self.mode_dados_btn)
        self.add_widget(mode_row)

        # Botões principais de ação
        action_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=78, spacing=10)
        self.call_btn = Button(
            text="CHAMAR",
            font_size="22sp",
            bold=True,
            background_normal="",
            background_color=(0.08, 0.52, 0.95, 1),
            color=(1, 1, 1, 1),
        )
        self.call_btn.bind(on_press=self.on_call)

        self.hangup_btn = Button(
            text="ENCERRAR",
            font_size="20sp",
            bold=True,
            background_normal="",
            background_color=(0.68, 0.16, 0.18, 1),
            color=(1, 1, 1, 1),
        )
        self.hangup_btn.bind(on_press=self.on_hangup)

        action_row.add_widget(self.call_btn)
        action_row.add_widget(self.hangup_btn)
        self.add_widget(action_row)

        # Contatos rápidos
        self.add_widget(
            Label(
                text="CONTATOS / CANAIS SALVOS",
                font_size="15sp",
                bold=True,
                color=(0.45, 0.78, 1.0, 1),
                size_hint_y=None,
                height=28,
                halign="left",
            )
        )
        contacts_scroll = ScrollView(size_hint_y=None, height=110)
        self.contacts_grid = GridLayout(cols=2, spacing=8, size_hint_y=None)
        self.contacts_grid.bind(minimum_height=self.contacts_grid.setter("height"))
        contacts_scroll.add_widget(self.contacts_grid)
        self.add_widget(contacts_scroll)

        # Histórico recente
        self.add_widget(
            Label(
                text="REGISTRO DE CHAMADAS / SINAIS",
                font_size="15sp",
                bold=True,
                color=(0.45, 0.78, 1.0, 1),
                size_hint_y=None,
                height=28,
                halign="left",
            )
        )
        history_scroll = ScrollView(size_hint_y=1)
        self.history_label = Label(
            text="Nenhum registro ainda.",
            font_size="15sp",
            color=(0.80, 0.90, 1.0, 1),
            size_hint_y=None,
            halign="left",
            valign="top",
        )
        self.history_label.bind(
            texture_size=lambda inst, val: setattr(inst, "height", max(val[1] + 16, 80)),
            width=lambda inst, val: setattr(inst, "text_size", (val, None)),
        )
        history_scroll.add_widget(self.history_label)
        self.add_widget(history_scroll)

    def set_mode(self, mode):
        self.current_mode = mode
        if mode == "VOZ":
            self.mode_voz_btn.background_color = (0.12, 0.48, 0.92, 1)
            self.mode_dados_btn.background_color = (0.16, 0.22, 0.32, 1)
        else:
            self.mode_dados_btn.background_color = (0.12, 0.48, 0.92, 1)
            self.mode_voz_btn.background_color = (0.16, 0.22, 0.32, 1)
        self.status_box.text = f"ESTADO: {self.call_state} | MODO: {self.current_mode}"

    def on_save_contact(self, _instance):
        target = self.target_input.text.strip()
        if not target:
            self.status_box.text = "ESTADO: INFORME UM DESTINO PARA SALVAR"
            return
        self.storage.add_contact(self.name_input.text, target)
        self.name_input.text = ""
        self._refresh_contacts()
        self.status_box.text = f"ESTADO: DESTINO {target} SALVO"

    def _refresh_contacts(self):
        self.contacts_grid.clear_widgets()
        for c in self.storage.data["contacts"][:8]:
            btn = Button(
                text=f"{c['name']}\n[{c['target']}]",
                font_size="15sp",
                size_hint_y=None,
                height=52,
                background_normal="",
                background_color=(0.12, 0.25, 0.42, 1),
                color=(0.88, 0.96, 1.0, 1),
            )
            tgt = c["target"]
            btn.bind(on_press=lambda _i, t=tgt: self._select_and_call(t))
            self.contacts_grid.add_widget(btn)

    def _select_and_call(self, target):
        self.target_input.text = target
        self.on_call(None)

    def _refresh_history(self):
        items = self.storage.data["history"][:12]
        if not items:
            self.history_label.text = "Nenhum registro ainda."
            return
        lines = [
            f"{it['timestamp']} | {it['mode']} -> {it['target']} ({it['status']})"
            for it in items
        ]
        self.history_label.text = "\n".join(lines)

    def on_call(self, _instance):
        target = self.target_input.text.strip()
        if not target:
            self.status_box.text = "ESTADO: DIGITE UM DESTINO VÁLIDO"
            return

        self.current_target = target
        self.call_state = "CALLING"
        self.elapsed_seconds = 0
        self.timer_label.text = "00:00"
        self.status_box.text = f"CHAMANDO {target} ({self.current_mode})..."

        payload = {
            "app": "janus_call",
            "event": "call_start",
            "target": target,
            "mode": self.current_mode,
            "timestamp": datetime.now().isoformat(),
        }
        url = self.storage.data.get("endpoint_url", DEFAULT_SIGNAL_URL)

        threading.Thread(
            target=self._worker_connect,
            args=(url, payload, target),
            daemon=True,
        ).start()

    def _worker_connect(self, url, payload, target):
        ok, info = self.signal.send_signal(url, payload)
        time.sleep(0.5)
        Clock.schedule_once(lambda _dt: self._on_connected(ok, info, target), 0)

    def _on_connected(self, ok, info, target):
        if self.call_state != "CALLING":
            return
        self.call_state = "ACTIVE"
        self.status_box.text = f"EM CHAMADA: {target} | {info}"
        self.storage.add_history(target, self.current_mode, info)
        self._refresh_history()

        if self.timer_ev:
            self.timer_ev.cancel()
        self.timer_ev = Clock.schedule_interval(self._tick_timer, 1.0)

    def _tick_timer(self, _dt):
        self.elapsed_seconds += 1
        mins = self.elapsed_seconds // 60
        secs = self.elapsed_seconds % 60
        self.timer_label.text = f"{mins:02d}:{secs:02d}"

    def on_hangup(self, _instance):
        if self.timer_ev:
            self.timer_ev.cancel()
            self.timer_ev = None

        duration = self.timer_label.text
        prev_target = self.current_target or self.target_input.text.strip() or "N/A"
        prev_state = self.call_state
        self.call_state = "IDLE"
        self.current_target = ""
        self.status_box.text = f"ENCERRADA ({duration}) — PRONTO"

        if prev_state != "IDLE":
            self.storage.add_history(prev_target, self.current_mode, f"Encerrada {duration}")
            self._refresh_history()


class JanusCallApp(App):
    def build(self):
        Window.clearcolor = (0.05, 0.07, 0.11, 1)
        return JanusCallRoot()


if __name__ == "__main__":
    JanusCallApp().run()
