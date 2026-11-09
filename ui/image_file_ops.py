"""File import and frame editing integrated with the workbench model."""
import base64
from pathlib import Path
import numpy as np
from PyQt6.QtCore import Qt, QTimer, QFileSystemWatcher
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QLabel, QPushButton, QComboBox,
                            QSpinBox, QFileDialog, QMessageBox, QDialog, QFormLayout,
                            QDoubleSpinBox, QCheckBox, QDialogButtonBox)
from core.image_import import read_asset, ASSET_FILTER, RAW_SUFFIXES
from core.project_files import PIC_FILTER, PIC_SUFFIX, project_path
from ui.project_dialogs import choose_project_save_path


class ImageFileOperations:
    def export_pdf(self,path=None):
        if not path:
            path,_ = QFileDialog.getSaveFileName(self,'导出 PDF','','PDF (*.pdf)')
        if not path:
            return
        from PyQt6.QtGui import QPdfWriter,QPainter,QPageSize,QPageLayout
        from PyQt6.QtCore import QSizeF,QMarginsF
        image = self._render_artboard(self.active_artboard) if self.active_artboard else self._render_composite(True)
        writer = QPdfWriter(str(path))
        writer.setResolution(300)
        writer.setPageSize(QPageSize(QSizeF(image.width()*25.4/300,image.height()*25.4/300),QPageSize.Unit.Millimeter))
        writer.setPageMargins(QMarginsF(0,0,0,0),QPageLayout.Unit.Millimeter)
        painter = QPainter(writer)
        painter.drawImage(writer.pageLayout().paintRectPixels(300),image)
        painter.end()
        self.status_tool.setText('PDF 已导出（300 DPI）')

    def channel_pack_pixels(self,channels):
        from ui.image_editor import numpy_from_qimage
        width,height = self._ctx_size()
        out = np.zeros((height,width,4),np.uint8)
        for index,source in enumerate(channels):
            if isinstance(source,int):
                out[:, :, index] = source
            else:
                image = numpy_from_qimage(self._render_layer_canvas(source))
                gray = image[:, :, :3].astype(np.float32) @ [.2126,.7152,.0722]
                out[:, :, index] = np.rint(gray*image[:, :, 3]/255).astype(np.uint8)
        return out

    def export_channel_pack(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('通道打包 PNG')
        form = QFormLayout(dialog)
        form.addRow(QLabel('将各图层的灰度值写入 R、G、B、A 通道。'))
        choices = []
        available = [layer for layer in self._all_layers() if layer.kind!='adjust']
        for index,name in enumerate(('R 红通道','G 绿通道','B 蓝通道','A 透明通道')):
            combo = QComboBox()
            combo.addItem('全黑 / 0',0)
            combo.addItem('全白 / 255',255)
            for layer in available:
                combo.addItem(layer.name,layer)
            combo.setCurrentIndex(index+2 if index<len(available) and index<3 else 1 if index==3 else 0)
            choices.append(combo)
            form.addRow(name,combo)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec()!=QDialog.DialogCode.Accepted:
            return
        path,_ = QFileDialog.getSaveFileName(self,'导出通道打包图','','PNG (*.png)')
        if path:
            from PIL import Image
            from ui.duo_delivery import offer_duo_export
            Image.fromarray(self.channel_pack_pixels([combo.currentData() for combo in choices])).save(path)
            offer_duo_export(self, [path], "图片工作台", "导出完成", f"通道图已保存到：{path}")

    def split_channels(self):
        from ui.image_editor import ImageLayer,numpy_from_qimage
        if self.active is None or self.active.kind=='adjust':
            return
        source = self.active
        pixels = numpy_from_qimage(self._render_layer_canvas(source))
        self._push_undo('拆分 RGBA 通道')
        parent = self._parent_list_of(source)
        index = parent.index(source)+1
        created = []
        for channel,name in enumerate('RGBA'):
            gray = pixels[:, :, channel]
            rgba = np.dstack((gray,gray,gray,np.full_like(gray,255)))
            layer = ImageLayer(source.name+' · '+name,rgba,rgba.shape[1],rgba.shape[0])
            layer.x,layer.y = layer.w/2,layer.h/2
            parent.insert(index,layer)
            index += 1
            created.append(layer)
        self.active,self.selected = created[-1],created
        self._refresh_layers()
        self._redraw()

    def save_project(self, path=None):
        if self._text_editing:
            self._end_text_edit(save=True)
        if not path:
            suggested = project_path(self._source_project_path, PIC_SUFFIX) if self._source_project_path else ''
            path = choose_project_save_path(self, '保存图片工程', suggested, PIC_FILTER, PIC_SUFFIX)
        if not path:
            return
        path = str(project_path(path, PIC_SUFFIX).resolve())
        # Settle the debounced edit before recording which revision was saved.
        self._commit_property_history()
        snapshot = self._snapshot()
        snapshot['format_version'] = 3
        if not hasattr(self, '_save_recovery_tokens'):
            self._save_recovery_tokens = {}
        self._save_recovery_tokens[path] = self._recovery_token()
        if not hasattr(self,'_save_jobs'):
            self._save_jobs, self._save_pending, self._save_revisions = {}, {}, {}
        if path in self._save_jobs:
            worker = self._save_jobs[path]
            if worker.queue_snapshot(snapshot):
                self._save_revisions[path] = self._history_idx
                self.status_tool.setText('最新修改已排队保存，可继续编辑')
                return worker
        return self._start_project_save(path,snapshot,self._history_idx)

    def _start_project_save(self,path,snapshot,revision):
        from core.image_project_io import ProjectSaveWorker
        worker = ProjectSaveWorker(path,snapshot)
        self._save_jobs[path] = worker
        self._save_revisions[path] = revision
        worker.saved.connect(self._project_saved)
        worker.failed.connect(self._project_save_failed)
        self.status_tool.setText('正在后台保存工程快照，可继续编辑')
        worker.start()
        return worker

    def _project_saved(self,path):
        from PyQt6 import sip
        if sip.isdeleted(self):
            return
        self._finish_project_save(path, self.sender())

    def _finish_project_save(self, path, worker, error=None):
        if self._save_jobs.get(path) is not worker:
            return
        self._save_jobs.pop(path,None)
        revision = self._save_revisions.pop(path,None)
        if error is not None:
            self._save_pending.pop(path,None)
            self._save_recovery_tokens.pop(path, None)
            self.status_tool.setText('保存失败：' + error)
            return
        if path in self._save_pending:
            snapshot,revision = self._save_pending.pop(path)
            self._start_project_save(path,snapshot,revision)
        else:
            token = self._save_recovery_tokens.pop(path, None)
            self.manual_save_completed(path, token)
            self.status_tool.setText('工程快照已保存' if token == self._recovery_token() else '快照已保存；随后产生的修改尚未保存')

    def _project_save_failed(self,path,message):
        self._finish_project_save(path, self.sender(), message)

    def detach_comp(self):
        if hasattr(self,'_comp_watcher'):
            paths = self._comp_watcher.files()+self._comp_watcher.directories()
            if paths:
                self._comp_watcher.removePaths(paths)
            self._comp_timer.stop()
        self._comp_path = None

    def add_image_from_path(self, path, *, from_drop=False):
        options = {}
        if Path(path).suffix.lower() in RAW_SUFFIXES:
            dialog = QDialog(self)
            dialog.setWindowTitle('RAW 显影')
            form = QFormLayout(dialog)
            exposure = QDoubleSpinBox()
            exposure.setRange(-5, 5)
            exposure.setSingleStep(.1)
            form.addRow('曝光 EV', exposure)
            white_balance = QCheckBox('使用拍摄时的相机白平衡')
            white_balance.setChecked(True)
            form.addRow(white_balance)
            form.addRow(QLabel('以 sRGB、8 位色深显影后导入，原始 RAW 文件保持不变。'))
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            form.addRow(buttons)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            options = dict(raw_exposure=exposure.value(), raw_camera_wb=white_balance.isChecked())
        try:
            asset = read_asset(path, **options)
            target = self
            if asset.artboards and (self.project.layers or self.project.artboards) and getattr(self, 'host', None):
                target = self.host.new_document(asset.width, asset.height, name=Path(path).stem)
            target.insert_imported_asset(asset, path, from_drop=from_drop)
            return True
        except Exception as exc:
            QMessageBox.warning(self, '导入失败', f'{Path(path).name}\n{exc}')

    def import_psd(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入 Photoshop 文档', '', 'Photoshop (*.psd *.psb)')
        if path:
            self.add_image_from_path(path)

    def insert_imported_asset(self, asset, path='', undo_snapshot=None, *, from_drop=False):
        from ui.image_editor import Artboard, ImageLayer
        from ui.image_folders import walk_layers
        if not asset.layers and not asset.artboards:
            raise ValueError('文件中没有可导入的图层')
        was_empty = not self._all_layers()
        match_canvas = (was_empty and not self.project.artboards
                        and (not from_drop or not getattr(self.project, '_has_dropped_image', False)))
        def build(descriptor):
            pixels = descriptor.get('pixels')
            height, width = pixels.shape[:2] if pixels is not None else (1, 1)
            layer = ImageLayer(descriptor['name'], pixels, width, height, descriptor.get('kind', 'image'))
            layer.x, layer.y = descriptor.get('x', asset.width/2), descriptor.get('y', asset.height/2)
            for key in ('opacity', 'visible', 'blend', 'mask', 'clip', 'frames', 'frame_durations', 'scale', 'rotation', 'adjust'):
                if key in descriptor:
                    setattr(layer, key, descriptor[key])
            if descriptor.get('svg_source'):
                layer.svg_source = base64.b64encode(descriptor['svg_source']).decode('ascii')
                layer.kind = 'svg'
            layer.children = [build(child) for child in descriptor.get('children', [])]
            if layer.kind == 'group':
                layer.collapsed = False
            return layer
        if asset.artboards:
            from PyQt6.QtGui import QColor
            boards = []
            for data in asset.artboards:
                board = Artboard(data['name'], data['x'], data['y'], data['w'], data['h'])
                board.bg_color = QColor(*data['bg'])
                board.transparent = data['transparent']
                board.layers = [build(layer) for layer in data['layers']]
                boards.append(board)
            self._push_undo('导入 PSD 画板', snap=undo_snapshot)
            if self.project.layers and not self.project.artboards:
                original = Artboard('原画布', 0, 0, self.project.w, self.project.h)
                original.layers, self.project.layers = self.project.layers, []
                original.transparent, original.bg_color = self.project.transparent, self.project.bg_color
                self.project.artboards.append(original)
            if self.project.artboards:
                offset = max(board.x + board.w for board in self.project.artboards) + 80
                for board in boards:
                    board.x += offset
            else:
                self.project.w, self.project.h = asset.width, asset.height
            self.project.artboards.extend(boards)
            self._invalidate_doc_bounds()
            self._set_active_artboard(boards[0])
            self._request_fit()
            self._last_import_notes = list(asset.notes)
            count = sum(layer.kind != 'group' for board in boards for layer in walk_layers(board.layers))
            self.status_tool.setText(f'已导入 {len(boards)} 个画板和 {count} 个图层')
            self.status_tool.setToolTip('\n'.join(asset.notes))
            return

        layers = [build(descriptor) for descriptor in asset.layers]
        self._push_undo('导入图像 / 文档', snap=undo_snapshot)
        if match_canvas:
            self._set_canvas_size(asset.width, asset.height)
        elif len(layers) == 1 and layers[0].kind in ('image', 'svg') and not layers[0].frames:
            layer = layers[0]
            width, height = self._ctx_size()
            layer.scale = min(width/layer.w, height/layer.h, 1.) * .9
            layer.x, layer.y = width/2, height/2
        self._ctx_layers().extend(layers)
        if from_drop:
            self.project._has_dropped_image = True
        self.set_active(layers[-1])
        self._refresh_layers()
        self._redraw()
        self.view.fit_view()
        self._last_import_notes = list(asset.notes)
        count = sum(layer.kind != 'group' for layer in walk_layers(layers))
        status = 'PSD 只有可读取的合成图，已导入为一层' if asset.merged_only else f'已导入 {count} 个图层'
        self.status_tool.setText(status + (' · 转换详情见“文件 → 导入报告”' if asset.notes else ''))
        self.status_tool.setToolTip('\n'.join(asset.notes))

    def show_import_report(self):
        notes = getattr(self, '_last_import_notes', [])
        QMessageBox.information(self, '导入报告', '\n'.join(notes) if notes else '最近一次导入没有需要说明的转换。')

    def open_comp(self, path=None):
        if not path:
            path = QFileDialog.getExistingDirectory(self, '选择 .comp 工程文件夹')
        if not path:
            return
        try:
            self._load_comp(path)
            self._watch_comp(path)
        except Exception as exc:
            QMessageBox.warning(self, 'Compositor 工程读取失败', str(exc))

    def _load_comp(self, path):
        import json
        from core.compositor_io import read_comp, package_digest
        folder = Path(path)
        snapshot = None
        sidecar = folder / 'creativeengine.json'
        if sidecar.exists():
            saved = json.loads(sidecar.read_text(encoding='utf-8'))
            if saved.get('signature') == package_digest(folder):
                snapshot = self.unpack_project(saved['snapshot'])
        if snapshot is None:
            asset = read_comp(folder)
            # Validate all records before replacing the live document.
            before = self._snapshot()
            self.project.layers = []
            self.project.artboards = []
            self.active = self.active_artboard = None
            self.selected = []
            try:
                self.insert_imported_asset(asset, folder, undo_snapshot=before)
            except Exception:
                self._restore(before)
                raise
        else:
            self._push_undo('打开 Compositor 工程')
            self._restore(snapshot)
        self._comp_digest = package_digest(folder)
        self._comp_history_index = self._history_idx
        self._comp_pending = False

    def export_comp(self, path=None):
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, '导出 Compositor 工程', '', 'Compositor (*.comp)')
        if not path:
            return
        from core.compositor_io import export_comp
        if not str(path).lower().endswith('.comp'):
            path = str(path) + '.comp'
        try:
            self._last_import_notes = export_comp(self, path)
            self.status_tool.setText('Compositor 工程已导出 · 兼容详情见导入报告')
        except Exception as exc:
            QMessageBox.warning(self, 'Compositor 导出失败', str(exc))

    def _watch_comp(self, path):
        self._comp_path = str(Path(path).resolve())
        if not hasattr(self, '_comp_watcher'):
            self._comp_watcher = QFileSystemWatcher(self)
            self._comp_timer = QTimer(self)
            self._comp_timer.setSingleShot(True)
            self._comp_timer.timeout.connect(self._comp_changed)
            self._comp_watcher.directoryChanged.connect(lambda _: self._comp_timer.start(400))
            self._comp_watcher.fileChanged.connect(lambda _: self._comp_timer.start(400))
        old = self._comp_watcher.files() + self._comp_watcher.directories()
        if old:
            self._comp_watcher.removePaths(old)
        folder = Path(path)
        self._comp_watcher.addPaths([str(folder), str(folder/'manifest.json'), str(folder/'images')]
                                   + [str(asset) for asset in (folder/'images').glob('*') if asset.is_file()])

    def _comp_changed(self):
        from core.compositor_io import package_digest
        from PyQt6.QtWidgets import QApplication
        if not getattr(self, '_comp_path', None):
            return
        if QApplication.activeModalWidget() is not None:
            self._comp_timer.start(1000)
            return
        try:
            digest = package_digest(self._comp_path)
            if digest == self._comp_digest:
                return
            if self._history_idx != self._comp_history_index or self._pending_property_history is not None:
                self._comp_pending = True
                self.status_tool.setText('外部工程已更新；本地修改已保留。文件 → 载入外部更新可重新加载。')
                return
            self._load_comp(self._comp_path)
            self._watch_comp(self._comp_path)
            self.status_tool.setText('已载入外部 Compositor 更新')
        except Exception as exc:
            self.status_tool.setText('外部更新尚未载入：' + str(exc))

    def reload_comp_from_disk(self):
        if getattr(self, '_comp_path', None):
            self.open_comp(self._comp_path)

    def _build_gif_controls(self):
        strip = QWidget()
        row = QHBoxLayout(strip)
        row.setContentsMargins(8, 4, 8, 4)
        row.addWidget(QLabel('动画帧'))
        self._gif_frame_combo = QComboBox()
        self._gif_frame_combo.currentIndexChanged.connect(self.select_gif_frame)
        row.addWidget(self._gif_frame_combo)
        self._gif_play = QPushButton('播放')
        self._gif_play.clicked.connect(self.toggle_gif_playback)
        row.addWidget(self._gif_play)
        row.addWidget(QLabel('时长'))
        self._gif_duration = QSpinBox()
        self._gif_duration.setRange(20, 60000)
        self._gif_duration.setSuffix(' ms')
        self._gif_duration.valueChanged.connect(self.set_gif_duration)
        row.addWidget(self._gif_duration)
        for text, callback in (('复制帧', self.duplicate_gif_frame), ('删除帧', self.delete_gif_frame),
                               ('导出 GIF…', self.export_gif)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            row.addWidget(button)
        row.addStretch()
        self._gif_timer = QTimer(self)
        self._gif_timer.setSingleShot(True)
        self._gif_timer.timeout.connect(self._gif_tick)
        self._gif_strip = strip
        strip.hide()
        return strip

    def _sync_gif_controls(self):
        if not hasattr(self, '_gif_strip'):
            return
        layer = self.active
        animated = bool(layer and layer.frames)
        self._gif_strip.setVisible(animated)
        if not animated:
            self._gif_timer.stop()
            self._gif_play.setText('播放')
            return
        self._gif_frame_combo.blockSignals(True)
        self._gif_frame_combo.clear()
        self._gif_frame_combo.addItems([f'{index+1} / {len(layer.frames)}' for index in range(len(layer.frames))])
        self._gif_frame_combo.setCurrentIndex(layer.frame_index)
        self._gif_frame_combo.blockSignals(False)
        self._gif_duration.blockSignals(True)
        self._gif_duration.setValue(layer.frame_durations[layer.frame_index])
        self._gif_duration.blockSignals(False)

    def select_gif_frame(self, index):
        layer = self.active
        if not layer or not layer.frames or not 0 <= index < len(layer.frames):
            return
        layer.frames[layer.frame_index] = layer.pixels
        layer.frame_index = index
        layer.pixels = layer.frames[index]
        self._sync_gif_controls()
        self._redraw()

    def toggle_gif_playback(self):
        if self._gif_timer.isActive():
            self._gif_timer.stop()
            self._gif_play.setText('播放')
        elif self.active and self.active.frames:
            self._gif_play.setText('暂停')
            self._gif_timer.start(self.active.frame_durations[self.active.frame_index])

    def _gif_tick(self):
        layer = self.active
        if layer and layer.frames:
            self.select_gif_frame((layer.frame_index+1) % len(layer.frames))
            self._gif_timer.start(layer.frame_durations[layer.frame_index])

    def set_gif_duration(self, value):
        if self.active and self.active.frames and not self.active.locked:
            self._queue_property_undo('修改动画帧时长')
            self.active.frame_durations[self.active.frame_index] = value

    def duplicate_gif_frame(self):
        layer = self.active
        if not layer or not layer.frames or layer.locked:
            return
        self._push_undo('复制动画帧')
        layer.frames.insert(layer.frame_index+1, layer.pixels.copy())
        layer.frame_durations.insert(layer.frame_index+1, layer.frame_durations[layer.frame_index])
        self.select_gif_frame(layer.frame_index+1)

    def delete_gif_frame(self):
        layer = self.active
        if not layer or len(layer.frames) < 2 or layer.locked:
            return
        self._push_undo('删除动画帧')
        index = layer.frame_index
        layer.frames.pop(index)
        layer.frame_durations.pop(index)
        layer.frame_index = min(index, len(layer.frames)-1)
        layer.pixels = layer.frames[layer.frame_index]
        self._sync_gif_controls()
        self._redraw()

    def export_gif(self, path=None):
        layer = self.active
        if layer is None or not layer.frames:
            return
        interactive = not path
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, '导出动画 GIF', '', 'GIF (*.gif)')
        if not path:
            return
        from PIL import Image
        from ui.image_editor import numpy_from_qimage
        current = layer.frame_index
        layer.frames[current] = layer.pixels
        frames = []
        try:
            for index in range(len(layer.frames)):
                layer.pixels = layer.frames[index]
                image = self._render_artboard(self.active_artboard) if self.active_artboard else self._render_composite(True)
                frames.append(Image.fromarray(numpy_from_qimage(image)))
            frames[0].save(str(path), save_all=True, append_images=frames[1:],
                           duration=layer.frame_durations, loop=0, disposal=2)
        finally:
            layer.pixels = layer.frames[current]
            layer.frame_index = current
        self.status_tool.setText('动画 GIF 已导出')
        if interactive:
            from ui.duo_delivery import offer_duo_export
            offer_duo_export(self, [path], "图片工作台", "导出完成", f"动画 GIF 已保存到：{path}")
