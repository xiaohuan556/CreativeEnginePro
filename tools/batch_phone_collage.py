"""Batch export CreativeEnginePro image templates using two replacement photos."""
import argparse
import json
import os
from itertools import combinations
import random
from pathlib import Path
import shutil
import subprocess

import cv2
import numpy as np
from PIL import Image
from PyQt6.QtWidgets import QApplication

from core.image_batch_processing import (export_jpeg, lockscreen_clock, recognize_words,
                                         remove_wallpics, replacement)
from ui.image_editor import ImageEditorWidget, numpy_from_qimage


SLOTS = ('图片1', '图片2')
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}


def read_template(path, *, lockscreen=False):
    with path.open(encoding='utf-8-sig') as stream:
        template = ImageEditorWidget.unpack_project(json.load(stream))
    if (template['w'], template['h']) != (1080, 1080) or template.get('artboards'):
        raise ValueError('模板需要是 1080×1080 的普通画布。')
    slots = []
    if lockscreen:
        for label in ('锁屏壁纸', '详情摘要图', 'IMG_46AE31A46A62-1'):
            matches = [i for i, layer in enumerate(template['layers'])
                       if layer['kind'] == 'image' and layer['visible'] and label in layer['name']]
            if len(matches) != 1:
                raise ValueError(f'模板需要一个可见的“{label}”图片图层。')
            slots.append(matches[0])
        return template, slots
    for name, prefix in zip(SLOTS, ('001-', '004-')):
        matches = [i for i, layer in enumerate(template['layers']) if layer['name'] == name]
        if not matches:
            matches = [i for i, layer in enumerate(template['layers']) if layer['name'].startswith(prefix)]
        if len(matches) != 1:
            raise ValueError(f'请在模板顶层放置一个名为“{name}”或以“{prefix}”开头的图片图层。')
        layer = template['layers'][matches[0]]
        if layer['kind'] != 'image' or layer.get('frames'):
            raise ValueError(f'“{layer["name"]}”需要是普通静态图片图层。')
        slots.append(matches[0])
    if slots[0] == slots[1]:
        raise ValueError('模板需要两个独立的图片图层。')
    return template, slots


def find_lockscreen_pairs(input_dir, theme_name):
    for theme in sorted(input_dir.iterdir(), key=lambda path: path.name.casefold()):
        if not theme.is_dir() or (theme_name and theme.name.casefold() != theme_name.casefold()):
            continue
        wallpapers = sorted(path for path in (theme / 'wallpapers').glob('*')
                            if path.is_file() and path.suffix.lower() in EXTENSIONS)
        previews = sorted(path for path in (theme / 'previews').glob('*')
                          if path.is_file() and path.suffix.lower() in EXTENSIONS)
        lock = next((path for path in wallpapers if '锁屏壁纸' in path.stem), None)
        lock = lock or next((path for path in wallpapers if '动态锁屏素材' in path.stem), None)
        if lock is None or not previews:
            print(f'{theme.name}：缺少锁屏图片或预览图片，已跳过。', flush=True)
            continue
        yield theme.name, lock, previews[0]


class WallpicsSample:
    """Match the reference word shape at its native screenshot scale."""
    def __init__(self, path):
        with Image.open(path) as image:
            gray = np.asarray(image.convert('L'))
        self.pattern = self.highpass(gray)
        self.mask = cv2.dilate((gray > 140).astype(np.uint8), np.ones((5, 5), np.uint8))
        if not self.mask.any():
            raise ValueError('文字样本需要包含深色背景上的白色 Wallpics。')

    @staticmethod
    def highpass(gray):
        gray = gray.astype(np.float32)
        return gray - cv2.GaussianBlur(gray, (0, 0), 2)

    def boxes(self, rgba):
        gray = cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_RGB2GRAY)
        score = cv2.matchTemplate(self.highpass(gray), self.pattern,
                                  cv2.TM_CCORR_NORMED, mask=self.mask)
        score = np.nan_to_num(np.abs(score), nan=0, posinf=0, neginf=0)
        height, width = self.pattern.shape
        while True:
            _, peak, _, (x, y) = cv2.minMaxLoc(score)
            if peak < .60:
                break
            yield x, y, width, height
            score[max(0, y - height // 2):y + height // 2 + 1,
                  max(0, x - width // 2):x + width // 2 + 1] = 0


def render_sample_boxes(sample, layers):
    from PyQt6.QtCore import QRectF

    for layer in layers:
        transform = layer.canvas_transform()
        for x, y, w, h in sample.boxes(layer.pixels):
            rect = transform.mapRect(QRectF(x, y, w, h))
            yield 'Wallpics', rect.x() * 2, rect.y() * 2, rect.width() * 2, rect.height() * 2


def find_preview_pairs(input_dir, theme_name=None):
    themes = sorted(
        (path for path in input_dir.iterdir() if path.is_dir()),
        key=lambda path: (path.name.casefold(), path.name),
    )
    for theme in themes:
        if theme_name is not None and theme.name.casefold() != theme_name.casefold():
            continue
        previews = theme / 'previews'
        if not previews.is_dir():
            print(f'{theme.name}：没有 previews 文件夹，已跳过。')
            continue
        paths = sorted(
            (path for path in previews.iterdir() if path.is_file() and path.suffix.lower() in EXTENSIONS),
            key=lambda path: (path.name.casefold(), path.name),
        )
        if len(paths) < 4:
            print(f'{theme.name}：至少需要 4 张预览图，实际找到 {len(paths)} 张，已跳过。')
            continue
        yield theme.name, paths[0], paths[3]


def find_random_pairs(input_dir, count, seed):
    first_previews = []
    for folder in sorted(input_dir.iterdir(), key=lambda path: (path.name.casefold(), path.name)):
        previews = folder / 'previews'
        if not previews.is_dir():
            continue
        images = sorted(
            (path for path in previews.iterdir() if path.is_file() and path.suffix.lower() in EXTENSIONS),
            key=lambda path: (path.name.casefold(), path.name),
        )
        if images:
            first_previews.append((folder.name, images[0]))
    possible = list(combinations(first_previews, 2))
    if count > len(possible):
        raise ValueError(f'最多可以组成 {len(possible)} 对不同主题。')
    rng = random.Random(seed)
    for first, second in rng.sample(possible, count):
        if rng.getrandbits(1):
            first, second = second, first
        yield f'{first[0]} + {second[0]}', first[1], second[1]


def run_batch(template_path, input_dir, output_dir, executable=None, *, theme_name=None,
              jpg_prefix=None, sample=None, random_count=0, seed=0, start_index=1,
              lockscreen=False):
    template, slots = read_template(template_path, lockscreen=lockscreen)
    if not input_dir.is_dir() or output_dir.resolve() == input_dir.resolve():
        raise ValueError('请指定有效的主题总目录，以及另一个输出文件夹。')
    if lockscreen:
        pairs = list(find_lockscreen_pairs(input_dir, theme_name))
    elif random_count:
        pairs = list(find_random_pairs(input_dir, random_count, seed))
    else:
        pairs = list(find_preview_pairs(input_dir, theme_name))
    if not pairs:
        raise ValueError('没有找到可用的主题图片。')
    editor = ImageEditorWidget()
    editor._recovery_timer.stop()
    background = template['bg']
    from PyQt6.QtGui import QColor

    failures = 0
    try:
        for index, (theme, first, second) in enumerate(pairs, start_index):
            try:
                filename = f'{jpg_prefix}{index}.jpg' if jpg_prefix else f'{theme}.png'
                if lockscreen and any((folder / filename).exists()
                                      for folder in (output_dir, output_dir / '待复查')):
                    print(f'{theme}：已有输出，已跳过。', flush=True)
                    continue
                layers = template['layers'].copy()
                for side, (slot_index, path) in enumerate(zip(slots, (first, second))):
                    panel = None
                    if lockscreen:
                        width = template['w'] / 2
                        panel = ((side + 0.5) * width, width, template['h'])
                    layers[slot_index] = replacement(path, layers[slot_index], panel=panel)
                clock_note = ''
                if lockscreen:
                    clock_index = slots[2]
                    layers[clock_index], color = lockscreen_clock(layers[slots[0]], layers[clock_index])
                    clock_note = f'，{color}时钟'
                stack = [editor._deser_layer(layer) for layer in layers]
                canvas = editor.render_stack(
                    stack, 1080, 1080, None if template['transparent'] else QColor(*background))
                sample_words = tuple(render_sample_boxes(sample, [stack[i] for i in slots[:2]])) if sample else ()
                rgba, hits = remove_wallpics(numpy_from_qimage(canvas), executable, sample_words=sample_words)
                remaining = [word for word, *_ in recognize_words(rgba, executable)
                             if word.strip().casefold() == 'wallpics'] if hits and (jpg_prefix or lockscreen) else []
                if remaining:
                    rgba, extra_hits = remove_wallpics(rgba, executable)
                    hits += extra_hits
                    remaining = [word for word, *_ in recognize_words(rgba, executable)
                                 if word.strip().casefold() == 'wallpics']
                destination = output_dir if hits and not remaining else output_dir / '待复查'
                destination.mkdir(parents=True, exist_ok=True)
                target = destination / filename
                if jpg_prefix:
                    payload = export_jpeg(rgba)
                    with target.open('xb') as stream:
                        stream.write(payload)
                else:
                    with target.open('xb') as stream:
                        Image.fromarray(rgba).save(stream, format='PNG')
                print(f'{theme}：{first.name} + {second.name} → {target}（识别 {hits} 处{clock_note}）', flush=True)
            except (ValueError, OSError, cv2.error, subprocess.TimeoutExpired) as exc:
                failures += 1
                print(f'{theme} 处理失败（{first.name} + {second.name}）：{exc}')
    finally:
        editor.close()
    return int(failures > 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('template', type=Path, help='图片工作台保存的 JSON 模板')
    parser.add_argument('input_dir', type=Path, help='包含各主题子目录的总目录')
    parser.add_argument('output_dir', type=Path, help='独立的输出文件夹')
    parser.add_argument('--random-count', type=int, default=0, help='随机配对不同主题的第一张预览图')
    parser.add_argument('--seed', type=int, default=0, help='随机配对种子')
    parser.add_argument('--start-index', type=int, default=1, help='JPG 起始序号')
    parser.add_argument('--wallpics-sample', type=Path, help='同尺寸截图里裁出的白色 Wallpics 字样')
    parser.add_argument('--jpg-prefix', help='输出限 500 KB 的 JPG，并用此前缀加序号命名')
    parser.add_argument('--theme', help='仅处理这个主题，用于试跑')
    parser.add_argument('--lockscreen', action='store_true', help='合成锁屏壁纸、首张预览及自适应黑白时钟')
    parser.add_argument('--tesseract', help='可选的 Tesseract 程序路径，默认使用 Windows OCR')
    args = parser.parse_args()
    if args.lockscreen and args.random_count:
        parser.error('锁屏合成按同一主题配对。')
    if args.start_index < 1 or args.random_count < 0 or (args.random_count and (args.theme or not args.jpg_prefix)):
        parser.error('随机配对需要 JPG 前缀和正数起始序号，并且不能指定单个主题。')
    if args.jpg_prefix and any(char in args.jpg_prefix for char in '/\\\\:'):
        parser.error('JPG 前缀只能是文件名的一部分。')
    executable = shutil.which(args.tesseract) if args.tesseract else None
    if args.tesseract and executable is None:
        parser.error('找不到指定的 Tesseract 程序。')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    app = QApplication([])
    try:
        return run_batch(args.template, args.input_dir, args.output_dir, executable, theme_name=args.theme,
                         jpg_prefix=args.jpg_prefix,
                         sample=WallpicsSample(args.wallpics_sample) if args.wallpics_sample else None,
                         random_count=args.random_count, seed=args.seed, start_index=args.start_index,
                         lockscreen=args.lockscreen)
    except (ValueError, OSError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())

