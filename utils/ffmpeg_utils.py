# utils/ffmpeg_utils.py
import os
import subprocess
import sys


def run_media_command(command, **kwargs):
    """Run FFmpeg or FFprobe without opening a Windows console."""
    kwargs.setdefault("creationflags", getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return subprocess.run(command, **kwargs)


def start_media_process(command, **kwargs):
    """Start a background FFmpeg or FFplay process."""
    kwargs.setdefault("creationflags", getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return subprocess.Popen(command, **kwargs)


def get_ffmpeg_path() -> str:
    if hasattr(sys, '_MEIPASS'):
        # 打包后，ffmpeg.exe 在 _MEIPASS 根目录
        return os.path.join(sys._MEIPASS, 'ffmpeg.exe')
    else:
        # 开发环境，ffmpeg.exe 在项目根目录
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'ffmpeg.exe')
