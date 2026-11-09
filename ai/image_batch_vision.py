"""Bounded contact sheets and validated image picks for visual batch planning."""
import base64
import io
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from core.image_template_batch import IMAGE_SUFFIXES, LayerSource

MAX_CANDIDATES = 48


def candidate_images(paths):
    roots = tuple(dict.fromkeys(Path(value).resolve() for value in paths if Path(value).is_dir()))
    images = {}
    for root in roots:
        for folder, dirs, files in os.walk(root):
            dirs[:] = sorted(name for name in dirs if not (Path(folder) / name).is_symlink())
            for name in sorted(files):
                path = Path(folder) / name
                if path.suffix.lower() not in IMAGE_SUFFIXES or not path.resolve().is_relative_to(root):
                    continue
                images.setdefault(path.resolve(), root)
                if len(images) > MAX_CANDIDATES:
                    raise ValueError(f'按画面选图每次最多查看 {MAX_CANDIDATES} 张，请给我一个更小的候选目录。')
    if not images:
        raise ValueError('请给出包含候选图片的文件夹。')
    return roots, {f'I{index}': (path, root) for index, (path, root) in enumerate(images.items(), 1)}


def contact_sheets(candidates):
    entries = list(candidates.items())
    for start in range(0, len(entries), 12):
        batch = entries[start:start + 12]
        sheet = Image.new('RGB', (640, 156 * ((len(batch) + 3) // 4)), '#ececec')
        draw = ImageDraw.Draw(sheet)
        for index, (key, (path, _)) in enumerate(batch):
            x, y = (index % 4) * 160, (index // 4) * 156
            with Image.open(path) as raw:
                thumb = ImageOps.exif_transpose(raw).convert('RGB')
                thumb.thumbnail((150, 128))
                sheet.paste(thumb, (x + (160 - thumb.width) // 2, y + (132 - thumb.height) // 2))
            draw.text((x + 8, y + 135), key, fill='black')
        stream = io.BytesIO()
        sheet.save(stream, format='JPEG', quality=86)
        data = base64.b64encode(stream.getvalue()).decode('ascii')
        yield {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{data}', 'detail': 'high'}}


def validate_selections(rows, layers, candidates):
    ids = {layer['id'] for layer in layers}
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_CANDIDATES:
        raise ValueError('Agent 没有选出可用的搭配，请补充你想要的画面或图层。')
    order = None
    jobs = []
    for number, row in enumerate(rows, 1):
        mapping = {}
        if not isinstance(row, list):
            raise ValueError('Agent 返回的搭配格式无效。')
        for pick in row:
            if not isinstance(pick, dict):
                raise ValueError('Agent 返回的搭配格式无效。')
            layer_id, key = pick.get('layer_id'), pick.get('image_id')
            if (type(layer_id) is not int or layer_id not in ids or layer_id in mapping
                    or not isinstance(key, str) or key not in candidates):
                raise ValueError('Agent 选中了模板或候选目录之外的素材。')
            mapping[layer_id] = candidates[key][0]
        if order is None:
            order = tuple(mapping)
        if not order or mapping.keys() != set(order):
            raise ValueError('Agent 的各组搭配需要使用同一套图层。')
        jobs.append((f'搭配 {number}', tuple(mapping[layer_id] for layer_id in order)))
    return tuple(LayerSource(layer_id, '*') for layer_id in order), tuple(jobs)
