# utils/ffmpeg_utils.py
import os
import sys

def get_ffmpeg_path() -> str:
    if hasattr(sys, '_MEIPASS'):
        # 打包后，ffmpeg.exe 在 _MEIPASS 根目录
        return os.path.join(sys._MEIPASS, 'ffmpeg.exe')
    else:
        # 开发环境，ffmpeg.exe 在项目根目录
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'ffmpeg.exe')