"""Shared image replacement, clock contrast, OCR and size-capped export."""
import csv
import io
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
import unicodedata

import cv2
import numpy as np
from PIL import Image, ImageOps

from core.content_aware_fill import fill_from_background
from utils.app_paths import resource_root


def replacement(path, slot, *, panel=None, fit=False):
    with Image.open(path) as image:
        rgba = ImageOps.exif_transpose(image).convert('RGBA')
        if panel is not None:
            x, width, height = panel
            source_width = slot['w']
            source_height = round(source_width * height / width)
            rgba = ImageOps.fit(rgba, (source_width, source_height), Image.Resampling.LANCZOS)
            return dict(slot, pixels=np.array(rgba), w=source_width, h=source_height,
                        x=x, y=height / 2, scale=width / source_width)
        width, height = slot['w'], slot['h']
        if rgba.size != (width, height):
            if fit:
                rgba = ImageOps.fit(rgba, (width, height), Image.Resampling.LANCZOS)
            elif rgba.width * height != rgba.height * width:
                raise ValueError(f'{path.name} 的比例与模板图层 {slot["name"]} 不一致。')
            else:
                rgba = rgba.resize((width, height), Image.Resampling.LANCZOS)
        return dict(slot, pixels=np.array(rgba))


def lockscreen_clock(wallpaper, clock):
    # Sample the wallpaper under the upper clock glyphs in template coordinates.
    ratio = clock['scale'] / wallpaper['scale']
    offset_x = (clock['x'] - wallpaper['x']) / wallpaper['scale']
    offset_y = (clock['y'] - wallpaper['y']) / wallpaper['scale']
    aligned = Image.fromarray(wallpaper['pixels']).convert('L').transform(
        (clock['w'], clock['h']), Image.Transform.AFFINE,
        (ratio, 0, offset_x + (wallpaper['w'] - clock['w'] * ratio) / 2,
         0, ratio, offset_y + (wallpaper['h'] - clock['h'] * ratio) / 2),
        Image.Resampling.BILINEAR)
    upper = int(clock['h'] * 0.45)
    glyphs = clock['pixels'][:upper, :, 3] > 128
    dark_ink = bool(glyphs.any() and np.median(np.asarray(aligned)[:upper][glyphs]) >= 150)
    effects = dict(clock.get('effects', {}), color_overlay=dark_ink, color_overlay_color='#000000')
    return dict(clock, effects=effects), '黑色' if dark_ink else '白色'


def recognize_words(rgba, executable=None, *, language='en-US', include_lines=False):
    image = Image.fromarray(rgba)
    backdrop = Image.new('RGBA', image.size, 'white')
    backdrop.alpha_composite(image)
    scale = min(2.0, 2160 / max(image.size))
    width, height = (max(1, round(side * scale)) for side in image.size)
    enlarged = backdrop.convert('RGB').resize((width, height), Image.Resampling.LANCZOS)
    options = dict(capture_output=True, timeout=60,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if executable is None:
        with TemporaryDirectory(prefix='cep-ocr-') as folder:
            image_path = Path(folder) / 'canvas.png'
            enlarged.save(image_path)
            script = resource_root() / 'tools' / 'read_image_ocr.ps1'
            result = subprocess.run(
                ['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(script),
                 str(image_path), language],
                **options)
    else:
        stream = io.BytesIO()
        enlarged.save(stream, 'PNG')
        result = subprocess.run(
            [executable, 'stdin', 'stdout', '-l', 'eng', '--psm', '11', 'tsv'],
            input=stream.getvalue(), **options)
    if result.returncode:
        raise ValueError(result.stderr.decode('utf-8', errors='replace').strip())
    output = result.stdout.decode('utf-8-sig')
    if executable is None:
        rows = [(row['text'], row['x'], row['y'], row['w'], row['h'], row['line'])
                for row in json.loads(output)]
    else:
        rows = [(row['text'], int(row['left']), int(row['top']), int(row['width']), int(row['height']),
                 (row['block_num'], row['par_num'], row['line_num']))
                for row in csv.DictReader(io.StringIO(output), delimiter='\t')
                if row['text'] and float(row['conf']) >= 45]
    # The existing fill and sample matching use a virtual 2× canvas.
    sx, sy = 2 * image.width / width, 2 * image.height / height
    return [(text, x * sx, y * sy, w * sx, h * sy, line) if include_lines
            else (text, x * sx, y * sy, w * sx, h * sy)
            for text, x, y, w, h, line in rows]


def normalized_text(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def match_text_boxes(words, targets, *, joiner=' '):
    expected = {joiner.join(normalized_text(target).split()) for target in targets}
    limit = max(map(len, expected), default=0)
    lines = {}
    for text, x, y, width, height, line in words:
        if text.strip():
            lines.setdefault(line, []).append((normalized_text(text), x, y, width, height))
    matches = []
    for tokens in lines.values():
        start = 0
        while start < len(tokens):
            phrase, match = '', None
            for end in range(start, len(tokens)):
                phrase = joiner.join((phrase, tokens[end][0])) if phrase else tokens[end][0]
                if len(phrase) > limit:
                    break
                if phrase in expected:
                    match = end
            if match is None:
                start += 1
                continue
            chunk = tokens[start:match + 1]
            left, top = min(row[1] for row in chunk), min(row[2] for row in chunk)
            right = max(x + width for _, x, _, width, _ in chunk)
            bottom = max(y + height for _, _, y, _, height in chunk)
            matches.append((' '.join(row[0] for row in chunk), left, top, right - left, bottom - top))
            start = match + 1
    return matches


def recognize_targets(rgba, targets):
    chinese, english = [], []
    for target in targets:
        (chinese if re.search(r'[\u3400-\u9fff]', target) else english).append(target)
    boxes = []
    for language, group, joiner in (('en-US', english, ' '), ('zh-CN', chinese, '')):
        if group:
            words = recognize_words(rgba, language=language, include_lines=True)
            boxes.extend(match_text_boxes(words, group, joiner=joiner))
    return boxes


def remove_wallpics(rgba, executable=None, *, sample_words=()):
    words = list(sample_words)
    # Prefer the glyph outline location, and add any independently detected words.
    for word in recognize_words(rgba, executable):
        _, x, y, w, h = word
        if not any(abs(x + w / 2 - (sx + sw / 2)) < sw / 2
                   and abs(y + h / 2 - (sy + sh / 2)) < sh / 2
                   for _, sx, sy, sw, sh in sample_words):
            words.append(word)
    return fill_text_boxes(rgba, [word for word in words if normalized_text(word[0]) == 'wallpics'])


def fill_text_boxes(rgba, words):
    height, width = rgba.shape[:2]
    mask = np.zeros((height, width), dtype=bool)
    boxes = []
    for _, x, y, w, h in words:
        x, y, w, h = round(x), round(y), round(w), round(h)
        pad = max(2, (h + 9) // 10)
        box = (max(0, x // 2 - pad), max(0, y // 2 - pad),
               min(width, (x + w + 1) // 2 + pad), min(height, (y + h + 1) // 2 + pad))
        left, top, right, bottom = box
        if left < right and top < bottom:
            mask[top:bottom, left:right] = True
            boxes.append(box)

    source = rgba.copy()
    for left, top, right, bottom in boxes:
        x0, y0 = max(0, left - 100), max(0, top - 30)
        x1, y1 = min(width, right + 100), min(height, bottom + 30)
        crop = np.s_[y0:y1, x0:x1]
        pixels = source[crop]
        visible = pixels[:, :, 3] > 0
        selected = np.zeros(visible.shape, dtype=bool)
        selected[top - y0:bottom - y0, left - x0:right - x0] = True
        selected &= visible
        if not selected.any():
            continue
        valid = visible & ~mask[crop]
        gray = cv2.cvtColor(pixels[:, :, :3], cv2.COLOR_RGB2GRAY)
        ring = cv2.dilate(selected.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
        samples = gray[ring & valid]
        if samples.size:
            low, middle, high = np.percentile(samples, (25, 50, 75))
            # On a dark, consistent background, prevent white text and icons
            # from being copied into the removed label.
            ceiling = middle + max(32, 4 * (high - low))
            if high - low < 24 and np.percentile(gray[selected], 98) > ceiling:
                valid &= gray <= ceiling
        filled = fill_from_background(pixels[:, :, :3], selected, valid=valid)
        rgba[crop][selected, :3] = filled[selected]
    return rgba, len(boxes)


def export_jpeg(rgba, max_bytes=500_000):
    image = Image.fromarray(rgba)
    background = Image.new('RGB', image.size, 'white')
    background.paste(image, mask=image.getchannel('A'))
    for quality in range(95, 0, -3):
        stream = io.BytesIO()
        background.save(stream, format='JPEG', quality=quality, optimize=True)
        if stream.tell() <= max_bytes:
            return stream.getvalue()
    raise ValueError(f'无法在保持画布尺寸的情况下将 JPG 压缩到 {max_bytes:,} 字节以内。')
