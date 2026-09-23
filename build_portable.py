"""Build a distributable local app; never bundle .env or meeting recordings."""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
MODEL_FILES = ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt')


def main():
    source_model = ROOT / 'models' / 'whisper-small'
    for name in MODEL_FILES:
        if not (source_model / name).is_file():
            raise SystemExit(f'Model file missing: {source_model / name}. See LOCAL_STT.md.')
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm',
                    '--distpath', str(ROOT / 'dist'), '--workpath', str(ROOT / 'build' / 'local'),
                    str(ROOT / 'QazaqProtocolLocal.spec')], cwd=ROOT, check=True)
    distribution = ROOT / 'dist' / 'QazaqProtocol-Local'
    target_model = distribution / 'models' / 'whisper-small'
    target_model.mkdir(parents=True, exist_ok=True)
    for name in (*MODEL_FILES, 'README.md'):
        source = source_model / name
        if source.is_file():
            shutil.copy2(source, target_model / name)
    shutil.copy2(ROOT / 'RUN_LOCAL.md', distribution / 'README.txt')
    print(f'Ready: {distribution / "QazaqProtocol.exe"}')


if __name__ == '__main__':
    main()
