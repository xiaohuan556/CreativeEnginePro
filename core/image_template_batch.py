"""Local execution for an image workbench template and a folder of theme groups."""
from dataclasses import dataclass, field
from datetime import date
import ntpath
from pathlib import Path, PurePosixPath, PureWindowsPath
import random
import re
import subprocess

import cv2
import numpy as np
from PyQt6.QtGui import QImage

from core.image_batch_processing import export_jpeg, fill_text_boxes, recognize_targets, replacement
from core.image_render_worker import ImageRenderContext

IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}
_CLOCK_NAME = re.compile(r'时钟|钟表|(?<![a-z])clock(?![a-z])|^IMG_46AE31A46A62-1(?:\.[^.]+)?$', re.I)


def walk_layers(layers):
    for layer in layers:
        if layer['visible']:
            yield layer
            yield from walk_layers(layer.get('children', ()))


def image_slots(snapshot):
    return [layer for layer in walk_layers(snapshot['layers'])
            if layer['kind'] == 'image' and 'pixels' in layer
            and not layer.get('frames')]


def source_patterns(rule):
    patterns = tuple(part.strip().replace('\\', '/') for part in rule.split('|') if part.strip())
    if not patterns or any(PureWindowsPath(pattern).drive or PurePosixPath(pattern).is_absolute()
                           or '..' in PurePosixPath(pattern).parts or '**' in pattern
                           for pattern in patterns):
        raise ValueError('取图规则须使用主题内的相对路径，多个候选用 | 分隔。')
    return patterns


def filename_prefix(pattern):
    prefix = pattern.replace('{date}', date.today().strftime('%Y%m%d'))
    if not prefix or len(prefix) > 120 or re.search(r'[<>:"/\\|?*{}\x00-\x1f]', prefix):
        raise ValueError('请填写有效的文件名前缀，日期可用 {date}。')
    is_reserved = getattr(ntpath, 'isreserved', lambda name: PureWindowsPath(name).is_reserved())
    if is_reserved(prefix + '1.jpg'):
        raise ValueError('文件名前缀使用了 Windows 保留名称。')
    return prefix


@dataclass(frozen=True)
class LayerSource:
    layer_id: int
    rule: str
    folder: Path | None = None
    key_pattern: str = '{key}'


@dataclass(frozen=True)
class BatchOptions:
    bindings: tuple[LayerSource, ...]
    mode: str
    targets: tuple
    name: str
    first_index: int
    max_kb: int
    pairing: str = 'same'
    cross_layers: tuple[int, ...] = ()
    seed: int = 0
    selected_jobs: tuple = ()
    input_roots: tuple[Path, ...] = ()

    def validate(self, snapshot):
        if snapshot.get('artboards'):
            raise ValueError('模板批处理需要普通画布。')
        available = {layer['id']: layer for layer in image_slots(snapshot)}
        ids = [item.layer_id for item in self.bindings]
        if not ids or len(set(ids)) != len(ids) or any(layer not in available for layer in ids):
            raise ValueError('请说明要替换的图片图层，以及各图层的取图位置。')
        if self.mode not in {'grouped', 'matched', 'ordered', 'selected'}:
            raise ValueError('无法确定素材的分组方式。')
        if self.mode == 'selected' and (not self.selected_jobs
                or any(len(paths) != len(ids) for _, paths in self.selected_jobs)):
            raise ValueError('Agent 尚未为所有指定图层选好图片。')
        if self.pairing not in {'same', 'next', 'shuffle'} or (
                self.pairing != 'same' and (self.mode != 'grouped'
                or not set(self.cross_layers) < set(ids) or not self.cross_layers)):
            raise ValueError('请说明保留哪个图层，以及哪些图层需要跨主题换图。')
        for item in self.bindings:
            source_patterns(item.rule)
            if item.key_pattern.count('{key}') != 1:
                raise ValueError('文件名配对规则需要包含一个 {key}。')
            if (self.mode in {'matched', 'ordered'} and item.folder is None) or (
                    item.folder and not item.folder.is_dir()):
                raise ValueError(f'图层「{available[item.layer_id]["name"]}」的素材目录不存在。')
        if self.first_index < 0 or self.max_kb < 1:
            raise ValueError('请填写有效的起始序号和大小上限。')
        filename_prefix(self.name)


def find_images(group, patterns):
    for pattern in patterns:
        images = sorted((path for path in group.glob(pattern)
                         if path.suffix.lower() in IMAGE_SUFFIXES and path.is_file()
                         and path.resolve().is_relative_to(group)),
                        key=lambda path: (path.name.casefold(), str(path).casefold(), str(path)))
        if images:
            return images
    return []


def index_images(images, key_pattern):
    before, after = key_pattern.casefold().split('{key}')
    result = {}
    for path in images:
        stem = path.stem.casefold()
        if not stem.startswith(before) or not stem.endswith(after):
            continue
        end = len(stem) - len(after)
        key = stem[len(before):end]
        if not key:
            continue
        if key in result:
            raise ValueError(f'素材编号「{key}」对应多张图，请说明要用哪张。')
        result[key] = path
    return result


@dataclass
class BatchResult:
    ready: list = field(default_factory=list)
    review: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    previews: list = field(default_factory=list)
    cancelled: bool = False
    jobs: tuple = ()
    source_errors: tuple = ()


def render_theme(snapshot, options, sources):
    from ui.image_editor import numpy_from_qimage

    inputs = {item.layer_id: source for item, source in zip(options.bindings, sources)}

    def replace_layers(layers):
        result = []
        for layer in layers:
            if layer['id'] in inputs:
                layer = replacement(inputs[layer['id']], layer, fit=True)
            elif layer.get('children'):
                layer = dict(layer, children=replace_layers(layer['children']))
            result.append(layer)
        return result

    context = ImageRenderContext().load(dict(snapshot, layers=replace_layers(snapshot['layers'])))
    clock_ids = {layer['id'] for layer in image_slots(snapshot)
                 if layer['id'] not in inputs and _CLOCK_NAME.search(layer['name'])}
    if clock_ids:
        adapt_clock_contrast(context, clock_ids)
    return numpy_from_qimage(context._render_composite(True))


def adapt_clock_contrast(context, clock_ids):
    from ui.image_editor import numpy_from_qimage
    from ui.image_folders import walk_layers as walk_render_layers

    width, height = context.project.w, context.project.h
    for layer in walk_render_layers(context.project.layers):
        if layer.id not in clock_ids or not np.any(layer.pixels[:, :, 3] < 128):
            continue
        glyphs = numpy_from_qimage(context._render_layer_canvas(layer, width, height))[:, :, 3] > 128
        rows = np.flatnonzero(glyphs.any(axis=1))
        if not rows.size:
            continue
        # Clock overlays can include dock controls. Sample the upper clock face.
        top, bottom = rows[0], rows[-1] + 1
        end = top + max(1, round((bottom - top) * 0.45))
        glyphs = glyphs[top:end]
        layer.visible = False
        try:
            pixels = numpy_from_qimage(context._render_composite(True))[top:end][glyphs]
        finally:
            layer.visible = True
        alpha = pixels[:, 3] / 255
        luminance = (pixels[:, :3] @ [0.2126, 0.7152, 0.0722]) * alpha + 255 * (1 - alpha)
        ink = '#000000' if np.median(luminance) >= 150 else '#ffffff'
        layer.effects = dict(layer.effects, color_overlay=True, color_overlay_color=ink,
                             color_overlay_opacity=1.)


def cleanup_text(rgba, targets):
    boxes = recognize_targets(rgba, targets)
    if not boxes:
        return rgba, '未识别到目标文字'
    for _ in range(2):
        rgba, _ = fill_text_boxes(rgba, boxes)
        boxes = recognize_targets(rgba, targets)
        if not boxes:
            return rgba, ''
    return rgba, '仍识别到目标文字'


def write_new(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open('xb')
    try:
        with stream:
            stream.write(payload)
    except OSError:
        path.unlink(missing_ok=True)
        raise


def collect_jobs(options, source, errors):
    if options.mode == 'selected':
        return list(options.selected_jobs)
    rules = [source_patterns(item.rule) for item in options.bindings]
    if options.mode != 'grouped':
        columns = [find_images(item.folder.resolve(), rule) for item, rule in zip(options.bindings, rules)]
        if not all(columns):
            raise ValueError('有图层目录没有找到图片，请补充对应的文件夹或文件名。')
        if options.mode == 'ordered':
            if len({len(column) for column in columns}) != 1:
                raise ValueError('各图层的文件夹张数不同，无法按顺序配对。')
            return [(row[0].stem, row) for row in zip(*columns)]
        indexed = [index_images(column, item.key_pattern) for column, item in zip(columns, options.bindings)]
        if not all(indexed):
            raise ValueError('没有找到符合命名规则的素材编号。')
        keys = set(indexed[0])
        if any(set(index) != keys for index in indexed):
            raise ValueError('各文件夹的图片名称无法一一对应。请说明配对规则，或明确按文件名排序配对。')
        return [(indexed[0][key].stem, tuple(index[key] for index in indexed)) for key in sorted(keys)]

    if source is None or not source.is_dir():
        raise ValueError('请告诉我包含各主题子文件夹的素材总目录。')
    source = source.resolve()
    external = {}
    for item, rule in zip(options.bindings, rules):
        if item.folder:
            root = item.folder.resolve()
            external[item.layer_id] = (root, index_images(find_images(root, rule), item.key_pattern))
    jobs = []
    for group in sorted(source.iterdir(), key=lambda path: (path.name.casefold(), path.name)):
        if not group.is_dir():
            continue
        resolved = group.resolve()
        if not resolved.is_relative_to(source):
            errors.append(f'{group.name}：主题目录位于素材目录之外')
            continue
        matches = []
        for item, rule in zip(options.bindings, rules):
            if item.layer_id not in external:
                matches.append(find_images(resolved, rule))
                continue
            root, indexed = external[item.layer_id]
            candidate = (root / group.name).resolve()
            nested = find_images(candidate, rule) if candidate.is_relative_to(root) and candidate.is_dir() else []
            match = indexed.get(group.name.casefold())
            matches.append(nested or ([match] if match else []))
        if not all(matches):
            errors.append(f'{group.name}：缺少符合取图规则的图片')
        else:
            jobs.append((group.name, tuple(items[0] for items in matches)))
    return jobs


def run_template_batch(snapshot, options, source, destination, *, preview=False,
                       cancelled=lambda: False, progress=lambda *_: None,
                       planned_jobs=None, source_errors=()):
    options.validate(snapshot)
    roots = [source, *(item.folder for item in options.bindings), *options.input_roots]
    if not preview and any(root and destination.resolve().is_relative_to(root.resolve()) for root in roots):
        raise ValueError('导出目录应位于素材目录之外。')
    result = BatchResult()
    if planned_jobs is None:
        jobs = apply_pairing(collect_jobs(options, source, result.errors), options)
    else:
        jobs = planned_jobs
        result.errors.extend(source_errors)
    if not jobs:
        raise ValueError('没有可用主题，请检查目录和取图规则。')
    result.jobs, result.source_errors = tuple(jobs), tuple(result.errors)
    prefix = filename_prefix(options.name)
    if preview:
        jobs = jobs[:2]
    else:
        for index in range(options.first_index, options.first_index + len(jobs)):
            name = f'{prefix}{index}.jpg'
            if any((folder / name).exists() for folder in (destination, destination / '待复查')):
                raise ValueError(f'已存在 {name}，请更换输出目录、前缀或起始序号。')
    for position, (theme, paths) in enumerate(jobs, 1):
        if cancelled():
            result.cancelled = True
            break
        progress(position, len(jobs), theme)
        try:
            rgba = render_theme(snapshot, options, paths)
            reason = ''
            if options.targets:
                rgba, reason = cleanup_text(rgba, options.targets)
            if cancelled():
                result.cancelled = True
                break
            payload = export_jpeg(rgba, options.max_kb * 1000)
            if preview:
                result.previews.append((theme, QImage.fromData(payload), len(payload), reason))
                continue
            folder = destination / '待复查' if reason else destination
            target = folder / f'{prefix}{options.first_index + position - 1}.jpg'
            write_new(target, payload)
            (result.review if reason else result.ready).append(str(target))
        except (ValueError, OSError, cv2.error, subprocess.TimeoutExpired) as error:
            result.errors.append(f'{theme}：{error}')
    return result


def apply_pairing(jobs, options):
    if options.pairing == 'same':
        return jobs
    if len(jobs) < 2:
        raise ValueError('跨主题拼图至少需要两个素材齐全的主题。')
    if options.pairing == 'next':
        donors = jobs[1:] + jobs[:1]
    else:
        donors = jobs.copy()
        rng = random.Random(options.seed)
        # Sattolo shuffle gives every theme a different donor with no repeats.
        for index in range(len(donors) - 1, 0, -1):
            other = rng.randrange(index)
            donors[index], donors[other] = donors[other], donors[index]
    cross = set(options.cross_layers)
    return [(theme, tuple(donor[index] if binding.layer_id in cross else paths[index]
                         for index, binding in enumerate(options.bindings)))
            for (theme, paths), (_, donor) in zip(jobs, donors)]
