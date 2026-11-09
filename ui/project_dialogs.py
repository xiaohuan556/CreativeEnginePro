"""File picker shared by the desktop project editors."""

from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from core.project_files import project_path


def choose_project_save_path(parent, title, suggested, file_filter, suffix):
    path, _ = QFileDialog.getSaveFileName(parent, title, str(suggested), file_filter)
    if not path:
        return None
    target = project_path(path, suffix)
    if target != Path(path) and target.exists():
        answer = QMessageBox.question(
            parent, title, f"要覆盖已有的工程文件吗？\n{target}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return None
    return target
