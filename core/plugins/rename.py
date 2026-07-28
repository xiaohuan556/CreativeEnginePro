# -*- coding: utf-8 -*-
"""批量重命名（导出插件，仅影响输出文件名）。

具体命名规则由 Saver 读取 ctx['rename'] 实现：
    pattern  文件名模板，支持占位符 {name}(原文件名) 与 {num}(4 位序号)
    例："{name}" / "{num}_water" / "pic_{num}"
实际像素数组原样返回。
"""
from core.plugins import register


@register
class Rename:
    NAME = "rename"
    LABEL = "批量重命名"
    CATEGORY = "output"

    def run(self, image, ctx):
        return image
