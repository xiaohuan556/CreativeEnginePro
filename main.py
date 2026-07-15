import os
import sys
import traceback
from datetime import datetime

# 强制 ffmpeg 单线程解码（OpenCV 后端）：
# 同一进程内多个 cv2.VideoCapture 争夺 async_lock → crash。
# 必须在任何 cv2 import 之前设置。
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")

# --- 1. 路径兼容处理 (必须最先运行) ---
if hasattr(sys, '_MEIPASS'):
    base_path = sys._MEIPASS
else:
    base_path = os.path.abspath(".")

sys.path.insert(0, base_path)
sys.path.insert(0, os.path.join(base_path, "core"))
sys.path.insert(0, os.path.join(base_path, "ui"))

# --- 2. 先创建 QApplication（必须在任何 Qt 对象创建之前）---
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtGui import QFont

def check_expiry():
    expire_date = datetime(2026, 10, 1)
    if datetime.now() > expire_date:
        return True
    return False

if __name__ == '__main__':
    app = QApplication(sys.argv)

    # 授权检查逻辑
    if check_expiry():
        QMessageBox.critical(None, "授权过期", "软件授权已到期，请联系管理员续期。")
        sys.exit(1)

    app.setFont(QFont("Segoe UI", 9))
    app.setStyleSheet("QToolTip { color: #ffffff; background-color: #2a2a2a; border: 1px solid #555555; padding: 2px 6px; }")

    # ==========================================
    # 全局异常捕获逻辑（防闪退神器）
    # ==========================================
    def handle_exception(exc_type, exc_value, exc_traceback):
        """拦截所有未捕获的异常并弹窗显示"""
        error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Critical)
        msg.setWindowTitle("功能运行出错")
        msg.setText("程序在执行操作时发生崩溃，详情如下：")
        msg.setInformativeText(error_msg)
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()

    sys.excepthook = handle_exception

    # --- 3. QApplication 就绪后再导入业务逻辑 ---
    from ui.main_window import UltimateEngine

    window = UltimateEngine()
    window.show()
    sys.exit(app.exec()) 