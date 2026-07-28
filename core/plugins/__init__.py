# -*- coding: utf-8 -*-
"""批处理插件注册表。

每个插件是一个类，实现：
    NAME     唯一键
    LABEL    界面显示名
    CATEGORY 'pixel'(变换像素数组) | 'output'(仅影响导出/命名)
    run(image: np.ndarray, ctx: dict) -> np.ndarray

pixel 插件返回变换后的数组；output 插件原样返回数组（其配置已写在 ctx 中由 Saver 读取）。
UI 通过 DEFAULT_ORDER 与 PLUGINS 列出可勾选/排序的步骤。
"""
PLUGINS = {}
DEFAULT_ORDER = []


def register(cls):
    PLUGINS[cls.NAME] = cls
    if cls.NAME not in DEFAULT_ORDER:
        DEFAULT_ORDER.append(cls.NAME)
    return cls


from .watermark_fixed import WatermarkFixed
from .superres import Superres
from .denoise import Denoise
from .resize import Resize
from .rename import Rename
from .convert import Convert
from .compress import Compress

# 界面默认勾选顺序（去水印 → 超分 → 去噪 → 改比例 → 重命名 → 格式 → 压缩）
DISPLAY_ORDER = [
    "watermark_fixed", "superres", "denoise", "resize",
    "rename", "convert", "compress",
]


def get_plugin(name):
    return PLUGINS.get(name)


def iter_plugins():
    for n in DISPLAY_ORDER:
        if n in PLUGINS:
            yield PLUGINS[n]
