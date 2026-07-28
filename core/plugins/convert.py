# -*- coding: utf-8 -*-
"""格式转换（导出插件，仅影响输出格式）。

Saver 读取 ctx['convert']['format'] 决定输出扩展名：
    png / jpg / jpeg / webp
实际像素数组原样返回。
"""
from core.plugins import register


@register
class Convert:
    NAME = "convert"
    LABEL = "格式转换"
    CATEGORY = "output"

    def run(self, image, ctx):
        return image
