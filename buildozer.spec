[app]

# Nome do aplicativo
title = Janus Call

# Nome do pacote (sem espaços ou caracteres especiais)
package.name = januscall

# Domínio do pacote
package.domain = org.januscall.app

# Pasta onde está o main.py
source.dir = .

# Extensões incluídas no pacote
source.include_exts = py,png,jpg,kv,atlas,db,json

# Versão do app
version = 0.1.0

# Dependências iniciais enxutas para estabilizar o build
requirements = python3,kivy,urllib3,certifi,idna

# Orientação da tela
orientation = portrait
fullscreen = 0

# Permissões Android para agenda, chamadas e futura voz/vídeo
android.permissions = INTERNET,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE,CAMERA,RECORD_AUDIO,MODIFY_AUDIO_SETTINGS,READ_CONTACTS,CALL_PHONE

# AndroidX e versões de API / SDK / NDK
android.enable_androidx = True
android.api = 33
android.minapi = 21
android.ndk = 25b
android.sdk = 33
android.accept_sdk_license = True

# Branch do python-for-android e arquitetura alvo
p4a.branch = master
android.archs = arm64-v8a


[buildozer]

# Nível de log detalhado para diagnosticar qualquer falha
log_level = 2

# Permite rodar como root no ambiente do runner sem aviso bloqueante
warn_on_root = 1
