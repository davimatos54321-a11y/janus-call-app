[app]
title = Janus Call
package.name = januscall
package.domain = org.januscall.app
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json,sqlite
version = 1.0.0
requirements = python3,kivy,urllib3,certifi,idna
orientation = portrait
fullscreen = 0

android.permissions = INTERNET,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE,CAMERA,RECORD_AUDIO,MODIFY_AUDIO_SETTINGS,READ_CONTACTS,CALL_PHONE

android.api = 33
android.minapi = 21
android.ndk = 25b
android.sdk = 33
android.accept_sdk_license = True
