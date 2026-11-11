"""Download official, unmodified ProPainter release weights. No video uploads."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('raft-things.pth', 'recurrent_flow_completion.pth', 'ProPainter.pth')
BASE = 'https://github.com/sczhou/ProPainter/releases/download/v0.1.0/'


def download(name):
    target = ROOT / 'models' / name
    target.parent.mkdir(exist_ok=True)
    if not target.exists():
        partial = target.with_suffix('.download')
        print('Downloading ' + name, flush=True)
        request = urllib.request.Request(BASE + name, headers={'User-Agent': 'WatermarkRemover/1.0'})
        with urllib.request.urlopen(request, timeout=120) as source, partial.open('wb') as dest:
            while block := source.read(1024 * 1024):
                dest.write(block)
        if partial.stat().st_size < 1_000_000:
            raise RuntimeError('Incomplete model: ' + name)
        partial.replace(target)
    digest = hashlib.file_digest(target.open('rb'), 'sha256').hexdigest()
    print(f'{name}: {target.stat().st_size:,} bytes, SHA256 {digest}', flush=True)
    return name, {'url': BASE + name, 'bytes': target.stat().st_size, 'sha256': digest}


if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=3) as pool:
        manifest = dict(pool.map(download, NAMES))
    (ROOT / 'models' / 'manifest.json').write_text(json.dumps(manifest, indent=2))
