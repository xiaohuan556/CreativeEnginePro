"""
小欢语音 - 流水线处理工作线程
将 core/pipeline.Pipeline 的执行封装到 QThread 中
"""
from PyQt6.QtCore import QThread, pyqtSignal
from pathlib import Path
from typing import Optional


class PipelineWorker(QThread):
    """
    后台流水线处理线程

    5 步流水线：
    1. 音视频分离 → 2. AI听写翻译 → 3. TTS配音 → 4. 音画对齐 → 5. 混剪渲染

    用法：
        from core.pipeline import PipelineConfig
        config = PipelineConfig(video_path=Path("video.mp4"), ...)
        worker = PipelineWorker(config)
        worker.step_update.connect(on_step)
        worker.finished.connect(on_done)
        worker.start()
    """

    # 信号
    step_update = pyqtSignal(int, str)    # (step_index 0-4, status: "active"|"done"|"error")
    progress = pyqtSignal(int)             # 0-100 总进度
    log = pyqtSignal(str, str)            # (message, level: "info"|"warn"|"error"|"success")
    finished = pyqtSignal(str)            # 最终输出文件路径
    error = pyqtSignal(str)               # 错误消息

    STEP_NAMES = ["音视频分离", "AI听写翻译", "TTS配音", "音画对齐", "混剪渲染"]

    def __init__(self, config):
        """
        Args:
            config: PipelineConfig 实例（来自 core.pipeline）
        """
        super().__init__()
        self._config = config
        self._pipeline = None

    def run(self):
        """在线程中执行完整流水线"""
        try:
            from core.pipeline import Pipeline

            def on_step(idx: int, status: str):
                self.step_update.emit(idx, status)

            def on_progress(pct: int, name: str):
                self.progress.emit(pct)
                self.log.emit(f"[{pct}%] {name}", "info")

            def on_log(text: str):
                self.log.emit(text, "info")

            self.log.emit("流水线启动...", "info")

            self._pipeline = Pipeline(
                self._config,
                on_step=on_step,
                on_progress=on_progress,
                on_log=on_log,
            )

            results = self._pipeline.run()

            # 查找最终输出
            output_path = ""
            for r in results:
                if r.success and r.output_path and str(r.output_path).endswith(".mp4"):
                    output_path = str(r.output_path)
                    break

            if output_path:
                self.log.emit(f"流水线完成 → {output_path}", "success")
                self.finished.emit(output_path)
            else:
                self.error.emit("流水线完成但未生成输出文件")

        except Exception as e:
            self.error.emit(str(e))
            self.log.emit(f"流水线错误: {e}", "error")

    def cancel(self):
        """取消流水线"""
        if self._pipeline:
            self._pipeline.cancel()
