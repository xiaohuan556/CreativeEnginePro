"""Compositor package reader and safe, atomic manifest writer.

The native manifest remains readable by Compositor. A separate sidecar retains
workbench-only data, and is trusted only while all exported assets are unchanged.
"""
import hashlib
import json
import uuid
from pathlib import Path
import numpy as np
from core.image_import import ImportedAsset, check_size

BLENDS = {'Normal':'normal', 'Multiply':'multiply', 'Screen':'screen', 'Overlay':'overlay',
          'Darken':'darken', 'Lighten':'lighten', 'Color Burn':'color_burn', 'Color Dodge':'color_dodge',
          'Linear Burn':'linear_burn', 'Linear Dodge (Add)':'linear_dodge', 'Soft Light':'soft_light',
          'Hard Light':'hard_light', 'Vivid Light':'vivid_light', 'Linear Light':'linear_light',
          'Difference':'difference', 'Exclusion':'exclusion', 'Hue':'hue', 'Saturation':'saturation',
          'Color':'color', 'Luminosity':'luminosity', 'Pass Through':'pass_through'}


def package_digest(folder):
    folder = Path(folder)
    digest = hashlib.sha256()
    for file in [folder / 'manifest.json'] + sorted((folder / 'images').glob('*')):
        if not file.is_file():
            continue
        digest.update(file.name.encode('utf-8'))
        with file.open('rb') as stream:
            for block in iter(lambda: stream.read(1024*1024), b''):
                digest.update(block)
    return digest.hexdigest()


def read_comp(folder):
    from PIL import Image
    from PyQt6.QtGui import QImage, QPainter, QTransform
    from PyQt6.QtCore import Qt
    folder = Path(folder)
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format') != 'com.compositor.project':
        raise ValueError('不是有效的 Compositor 工程')
    if manifest.get('version') not in set(range(1,21)) | {27}:
        raise ValueError('该 Compositor 工程版本尚未适配')
    width, height = int(manifest['width']), int(manifest['height'])
    check_size(width, height)
    records = manifest.get('layers', [])
    descriptors, parents, notes = {}, {}, []
    def asset_path(name):
        result = (folder / 'images' / name).resolve()
        if not result.is_relative_to((folder / 'images').resolve()) or Path(name).name != name:
            raise ValueError('工程资源路径不合法')
        return result
    def pixels(name):
        with Image.open(asset_path(name)) as image:
            check_size(*image.size)
            return np.array(image.convert('RGBA'), dtype=np.uint8)
    for record in records:
        identity = str(uuid.UUID(record['id'])).upper()
        if identity in descriptors:
            raise ValueError('工程包含重复图层 ID')
        transform = record.get('transform', {})
        size = transform.get('size', [width, height])
        origin = transform.get('origin', [0, 0])
        descriptor = dict(name=record.get('name', '图层'), visible=record.get('isVisible', True),
                          opacity=float(record.get('opacity', 1)), blend=BLENDS.get(record.get('blendMode', 'Normal'), 'normal'))
        if record.get('isGroup'):
            descriptor.update(kind='group', children=[])
        elif record.get('adjustment'):
            adjustment = record['adjustment']
            kind = adjustment['kind']
            mapping = {'Curves':'curves', 'Levels':'levels', 'Hue/Saturation':'hsl', 'Exposure':'exposure',
                       'Invert':'invert', 'Black & White':'black_white', 'Color Balance':'color_balance'}
            if kind not in mapping:
                raise ValueError(f'该 .comp 使用尚未映射的调整类型：{kind}；工程未被修改')
            spec = {'type':mapping[kind]}
            if kind == 'Curves':
                spec['channels'] = {key:[[p['x'], p['y']] for p in points] for key,points in
                                    zip(('rgb','red','green','blue'), adjustment['curves']['channels'])}
            elif kind == 'Levels':
                values = adjustment['levels']['ranges'][0]
                spec.update(black=values['black'], gamma=values['gamma']*100, white=values['white'])
            elif kind == 'Hue/Saturation':
                spec.update({key:adjustment.get(key, 0) for key in ('hue','saturation','lightness')})
            elif kind == 'Exposure':
                spec['exposure'] = adjustment.get('exposureSettings', {}).get('exposure', 0)*100
            descriptor.update(kind='adjust', adjust=spec)
        else:
            data = pixels(record['imageFile']) if record.get('imageFile') else np.zeros((height, width, 4), np.uint8)
            if transform.get('flipX'):
                data = data[:, ::-1].copy()
            if transform.get('flipY'):
                data = data[::-1].copy()
            sx, sy = float(size[0])/data.shape[1], float(size[1])/data.shape[0]
            if abs(sx-sy) > .0001:
                import cv2
                check_size(max(1, round(size[0])), max(1, round(size[1])))
                data = cv2.resize(data, (max(1, round(size[0])), max(1, round(size[1]))))
                sx = 1
            descriptor.update(pixels=data, x=origin[0]+size[0]/2, y=origin[1]+size[1]/2,
                              scale=sx, rotation=transform.get('rotation', 0))
        if record.get('maskFile') and record.get('maskEnabled', True):
            data = pixels(record['maskFile'])[:, :, 0]
            mask_transform = record.get('maskPlacement') or transform
            mask_size = mask_transform.get('size', size)
            mask_origin = mask_transform.get('origin', origin)
            matrix = QTransform()
            matrix.translate(mask_origin[0]+mask_size[0]/2, mask_origin[1]+mask_size[1]/2)
            matrix.rotate(mask_transform.get('rotation', 0))
            matrix.scale(mask_size[0]/data.shape[1], mask_size[1]/data.shape[0])
            matrix.translate(-data.shape[1]/2, -data.shape[0]/2)
            from ui.image_editor import qimage_from_numpy, numpy_from_qimage
            rgba = np.dstack((data, data, data, np.full_like(data, 255)))
            image = QImage(width, height, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.black)
            painter = QPainter(image)
            painter.setTransform(matrix)
            painter.drawImage(0, 0, qimage_from_numpy(rgba))
            painter.end()
            descriptor['mask'] = numpy_from_qimage(image)[:, :, 0].copy()
        descriptors[identity] = descriptor
        parents[identity] = str(uuid.UUID(record['parentID'])).upper() if record.get('parentID') else None
    roots = []
    for identity, descriptor in descriptors.items():
        parent = parents[identity]
        seen = {identity}
        ancestor = parent
        while ancestor:
            if ancestor in seen or ancestor not in descriptors:
                raise ValueError('工程文件夹结构无效')
            seen.add(ancestor)
            ancestor = parents[ancestor]
        if parent:
            if descriptors[parent].get('kind') != 'group':
                raise ValueError('图层的父节点不是文件夹')
            descriptors[parent]['children'].append(descriptor)
        else:
            roots.append(descriptor)
    return ImportedAsset(width, height, roots, notes)


def export_comp(editor, folder):
    from PIL import Image
    from ui.image_editor import numpy_from_qimage
    from ui.image_folders import walk_layers
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    images = folder / 'images'
    images.mkdir(exist_ok=True)
    width, height = editor._ctx_size()
    roots = editor._root_layers()
    records, notes = [], []
    blend_names = {value:key for key,value in BLENDS.items()}
    def add_image(image, name, opacity=1., blend='normal', parent=None, group=False):
        identity = str(uuid.uuid4()).upper()
        record = dict(id=identity, name=name, isVisible=True, isGroup=group, opacity=opacity,
                      blendMode=blend_names.get(blend, 'Normal'),
                      transform=dict(origin=[0,0], size=[width,height], rotation=0, flipX=False, flipY=False, sampling='High quality'))
        if parent:
            record['parentID'] = parent
        if image is not None:
            filename = identity + '.png'
            Image.fromarray(numpy_from_qimage(image)).save(images / filename)
            record['imageFile'] = filename
        records.append(record)
        return identity
    if editor.project.artboards or any(layer.kind == 'adjust' for layer in walk_layers(roots)):
        image = editor._render_composite(True)
        width, height = image.width(), image.height()
        add_image(image, '兼容合成预览')
        notes.append('此工程含画板或调整层，Compositor 兼容视图使用合成图；工作台完整可编辑结构另存于同目录。')
    else:
        if not editor.project.transparent:
            from PyQt6.QtGui import QImage
            background = QImage(width,height,QImage.Format.Format_ARGB32)
            background.fill(editor.project.bg_color)
            add_image(background,'画布背景')
        def emit(layers, parent=None):
            for layer in layers:
                if layer.kind == 'group':
                    identity = add_image(None, layer.name, layer.opacity, layer.blend, parent, True)
                    records[-1]['isVisible'] = layer.visible
                    if layer.mask is not None:
                        Image.fromarray(layer.mask).save(images / (identity+'.mask.png'))
                        records[-1].update(maskFile=identity+'.mask.png', maskEnabled=True)
                    emit(layer.children, identity)
                else:
                    image = editor._render_layer_canvas(layer, width, height)
                    add_image(image, layer.name, 1., layer.blend, parent)
                    records[-1]['isVisible'] = layer.visible
        emit(roots)
        notes.append('图层外观与位置导出为 Compositor 像素图层；工作台源像素、文字和变换保留在同目录的编辑数据中。')
    manifest = dict(format='com.compositor.project', version=20, colorSpace='sRGB', resolution=72,
                    documentID=str(uuid.uuid4()).upper(), width=width, height=height,
                    activeLayerID=records[-1]['id'] if records else None, layers=records)
    temporary = folder / ('.manifest.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    temporary.replace(folder / 'manifest.json')
    sidecar = dict(signature=package_digest(folder), snapshot=editor.pack_project(editor._snapshot()))
    temporary = folder / ('.creativeengine.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(sidecar, ensure_ascii=False), encoding='utf-8')
    temporary.replace(folder / 'creativeengine.json')
    return notes
