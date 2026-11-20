import base64
import json
import os
import sqlite3
import subprocess
import threading
import time
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

try:
    from kivy.app import App
    from kivy.clock import Clock
    from kivy.core.window import Window
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.button import Button
    from kivy.uix.label import Label
    from kivy.uix.scrollview import ScrollView
    from kivy.uix.textinput import TextInput
    TEM_KIVY = True
except Exception:
    TEM_KIVY = False


# ==================== CONFIGURAÇÕES DO APP ====================
MEU_NOME = "Adailton"
MEU_NUMERO = "+5519999999999"
DDI_PADRAO = "55"

# URL do seu servidor de sinalização WebRTC/mensagens (ex: https://seu-servidor.com)
URL_SINALIZACAO_PADRAO = ""

# Servidores STUN/TURN públicos/privados para travessia de rede (essenciais fora da LAN)
SERVIDORES_ICE = [
    {"urls": "stun:stun.l.google.com:19302"},
    {"urls": "stun:stun1.l.google.com:19302"},
    # Em produção comercial, adicione seu servidor TURN aqui:
    # {"urls": "turn:seu-turn.exemplo.com:3478", "username": "usuario", "credential": "senha"},
]

PASTA_DADOS = (
    "/storage/emulated/0/Download/JanusCallCommercial"
    if os.path.exists("/storage/emulated/0/Download")
    else os.path.expanduser("~/Downloads/JanusCallCommercial")
)
os.makedirs(PASTA_DADOS, exist_ok=True)
CAMINHO_DB = os.path.join(PASTA_DADOS, "janus_call.sqlite")
CAMINHO_CHAVE_PRIV = os.path.join(PASTA_DADOS, "chave_privada_x25519.pem")


def normalizar_e164(numero: str, ddi_padrao: str = DDI_PADRAO) -> str:
    limpo = "".join(ch for ch in numero.strip() if ch.isdigit())
    if not limpo:
        return ""
    if numero.strip().startswith("+"):
        return f"+{limpo}"
    if len(limpo) <= 11 and ddi_padrao:
        return f"+{ddi_padrao}{limpo}"
    return f"+{limpo}"


# ==================== CRIPTOGRAFIA E2EE (X25519 + HKDF + AES-256-GCM) ====================
class CofreSeguranca:
    def __init__(self, caminho_chave_priv: str):
        self.caminho_chave = caminho_chave_priv
        if os.path.isfile(self.caminho_chave):
            with open(self.caminho_chave, "rb") as f:
                self._priv = serialization.load_pem_private_key(f.read(), password=None)
        else:
            self._priv = x25519.X25519PrivateKey.generate()
            pem = self._priv.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            with open(self.caminho_chave, "wb") as f:
                f.write(pem)
        self._pub = self._priv.public_key()

    def chave_publica_b64(self) -> str:
        raw = self._pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return base64.b64encode(raw).decode("ascii")

    def derivar_chave_sessao(self, pub_remota_b64: str, info: bytes = b"JanusCall-v1") -> bytes:
        pub_bytes = base64.b64decode(pub_remota_b64.encode("ascii"))
        pub_remota = x25519.X25519PublicKey.from_public_bytes(pub_bytes)
        segredo = self._priv.exchange(pub_remota)
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=info,
        ).derive(segredo)

    @staticmethod
    def cifrar(chave_aes: bytes, texto_plano: str, aad: str = "") -> str:
        nonce = os.urandom(12)
        ct = AESGCM(chave_aes).encrypt(
            nonce,
            texto_plano.encode("utf-8"),
            aad.encode("utf-8") if aad else None,
        )
        return base64.b64encode(nonce + ct).decode("ascii")

    @staticmethod
    def decifrar(chave_aes: bytes, pacote_b64: str, aad: str = "") -> str:
        raw = base64.b64decode(pacote_b64.encode("ascii"))
        if len(raw) < 13:
            raise ValueError("Pacote cifrado inválido.")
        nonce, ct = raw[:12], raw[12:]
        pt = AESGCM(chave_aes).decrypt(
            nonce,
            ct,
            aad.encode("utf-8") if aad else None,
        )
        return pt.decode("utf-8")


# ==================== BANCO DE DADOS (CONTATOS E CHAMADAS) ====================
class BancoChamadas:
    def __init__(self, caminho_db: str):
        self.caminho_db = caminho_db
        self.lock = threading.Lock()
        self._init_db()

    def _conn(self):
        return sqlite3.connect(self.caminho_db, check_same_thread=False)

    def _init_db(self):
        with self.lock, self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS contatos (
                    numero TEXT PRIMARY KEY,
                    nome TEXT NOT NULL,
                    pubkey_b64 TEXT DEFAULT '',
                    atualizado_em INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS historico (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    numero TEXT NOT NULL,
                    canal TEXT NOT NULL,
                    tipo_midia TEXT NOT NULL,
                    status TEXT NOT NULL,
                    criado_em INTEGER NOT NULL
                )
                """
            )

    def salvar_contato(self, numero: str, nome: str = "", pubkey_b64: str = ""):
        num = normalizar_e164(numero)
        if not num:
            return None
        agora = int(time.time())
        with self.lock, self._conn() as conn:
            atual = conn.execute("SELECT nome, pubkey_b64 FROM contatos WHERE numero = ?", (num,)).fetchone()
            nome_final = nome.strip() or (atual[0] if atual else num)
            pub_final = pubkey_b64.strip() or (atual[1] if atual else "")
            conn.execute(
                """
                INSERT INTO contatos (numero, nome, pubkey_b64, atualizado_em)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(numero) DO UPDATE SET
                    nome = excluded.nome,
                    pubkey_b64 = excluded.pubkey_b64,
                    atualizado_em = excluded.atualizado_em
                """,
                (num, nome_final, pub_final, agora),
            )
        return num

    def buscar_contato(self, numero: str):
        num = normalizar_e164(numero)
        with self.lock, self._conn() as conn:
            r = conn.execute("SELECT numero, nome, pubkey_b64 FROM contatos WHERE numero = ?", (num,)).fetchone()
        if not r:
            return None
        return {"numero": r[0], "nome": r[1], "pubkey_b64": r[2]}

    def listar_contatos(self):
        with self.lock, self._conn() as conn:
            rows = conn.execute("SELECT numero, nome, pubkey_b64 FROM contatos ORDER BY nome ASC").fetchall()
        return [{"numero": r[0], "nome": r[1], "pubkey_b64": r[2]} for r in rows]

    def registrar_chamada(self, numero: str, canal: str, tipo_midia: str, status: str):
        num = normalizar_e164(numero)
        with self.lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO historico (numero, canal, tipo_midia, status, criado_em)
                VALUES (?, ?, ?, ?, ?)
                """,
                (num, canal, tipo_midia, status, int(time.time())),
            )


# ==================== MOTOR DE CHAMADAS (WEBRTC + TELEFONE) ====================
class CentralChamadas:
    def __init__(self, meu_numero: str, on_log=None):
        self.meu_numero = normalizar_e164(meu_numero)
        self.cofre = CofreSeguranca(CAMINHO_CHAVE_PRIV)
        self.db = BancoChamadas(CAMINHO_DB)
        self.on_log = on_log or (lambda s: print(s))

        self.lock = threading.Lock()
        self.url_sinalizacao = URL_SINALIZACAO_PADRAO
        self.estado = "LIVRE"       # LIVRE | CHAMANDO | RECEBENDO | EM_CHAMADA
        self.midia = "VOZ"          # VOZ | VIDEO
        self.destino_atual = ""

    def log(self, msg: str):
        self.on_log(msg)

    def configurar_servidor(self, url: str):
        with self.lock:
            self.url_sinalizacao = url.strip().rstrip("/")
        self.log(f"[SERVIDOR] Sinalização configurada: {self.url_sinalizacao or 'nenhuma'}")

    def ligar_webrtc(self, numero_raw: str, midia: str = "VOZ"):
        num = normalizar_e164(numero_raw)
        if not num:
            self.log("[ERRO] Digite um número válido.")
            return

        midia = midia.upper()
        with self.lock:
            self.estado = "CHAMANDO"
            self.midia = midia
            self.destino_atual = num
            url = self.url_sinalizacao

        self.db.registrar_chamada(num, "WEBRTC", midia, "CHAMANDO")
        self.log(f"[CHAMADA {midia}] Iniciando sessão segura para {num}...")

        if not url:
            self.log(
                "[AVISO REDE] Nenhuma URL de sinalização configurada. "
                "Para chamadas entre celulares na internet/Claro, informe o servidor de sinalização e TURN."
            )
            return

        def _enviar_convite():
            try:
                payload = {
                    "de": self.meu_numero,
                    "para": num,
                    "midia": midia,
                    "pubkey": self.cofre.chave_publica_b64(),
                    "ice_servers": SERVIDORES_ICE,
                }
                req = urllib.request.Request(
                    f"{url}/call/invite",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=6) as resp:
                    self.log(f"[SINALIZAÇÃO] Convite {midia} enviado para {num} (HTTP {resp.status}).")
            except Exception as e:
                self.log(f"[ERRO SINALIZAÇÃO] Falha ao contatar servidor: {e}")

        threading.Thread(target=_enviar_convite, daemon=True).start()

    def atender_chamada(self):
        with self.lock:
            num = self.destino_atual
            if not num:
                self.log("[AVISO] Nenhuma chamada pendente para atender.")
                return
            self.estado = "EM_CHAMADA"
        self.db.registrar_chamada(num, "WEBRTC", self.midia, "ATENDIDA")
        self.log(f"[EM CHAMADA {self.midia}] Conectado com {num}.")

    def desligar_chamada(self):
        with self.lock:
            num = self.destino_atual
            self.estado = "LIVRE"
            self.destino_atual = ""
        if num:
            self.db.registrar_chamada(num, "WEBRTC", self.midia, "ENCERRADA")
        self.log("[CHAMADA ENCERRADA] Linha liberada.")

    def ligar_telefone_padrao(self, numero_raw: str) -> bool:
        """Abre o discador nativo do Android para chamada telefônica tradicional (rede da operadora)."""
        num = normalizar_e164(numero_raw)
        if not num:
            self.log("[ERRO] Digite um número válido para ligar.")
            return False

        self.db.registrar_chamada(num, "TELEFONE_NATIVO", "VOZ", "DISCADO")
        self.log(f"[TELEFONE] Abrindo discador padrão para {num}...")

        try:
            subprocess.run(
                ["am", "start", "-a", "android.intent.action.DIAL", "-d", f"tel:{num}"],
                check=True,
            )
            return True
        except Exception as e:
            self.log(f"[AVISO] Discador nativo indisponível neste ambiente ({e}).")
            return False


# ==================== INTERFACE DO APLICATIVO (KIVY) ====================
if TEM_KIVY:
    class JanusCallApp(App):
        def build(self):
            Window.clearcolor = (0.05, 0.07, 0.11, 1)
            self.central = CentralChamadas(MEU_NUMERO, on_log=self._log_ui)

            raiz = BoxLayout(orientation="vertical", padding=12, spacing=10)

            raiz.add_widget(
                Label(
                    text=f"JANUS CALL  |  {MEU_NOME} ({MEU_NUMERO})",
                    size_hint_y=None,
                    height=44,
                    bold=True,
                    color=(0.35, 0.8, 1, 1),
                )
            )

            self.txt_numero = TextInput(
                hint_text="Número (ex: 19988887777 ou +14155550199)",
                multiline=False,
                size_hint_y=None,
                height=52,
                font_size=19,
            )
            raiz.add_widget(self.txt_numero)

            self.txt_nome = TextInput(
                hint_text="Nome do contato (opcional)",
                multiline=False,
                size_hint_y=None,
                height=44,
            )
            raiz.add_widget(self.txt_nome)

            self.txt_servidor = TextInput(
                hint_text="URL servidor sinalização (ex: https://api.seudominio.com)",
                multiline=False,
                size_hint_y=None,
                height=44,
            )
            raiz.add_widget(self.txt_servidor)

            btn_srv = Button(text="SALVAR SERVIDOR", size_hint_y=None, height=44, background_color=(0.25, 0.35, 0.55, 1))
            btn_srv.bind(on_press=lambda _: self.central.configurar_servidor(self.txt_servidor.text))
            raiz.add_widget(btn_srv)

            # Linha 1: Chamada de Voz e Videochamada (App / WebRTC)
            l1 = BoxLayout(size_hint_y=None, height=56, spacing=8)
            b_voz = Button(text="CHAMAR VOZ", background_color=(0.12, 0.65, 0.35, 1), bold=True)
            b_voz.bind(on_press=lambda _: self.central.ligar_webrtc(self.txt_numero.text, "VOZ"))

            b_vid = Button(text="VIDEOCHAMADA", background_color=(0.15, 0.45, 0.85, 1), bold=True)
            b_vid.bind(on_press=lambda _: self.central.ligar_webrtc(self.txt_numero.text, "VIDEO"))

            l1.add_widget(b_voz)
            l1.add_widget(b_vid)
            raiz.add_widget(l1)

            # Linha 2: Atender, Desligar e Ligação Telefônica Padrão (tel:)
            l2 = BoxLayout(size_hint_y=None, height=54, spacing=8)
            b_at = Button(text="ATENDER", background_color=(0.18, 0.72, 0.48, 1), bold=True)
            b_at.bind(on_press=lambda _: self.central.atender_chamada())

            b_de = Button(text="DESLIGAR", background_color=(0.82, 0.22, 0.22, 1), bold=True)
            b_de.bind(on_press=lambda _: self.central.desligar_chamada())

            b_tel = Button(text="LIGAR (TEL)", background_color=(0.45, 0.35, 0.75, 1), bold=True)
            b_tel.bind(on_press=lambda _: self.central.ligar_telefone_padrao(self.txt_numero.text))

            l2.add_widget(b_at)
            l2.add_widget(b_de)
            l2.add_widget(b_tel)
            raiz.add_widget(l2)

            # Linha 3: Agenda
            l3 = BoxLayout(size_hint_y=None, height=46, spacing=8)
            b_sal = Button(text="SALVAR CONTATO", background_color=(0.25, 0.35, 0.55, 1))
            b_sal.bind(on_press=self._salvar_contato)

            b_ver = Button(text="AGENDA", background_color=(0.25, 0.35, 0.55, 1))
            b_ver.bind(on_press=self._ver_agenda)

            l3.add_widget(b_sal)
            l3.add_widget(b_ver)
            raiz.add_widget(l3)

            self.lbl_log = Label(
                text="Pronto para testes de chamada, agenda e E2EE.\n",
                size_hint_y=None,
                halign="left",
                valign="top",
                color=(0.88, 0.94, 1, 1),
            )
            self.lbl_log.bind(texture_size=lambda inst, val: setattr(inst, "height", max(200, val[1] + 20)))
            self.lbl_log.bind(size=lambda inst, val: setattr(inst, "text_size", (val[0], None)))

            scroll = ScrollView(size_hint=(1, 1))
            scroll.add_widget(self.lbl_log)
            raiz.add_widget(scroll)

            return raiz

        def _log_ui(self, msg: str):
            Clock.schedule_once(lambda _: self._append(msg), 0)

        def _append(self, msg: str):
            self.lbl_log.text += f"{msg}\n"

        def _salvar_contato(self, _):
            num = self.central.db.salvar_contato(self.txt_numero.text, self.txt_nome.text)
            if num:
                self._append(f"[AGENDA] Contato salvo: {num}")

        def _ver_agenda(self, _):
            contatos = self.central.db.listar_contatos()
            if not contatos:
                self._append("[AGENDA] Vazia.")
                return
            linhas = ["[CONTATOS]:"] + [f" - {c['nome']} ({c['numero']})" for c in contatos]
            self._append("\n".join(linhas))


if __name__ == "__main__":
    if TEM_KIVY:
        JanusCallApp().run()
    else:
        print("Kivy não instalado neste ambiente. Instale 'kivy' e 'cryptography' para rodar a interface do app.")
