"""Refresh isolated reusable adapter snapshot; run only during artifact preparation."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]
origin = root / 'interface-lab/server'
dest = Path(__file__).resolve().parent / 'src/source_daily/_vendor'
manifest = {}
for name in ('daily_media', 'materials_daily', 'materials_capture', 'materials_http', 'materials_storage', 'media_runtime'):
    raw = (origin / f'{name}.py').read_bytes()
    text = raw.decode().replace('import observability\n', 'from . import observability\n')
    text = text.replace('from domain import ', 'from .support import ')
    text = text.replace('from materials_capture import ', 'from .materials_capture import ')
    text = text.replace('from media_runtime import ', 'from .media_runtime import ')
    (dest / f'{name}.py').write_text(text)
    manifest[name] = {'source': f'interface-lab/server/{name}.py', 'sha256': hashlib.sha256(raw).hexdigest(),
                      'vendored_sha256': hashlib.sha256(text.encode()).hexdigest()}
(dest / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
