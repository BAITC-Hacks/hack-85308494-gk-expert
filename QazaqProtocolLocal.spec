# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('ui', 'ui')]
binaries = []
hiddenimports = ['core.self_test', 'pyaudiowpatch', 'soundfile', 'sounddevice', 'docx', 'dotenv', 'mss', 'scipy.signal']
for package in ('faster_whisper', 'ctranslate2', 'av', 'onnxruntime', 'tokenizers', 'webview', 'reportlab', 'sherpa_onnx'):
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports

a = Analysis(['app.py'], pathex=[], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['openai', 'torch', 'tensorflow', 'transformers', 'matplotlib', 'IPython'],
             noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='QazaqProtocol',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='QazaqProtocol-Local')
