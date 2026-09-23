"""Provision public local models; never reads or uploads meeting data."""
from pathlib import Path
import hashlib
import json
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / 'models' / 'speakers'
RELEASE = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/'
EXPECTED = {'segmentation.onnx': '220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079',
            'embedding.onnx': 'ad4a1802485d8b34c722d2a9d04249662f2ece5d28a7a039063ca22f515a789e'}


def download(url, path):
    if not path.is_file():
        request = urllib.request.Request(url, headers={'User-Agent': 'QazaqProtocol-model-setup'})
        with urllib.request.urlopen(request, timeout=120) as response, path.with_suffix(path.suffix + '.part').open('wb') as out:
            while block := response.read(1024 * 1024):
                out.write(block)
        path.with_suffix(path.suffix + '.part').replace(path)


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    cache = ROOT / '.cache' / 'speaker-models'
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / 'segmentation.tar.bz2'
    download(RELEASE + 'speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2', archive)
    prefix = 'sherpa-onnx-pyannote-segmentation-3-0/'
    with tarfile.open(archive) as tar:
        for name, target in [('model.onnx', 'segmentation.onnx'), ('LICENSE', 'SEGMENTATION_LICENSE'), ('README.md', 'SEGMENTATION_README.md')]:
            with tar.extractfile(prefix + name) as stream:
                (DEST / target).write_bytes(stream.read())
    download(RELEASE + 'speaker-recongition-models/nemo_en_titanet_small.onnx', DEST / 'embedding.onnx')
    download('https://raw.githubusercontent.com/NVIDIA/NeMo/main/LICENSE', DEST / 'EMBEDDING_LICENSE')
    for name, expected in EXPECTED.items():
        if hashlib.sha256((DEST / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Model checksum mismatch: ' + name)
    manifest = {'segmentation_source': RELEASE + 'speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2',
                'embedding_source': RELEASE + 'speaker-recongition-models/nemo_en_titanet_small.onnx',
                'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in DEST.iterdir() if p.is_file() and p.name != 'manifest.json'}}
    (DEST / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    download(RELEASE + 'speaker-segmentation-models/0-four-speakers-zh.wav', cache / 'four-speakers.wav')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
