from dataclasses import replace
import os
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication

from ai.image_batch_agent import plan_batch_instruction, validate_batch_instruction
from core.image_batch_processing import match_text_boxes
from core.image_template_batch import BatchOptions, LayerSource, apply_pairing, collect_jobs, run_template_batch
from ui.image_editor import ImageEditorWidget, ImageLayer


def test_ocr_matches_complete_phrase_within_one_line():
    words = [('Free', 0, 0, 20, 10, 0), ('Download', 22, 0, 40, 10, 0),
             ('Free', 0, 20, 20, 10, 1), ('Shipping', 22, 20, 40, 10, 1),
             ('Free', 0, 40, 20, 10, 2), ('Download', 0, 60, 40, 10, 3)]
    assert match_text_boxes(words, ['free download']) == [('free download', 0, 0, 62, 10)]
    assert match_text_boxes([('免费', 0, 0, 20, 10, 0), ('下载', 22, 0, 20, 10, 0)],
                            ['免费下载'], joiner='') == [('免费 下载', 0, 0, 42, 10)]


def test_agent_uses_configured_chat_without_sending_local_paths(monkeypatch):
    def submit(provider, request):
        assert provider == 'desktop-chat'
        prompt = request.inputs['messages'][-1]['content']
        assert 'PATH_1' in prompt and 'PATH_2' in prompt
        assert 'G:' not in prompt and 'D:' not in prompt
        data = {'source': 'PATH_1', 'destination': 'PATH_2', 'targets': ['Wallpics'],
                'max_kb': 500, 'request_id': 7, 'delivery': True, 'first_index': 6}
        return SimpleNamespace(is_finished=True, is_success=True, result=SimpleNamespace(data=data))

    registry = SimpleNamespace(by_capability=lambda _: [SimpleNamespace(name='desktop-chat')])
    monkeypatch.setattr('ai.service.get_ai_manager', lambda: SimpleNamespace(registry=registry, submit=submit))
    plan = plan_batch_instruction(
        r'处理 G:\素材\hot，导出到 "D:\完成 批次"，去掉 Wallpics，500 KB，交付 REQ-7', lambda: False)
    assert plan == {'source': r'G:\素材\hot', 'destination': r'D:\完成 批次', 'targets': ['Wallpics'],
                    'max_kb': 500, 'request_id': 7, 'delivery': True}


def test_workbench_template_renders_preview_and_exports_distinct_results(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    editor = ImageEditorWidget()
    editor._recovery_timer.stop()
    try:
        editor.project.w, editor.project.h = 33, 25
        pixels = np.full((26, 11, 4), 255, np.uint8)
        left = ImageLayer('左', pixels, 11, 26)
        right = ImageLayer('右', pixels, 11, 26)
        badge = ImageLayer('第三张', pixels, 11, 26)
        left.x, left.y = 5.5, 13
        right.x, right.y = 16.5, 13
        badge.x, badge.y = 27.5, 13
        editor.project.layers = [left, right, badge]
        external = tmp_path / 'separate'
        external.mkdir()
        bindings = (LayerSource(left.id, 'wallpapers/*锁屏壁纸* | wallpapers/*动态锁屏素材*'),
                    LayerSource(right.id, 'previews/*'),
                    LayerSource(badge.id, '*.png', external, '{key}_角标'))
        options = BatchOptions(bindings, 'grouped',
                               ('Wallpics',), 'Demo_', 3, 10)
        editor._batch_template = {'layers': [left.id, right.id], 'max_kb': 10}
        snapshot = editor._snapshot()
        editor._rebuild_from_snap(editor.unpack_project(editor.pack_project(snapshot)))
        assert editor._batch_template == snapshot['batch_template']

        source = tmp_path / 'themes'
        destination = tmp_path / 'export'
        for name, wallpaper in [('B', '007-动态锁屏素材.jpg'), ('A', '004-锁屏壁纸.png')]:
            group = source / name
            (group / 'wallpapers').mkdir(parents=True)
            (group / 'previews').mkdir()
            Image.new('RGB', (20, 40), (0, 0, 0)).save(group / 'wallpapers' / wallpaper)
            Image.new('RGB', (40, 80), (240, 20, 20)).save(group / 'previews' / '001.png')
            Image.new('RGB', (40, 80), (0, 0, 0)).save(group / 'previews' / '002.png')
            Image.new('RGB', (20, 40), (0, 0, 220)).save(external / f'{name}_角标.png')
        preview = run_template_batch(snapshot, replace(options, targets=()), source, destination, preview=True)
        assert [name for name, *_ in preview.previews] == ['A', 'B']
        assert not destination.exists()
        _, image, _, _ = preview.previews[0]
        assert image.width() == 33 and image.height() == 25
        assert image.pixelColor(4, 12).red() < 30
        assert image.pixelColor(16, 12).red() > 200
        assert image.pixelColor(28, 12).blue() > 190

        reasons = iter(('', '未识别到目标文字'))
        monkeypatch.setattr('core.image_template_batch.cleanup_text', lambda rgba, _: (rgba, next(reasons)))
        Image.new('RGB', (40, 80), (0, 0, 0)).save(source / 'A' / 'previews' / '000.png')
        result = run_template_batch(snapshot, options, source, destination, planned_jobs=preview.jobs)
        assert result.ready == [str(destination / 'Demo_3.jpg')]
        assert result.review == [str(destination / '待复查' / 'Demo_4.jpg')]
        assert all(path.stat().st_size <= 10_000 for path in destination.rglob('*.jpg'))
        with Image.open(destination / 'Demo_3.jpg') as rendered:
            assert rendered.getpixel((16, 12))[0] > 200
        with pytest.raises(ValueError, match='已存在'):
            run_template_batch(snapshot, options, source, destination)
    finally:
        editor.close()


def test_separate_folders_match_by_common_identifier(tmp_path):
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    (first / 'A_封面.png').touch()
    (second / '画面_A.jpg').touch()
    bindings = (LayerSource(1, '*', first, '{key}_封面'), LayerSource(2, '*', second, '画面_{key}'))
    options = BatchOptions(bindings, 'matched', (), 'Demo_', 1, 500)
    jobs = collect_jobs(options, None, [])
    assert jobs == [('A_封面', (first / 'A_封面.png', second / '画面_A.jpg'))]
    (second / '画面_B.jpg').touch()
    with pytest.raises(ValueError, match='无法一一对应'):
        collect_jobs(options, None, [])


def test_agent_can_bind_mixed_folders_and_leaves_decoration_unbound(tmp_path):
    themes, external = tmp_path / 'themes', tmp_path / 'external'
    themes.mkdir()
    external.mkdir()
    paths = {'PATH_1': str(themes), 'PATH_2': str(external)}
    layers = [{'id': 1, 'name': '封面'}, {'id': 2, 'name': '截图'}, {'id': 3, 'name': '装饰'}]
    plan = validate_batch_instruction({
        'source': 'PATH_1', 'mode': 'grouped', 'bindings': [
            {'layer_id': 1, 'rule': 'wallpapers/*'},
            {'layer_id': 2, 'rule': '*.png', 'folder': 'PATH_2', 'key_pattern': '{key}_截图'},
        ],
    }, '按主题处理，截图从另外一个目录取。', paths, layers)
    assert plan['bindings'] == (LayerSource(1, 'wallpapers/*'),
                                LayerSource(2, '*.png', external, '{key}_截图'))


def test_cross_pairing_moves_only_requested_layer_and_is_repeatable():
    jobs = [('A', ('A1', 'A2')), ('B', ('B1', 'B2')), ('C', ('C1', 'C2'))]
    options = BatchOptions((LayerSource(1, '*'), LayerSource(2, '*')), 'grouped',
                           (), 'Demo_', 1, 500, pairing='next', cross_layers=(2,))
    assert apply_pairing(jobs, options) == [('A', ('A1', 'B2')), ('B', ('B1', 'C2')), ('C', ('C1', 'A2'))]
    shuffled = replace(options, pairing='shuffle', seed=12)
    result = apply_pairing(jobs, shuffled)
    assert result == apply_pairing(jobs, shuffled)
    assert all(theme != paths[1][0] for theme, paths in result)


def test_visual_plan_selects_only_known_candidate_images(tmp_path):
    first, second = tmp_path / 'a.png', tmp_path / 'b.png'
    candidates = {'I1': (first, tmp_path), 'I2': (second, tmp_path)}
    layers = [{'id': 1, 'name': '封面'}, {'id': 2, 'name': '截图'}]
    response = {'selections': [[{'layer_id': 2, 'image_id': 'I2'}, {'layer_id': 1, 'image_id': 'I1'}]]}
    plan = validate_batch_instruction(response, '按画面选图', {}, layers, visual=((tmp_path,), candidates))
    assert plan['mode'] == 'selected'
    assert plan['selected_jobs'] == (('搭配 1', (second, first)),)
    response['selections'][0][0]['image_id'] = 'I3'
    with pytest.raises(ValueError, match='候选目录之外'):
        validate_batch_instruction(response, '按画面选图', {}, layers, visual=((tmp_path,), candidates))


def test_paste_sources_preview_samples_and_close_reordered_tab(tmp_path, monkeypatch):
    from PyQt6.QtCore import Qt, QTimer
    from PyQt6.QtGui import QColor, QImage
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QPushButton, QTabBar, QToolButton
    from ui.image_editor import ImageEditorContainer
    from ui.image_template_batch import SampleImage, TemplateBatchDialog

    monkeypatch.setenv('CEP_IMAGE_RECOVERY_DIR', str(tmp_path / 'recovery'))
    app = QApplication.instance() or QApplication([])
    host = ImageEditorContainer()
    original = host.current_widget()
    dialog = TemplateBatchDialog(original, original._snapshot())
    try:
        dialog.show()
        app.clipboard().setText(str(tmp_path))
        dialog.source_display.setFocus()
        QTest.keyClick(dialog.source_display, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        assert dialog._typed_sources() == [str(tmp_path)]
        assert not dialog.source_display.isReadOnly()
        for name, color in [('第一张', 'red'), ('第二张', 'blue')]:
            image = QImage(30, 20, QImage.Format.Format_ARGB32)
            image.fill(QColor(color))
            dialog._show_sample(name, image, 1000, '')
        dialog.preview_area.show()
        previews = [button for button in dialog.preview_area.findChildren(QPushButton)]
        assert [button.text() for button in previews] == ['放大预览', '放大预览']
        opened = []

        def inspect_preview():
            popup = app.activeModalWidget()
            opened.append((popup.windowTitle(), len(popup.findChildren(SampleImage))))
            popup.reject()

        QTimer.singleShot(0, inspect_preview)
        previews[0].click()
        assert opened == [('第一张', 1)]
        assert host.tab.count() == 1
        assert dialog.previews.count() == 2 and not original.project.artboards

        blank = host.new_document(30, 20, '关闭测试')
        bar = host.tab.tabBar()
        bar.moveTab(host.tab.indexOf(blank), 0)
        close = bar.tabButton(0, QTabBar.ButtonPosition.RightSide)
        assert isinstance(close, QToolButton) and not close.icon().isNull()
        close.click()
        assert host.tab.indexOf(blank) == -1 and host.tab.indexOf(original) >= 0
    finally:
        dialog.close()
        host.close()
