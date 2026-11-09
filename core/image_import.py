"""Decode workstation assets into editable layer descriptors."""
from pathlib import Path
from dataclasses import dataclass, field
import numpy as np

RAW_SUFFIXES = {'.dng', '.nef', '.nrw', '.cr2', '.cr3', '.arw', '.raf', '.orf', '.rw2', '.pef', '.raw'}
SUPPORTED_SUFFIXES = RAW_SUFFIXES | {'.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tif', '.tiff', '.gif', '.heic', '.heif', '.svg', '.svgz', '.pdf', '.psd', '.psb'}
ASSET_FILTER = '图像与文档 (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff *.gif *.heic *.heif *.svg *.svgz *.pdf *.psd *.psb *.dng *.nef *.nrw *.cr2 *.cr3 *.arw *.raf *.orf *.rw2 *.pef *.raw)'


@dataclass
class ImportedAsset:
    width: int
    height: int
    layers: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    merged_only: bool = False
    artboards: list = field(default_factory=list)


def check_size(width, height):
    if width < 1 or height < 1 or width > 30000 or height > 30000 or width * height > 100_000_000:
        raise ValueError('图像尺寸超出当前可处理范围')


def qimage_pixels(image):
    from PyQt6.QtGui import QImage
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    data = image.bits()
    data.setsize(image.sizeInBytes())
    return np.frombuffer(data, np.uint8).reshape(image.height(), image.bytesPerLine())[:, :image.width()*4].reshape(image.height(), image.width(), 4).copy()


def read_asset(path, pdf_dpi=300, raw_exposure=0., raw_camera_wb=True):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in RAW_SUFFIXES:
        import rawpy
        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(use_camera_wb=raw_camera_wb, no_auto_bright=True,
                                  bright=2. ** raw_exposure, output_bps=8)
        h, w = rgb.shape[:2]
        check_size(w, h)
        pixels = np.concatenate((rgb, np.full((h, w, 1), 255, np.uint8)), axis=2)
        return ImportedAsset(w, h, [dict(name=path.name, pixels=pixels)])
    if suffix in {'.svg', '.svgz'}:
        from PyQt6.QtSvg import QSvgRenderer
        from PyQt6.QtGui import QImage, QPainter
        from PyQt6.QtCore import Qt
        renderer = QSvgRenderer(str(path))
        if not renderer.isValid():
            raise ValueError('无法解析 SVG 图像')
        size = renderer.defaultSize()
        check_size(size.width(), size.height())
        image = QImage(size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        return ImportedAsset(size.width(), size.height(), [dict(name=path.name, pixels=qimage_pixels(image),
                              svg_source=path.read_bytes() if suffix == '.svg' else b'')], ['SVG 将保留源文件内容，并以像素预览导入'])
    if suffix == '.pdf':
        from PyQt6.QtPdf import QPdfDocument
        from PyQt6.QtCore import QSize
        document = QPdfDocument(None)
        error = document.load(str(path))
        if error.value != 0:
            raise ValueError('无法打开 PDF：' + str(error))
        layers = []
        for index in reversed(range(document.pageCount())):
            points = document.pagePointSize(index)
            w, h = round(points.width()*pdf_dpi/72), round(points.height()*pdf_dpi/72)
            check_size(w, h)
            image = document.render(index, QSize(w, h))
            if image.isNull():
                raise ValueError(f'无法渲染 PDF 第 {index+1} 页')
            layers.append(dict(name=f'{path.stem} · 第 {index+1} 页', pixels=qimage_pixels(image)))
        document.close()
        if not layers:
            raise ValueError('PDF 没有可读取的页面')
        return ImportedAsset(max(item['pixels'].shape[1] for item in layers), max(item['pixels'].shape[0] for item in layers), layers)
    if suffix in {'.psd', '.psb'}:
        return read_psd(path)
    if suffix in {'.heic', '.heif'}:
        from pillow_heif import register_heif_opener
        register_heif_opener(thumbnails=False)
    from PIL import Image, ImageOps, ImageSequence
    with Image.open(path) as source:
        check_size(*source.size)
        if getattr(source, 'is_animated', False):
            frames, durations = [], []
            for frame in ImageSequence.Iterator(source):
                frames.append(np.array(frame.convert('RGBA'), dtype=np.uint8))
                durations.append(max(20, int(frame.info.get('duration', 100))))
            return ImportedAsset(source.width, source.height, [dict(name=path.name, pixels=frames[0], frames=frames, frame_durations=durations)])
        pixels = np.array(ImageOps.exif_transpose(source).convert('RGBA'), dtype=np.uint8)
    return ImportedAsset(pixels.shape[1], pixels.shape[0], [dict(name=path.name, pixels=pixels)])


def read_psd(path):
    from psd_tools import PSDImage
    from psd_tools.constants import Tag
    from psd_tools.terminology import Key
    document = PSDImage.open(path)
    check_size(document.width, document.height)
    notes = []
    has_pixels = False

    def decode(node, origin=(0, 0), size=(document.width, document.height)):
        nonlocal has_pixels
        result = []
        for source in node:
            mode = getattr(source.blend_mode, 'name', 'NORMAL').lower()
            descriptor = dict(name=source.name, opacity=source.opacity/255,
                              visible=bool(source.visible), blend=mode, clip=bool(getattr(source, 'clipping', False)))
            if source.is_group():
                descriptor.update(kind='group', children=decode(source, origin, size))
            else:
                image = source.topil()
                if image is None:
                    image = source.composite()
                if image is None:
                    notes.append(f'{source.name}：无可导入像素，已跳过')
                    continue
                pixels = np.array(image.convert('RGBA'), dtype=np.uint8)
                has_pixels = True
                descriptor.update(pixels=pixels, x=source.left - origin[0] + pixels.shape[1]/2,
                                  y=source.top - origin[1] + pixels.shape[0]/2)
                if getattr(source, 'kind', '') in ('type', 'smartobject', 'shape', 'adjustment'):
                    notes.append(f'{source.name}：已保留外观，作为像素图层导入')
                mask = getattr(source, 'mask', None)
                if mask is not None and not getattr(mask, 'disabled', False):
                    image_mask = mask.topil()
                    if image_mask is not None:
                        width, height = size
                        canvas_mask = np.full((height, width), getattr(mask, 'background_color', 255), np.uint8)
                        values = np.array(image_mask.convert('L'), dtype=np.uint8)
                        left, top = int(mask.left) - origin[0], int(mask.top) - origin[1]
                        x0, y0 = max(0, left), max(0, top)
                        x1, y1 = min(width, left+values.shape[1]), min(height, top+values.shape[0])
                        if x1 > x0 and y1 > y0:
                            canvas_mask[y0:y1, x0:x1] = values[y0-top:y1-top, x0-left:x1-left]
                        descriptor['mask'] = canvas_mask
            result.append(descriptor)
        return result

    boards, root_layers = [], []
    for source in document:
        if source.kind != 'artboard':
            root_layers.append(source)
            continue
        left, top, right, bottom = source.bbox
        width, height = right - left, bottom - top
        check_size(width, height)
        data = next((source.tagged_blocks.get_data(tag)
                     for tag in (Tag.ARTBOARD_DATA3, Tag.ARTBOARD_DATA2, Tag.ARTBOARD_DATA1)
                     if tag in source.tagged_blocks), {})
        background_type = int(data.get(b'artboardBackgroundType', 1))
        rgb = (0, 0, 0) if background_type == 3 else (255, 255, 255)
        color = data.get(Key.Color)
        if background_type == 4 and color:
            rgb = tuple(max(0, min(255, round(float(color.get(key, 255)))))
                        for key in (Key.Red, Key.Green, Key.Blue))
        boards.append(dict(name=source.name, x=left, y=top, w=width, h=height, bg=rgb,
                           transparent=background_type not in (2, 3, 4),
                           layers=decode(source, (left, top), (width, height))))

    layers = decode(root_layers)
    if boards:
        left, top = min(board['x'] for board in boards), min(board['y'] for board in boards)
        for board in boards:
            board['x'] -= left
            board['y'] -= top
        width = max(board['x'] + board['w'] for board in boards)
        height = max(board['y'] + board['h'] for board in boards)
        if layers:
            boards.append(dict(name='画板外图层', x=width + 80, y=0,
                               w=document.width, h=document.height, bg=(255, 255, 255),
                               transparent=True, layers=layers))
            notes.append('PSD 中画板外的图层已单独放在一个画板中。')
            width += 80 + document.width
            height = max(height, document.height)
        check_size(width, height)
        return ImportedAsset(width, height, notes=notes, artboards=boards)
    if not has_pixels:
        # Flattened PSDs can carry a complete image with zero layer records.
        composite = document.composite()
        if composite is None:
            raise ValueError('PSD 中没有可读取的图层或合成图像')
        layers = [dict(name=Path(path).stem, pixels=np.array(composite.convert('RGBA'), dtype=np.uint8))]
        notes.append('PSD 没有可导入的独立图层，已将合成图像导入为一个像素图层。')
    return ImportedAsset(document.width, document.height, layers, notes, merged_only=not has_pixels)
