# -*- coding: utf-8 -*-
"""压缩 / 质量控制（导出插件，仅影响保存质量）。

Saver 读取 ctx['compress']['quality']（1-100，默认 95）应用于 jpg/webp。
实际像素数组原样返回。
"""
from core.plugins import register


@register
class Compress:
    NAME = "compress"
    LABEL = "压缩 / 画质"
    CATEGORY = "output"

    def run(self, image, ctx):
        return image
