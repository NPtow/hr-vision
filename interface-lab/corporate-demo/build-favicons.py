"""Package the approved monogram at browser icon sizes without changing its design."""
from pathlib import Path
import base64
import shutil
import struct
import math
from PIL import Image

ROOT = Path(__file__).resolve().parent / 'public'
BRAND = ROOT / 'brand'
SOURCE = BRAND / 'md-monogram-v3.png'

# Frame the approved artwork tightly for the browser's fixed-size icon slot.
# Ignore barely visible alpha noise when measuring the artwork, but retain
# the original pixels and proportions. The master logo remains unchanged.
master = Image.open(SOURCE).convert('RGBA')
bounds = master.getchannel('A').point(lambda a: 255 if a >= 16 else 0).getbbox()
assert bounds, 'Monogram is empty'
left, top, right, bottom = bounds
side = math.ceil(max(right - left, bottom - top) / 0.96)
x = round((left + right - side) / 2)
y = round((top + bottom - side) / 2)
framed = master.crop((x, y, x + side, y + side))

def save_icon(size, target):
    framed.resize((size, size), Image.Resampling.LANCZOS).save(target)

frames = []
for size in (16, 32, 48, 64):
    target = BRAND / f'md-favicon-v4-{size}.png'
    save_icon(size, target)
    if size <= 48:
        frames.append((size, target.read_bytes()))

directory = bytearray(struct.pack('<HHH', 0, 1, len(frames)))
offset = 6 + 16 * len(frames)
for size, data in frames:
    directory.extend(struct.pack('<BBBBHHII', size, size, 0, 0, 1, 32, len(data), offset))
    offset += len(data)
icon = bytes(directory) + b''.join(data for _, data in frames)
(BRAND / 'md-favicon-v4.ico').write_bytes(icon)
(ROOT / 'favicon.ico').write_bytes(icon)
for size in (32, 48):
    shutil.copy2(BRAND / f'md-favicon-v4-{size}.png', ROOT / f'favicon-{size}.png')

touch = BRAND / 'md-apple-touch-v4.png'
save_icon(180, touch)
shutil.copy2(touch, ROOT / 'apple-touch-icon.png')

# Keep the legacy SVG URL compatible with the new raster artwork as well.
encoded = base64.b64encode(frames[-1][1]).decode('ascii')
(ROOT / 'favicon.svg').write_text(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
    f'<image width="48" height="48" href="data:image/png;base64,{encoded}"/></svg>\n')
print(f'Packaged MD v4: frame {side}px from {master.width}px, '
      f'{master.width / side:.2f}x linear / {(master.width / side) ** 2:.2f}x area.')
