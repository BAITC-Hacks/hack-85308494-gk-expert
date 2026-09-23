"""Publish only a reviewed source snapshot, with no private history or media."""
import argparse
import ast
from datetime import datetime
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
PUBLISH = Path.home() / 'codex-antigravity-coordination' / 'publish-repo'
REMOTE = 'https://github.com/BAITC-Hacks/hack-85308494-gk-expert.git'
SECRET = re.compile(rb'sk-[A-Za-z0-9_-]{20,}|gh[opusr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----')
ROOT_FILES = ('app.py', 'requirements.txt', 'requirements-local-stt.txt', 'run_app.bat', 'build.bat',
              'build_portable.py', 'QazaqProtocolLocal.spec', 'README.md', 'LOCAL_STT.md', 'RUN_LOCAL.md', 'push_to_git.bat')
TEST_FILES = ('test_audio_lifecycle.py', 'test_audio_mixing.py', 'test_stt_offline.py',
              'test_nlp_local.py', 'test_document_local.py', 'test_upload_api.py',
              'test_audio_pipeline.py', 'test_save_dialog.py', 'test_speaker_engine.py', 'test_speaker_context.py')


def git(*args):
    result = subprocess.run(['git', *args], cwd=PUBLISH, capture_output=True, check=True)
    return result.stdout.decode('utf-8', errors='replace').strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true', help='Copy/validate only; no commit or push')
    options = parser.parse_args()
    if not PUBLISH.exists():
        PUBLISH.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', 'clone', REMOTE, str(PUBLISH)], check=True)
    origin = git('remote', 'get-url', 'origin')
    if not origin.endswith('github.com/BAITC-Hacks/hack-85308494-gk-expert.git'):
        raise RuntimeError('Unexpected publication remote')
    git('remote', 'set-url', 'origin', REMOTE)
    if git('branch', '--show-current') != 'main':
        raise RuntimeError('Publication must use main')
    if not options.prepare:
        git('fetch', 'origin', 'main')
        git('merge', '--ff-only', 'origin/main')
    paths = [ROOT / name for name in ROOT_FILES]
    paths += list((ROOT / 'core').glob('*.py'))
    paths += [p for p in (ROOT / 'ui').rglob('*') if p.is_file() and p.suffix.lower() in {'.html','.js','.css','.svg','.ttf','.woff','.woff2','.png','.jpg'}]
    paths += [ROOT / 'tests' / name for name in TEST_FILES]
    paths += [ROOT / 'scripts' / 'manual_push.py']
    paths += [ROOT / 'scripts' / 'prepare_speaker_models.py']
    payloads = {}
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            raise RuntimeError('Linked source path refused')
        data = path.read_bytes()
        if SECRET.search(data):
            raise RuntimeError('Credential-like content found in ' + path.name)
        if path.suffix == '.py':
            ast.parse(data.decode('utf-8-sig'))
        payloads[path.relative_to(ROOT).as_posix()] = data
    payloads['.gitignore'] = b'.env\n.env.*\n!.env.example\n.venv/\n__pycache__/\n*.py[cod]\nbuild/\ndist/\nmodels/\nsound/\nstorage/\n*.log\n'
    payloads['.env.example'] = b'PORT=8000\nSTT_MODEL_DIR=models/whisper-small\nSTT_DEVICE=cpu\nSTT_COMPUTE_TYPE=int8\n'
    for name, data in payloads.items():
        destination = PUBLISH / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    if options.prepare:
        print('Source snapshot prepared; no commit or push performed.')
        return
    git('add', '--', *payloads)
    for path in git('ls-files', '-z').split('\0'):
        if not path:
            continue
        if path.startswith(('sound/', 'storage/', 'models/', 'dist/')) or path == '.env':
            raise RuntimeError('Private/generated file in index: ' + path)
        blob = subprocess.run(['git', 'show', ':' + path], cwd=PUBLISH, check=True, capture_output=True).stdout
        if SECRET.search(blob):
            raise RuntimeError('Credential-like content in index: ' + path)
    if git('diff', '--cached', '--name-only'):
        git('commit', '-m', 'fix: local meeting assistant ' + datetime.now().strftime('%Y-%m-%d %H:%M'))
    git('push', 'origin', 'HEAD:main')
    head = git('rev-parse', 'HEAD')
    if git('ls-remote', 'origin', 'refs/heads/main').split()[0] != head:
        raise RuntimeError('Remote verification failed')
    print('Push verified: ' + head)


if __name__ == '__main__':
    main()
