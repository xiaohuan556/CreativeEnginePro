"""Native desktop account management for CreativeEnginePro administrators."""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from core.release_gate import AuthenticationError, AuthenticationUnavailable, DesktopAuthClient


ROLE_LABELS = {
    "admin": "管理员", "producer": "制片人", "director": "导演",
    "editor": "剪辑", "reviewer": "审核", "viewer": "只读",
}
STATUS_LABELS = {"active": "可登录", "pending": "待批准", "suspended": "已停用"}


def _api_message(error: Exception) -> str:
    return str(error) or "操作失败，请检查本地服务是否正在运行。"


class AccountEditorDialog(QDialog):
    """Compact add/edit form shown from the account list."""

    def __init__(self, account: dict[str, Any] | None = None, parent=None):
        super().__init__(parent)
        self.account = account
        self.setWindowTitle("编辑账号" if account else "新增登录账号")
        self.setFixedWidth(510)
        self.setObjectName("accountEditor")
        self.setStyleSheet("""
            QDialog#accountEditor { background:#1e1e1e; color:#eeeeee; }
            QLabel { color:#b7b7b7; }
            QLineEdit, QComboBox, QSpinBox {
                min-height:35px; padding:0 9px; color:#eeeeee;
                background:#252525; border:1px solid #444444; border-radius:3px;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color:#3d8ef8; }
            QCheckBox { color:#c5c5c5; spacing:8px; }
            QPushButton { min-height:36px; padding:0 18px; border:1px solid #444444; border-radius:3px; background:#2d2d2d; color:#cccccc; }
            QPushButton[text="保存"], QPushButton[text="创建账号"] { color:white; background:#3d8ef8; border:0; }
        """)

        self.username = QLineEdit()
        self.username.setPlaceholderText("英文、数字、点、横线或下划线")
        self.display_name = QLineEdit()
        self.display_name.setPlaceholderText("显示在桌面端的名称")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("至少 12 位" if not account else "留空表示不修改")
        self.role = QComboBox()
        for value, label in ROLE_LABELS.items():
            self.role.addItem(label, value)
        self.status = QComboBox()
        for value, label in STATUS_LABELS.items():
            self.status.addItem(label, value)
        self.approved = QCheckBox("创建后立即允许登录")
        self.approved.setChecked(True)
        self.paid = QCheckBox("允许使用付费模型")
        self.daily_tasks = self._spin(0, 10000, 50)
        self.daily_credits = self._spin(0, 10_000_000, 5000)
        self.concurrent = self._spin(0, 50, 2)
        self.daily_asset = self._spin(0, 10_000_000, 2048, " MB")
        self.storage = self._spin(0, 10_000_000, 20480, " MB")
        self.models = QLineEdit()
        self.models.setPlaceholderText("留空仅可用本地处理 / Edge TTS；多个模型用英文逗号分隔")

        form = QFormLayout()
        form.setContentsMargins(24, 22, 24, 8)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        form.addRow("登录账号", self.username)
        form.addRow("显示名称", self.display_name)
        form.addRow("登录密码", self.password)
        form.addRow("账号角色", self.role)
        if account:
            form.addRow("账号状态", self.status)
        else:
            form.addRow("登录权限", self.approved)
        form.addRow("任务权限", self.paid)
        form.addRow("每日任务数", self.daily_tasks)
        form.addRow("每日额度", self.daily_credits)
        form.addRow("并发任务数", self.concurrent)
        form.addRow("每日素材额度", self.daily_asset)
        form.addRow("总存储额度", self.storage)
        form.addRow("限定模型", self.models)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存" if account else "创建账号")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        layout.setContentsMargins(0, 0, 0, 18)
        if account:
            self._load(account)

    @staticmethod
    def _spin(minimum: int, maximum: int, value: int, suffix: str = "") -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        widget.setValue(value)
        if suffix:
            widget.setSuffix(suffix)
        return widget

    def _load(self, account: dict[str, Any]) -> None:
        limits = account.get("limits") or {}
        self.username.setText(str(account.get("username") or ""))
        self.username.setEnabled(False)
        self.display_name.setText(str(account.get("display_name") or ""))
        self._select(self.role, str(account.get("role") or "producer"))
        self._select(self.status, str(account.get("status") or "pending"))
        self.paid.setChecked(bool(limits.get("allow_paid_models")))
        self.daily_tasks.setValue(int(limits.get("daily_tasks") or 0))
        self.daily_credits.setValue(int(limits.get("daily_credits") or 0))
        self.concurrent.setValue(int(limits.get("concurrent_tasks") or 0))
        self.daily_asset.setValue(int(limits.get("daily_asset_mb") or 0))
        self.storage.setValue(int(limits.get("storage_mb") or 0))
        self.models.setText(", ".join(str(item) for item in limits.get("allowed_models") or []))

    @staticmethod
    def _select(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _accept_if_valid(self) -> None:
        if not self.account and not self.username.text().strip():
            QMessageBox.warning(self, "信息不完整", "请输入登录账号。")
            return
        if not self.account and len(self.password.text()) < 12:
            QMessageBox.warning(self, "密码过短", "初始密码至少需要 12 位。")
            return
        self.accept()

    def payload(self) -> dict[str, Any]:
        models = [item.strip() for item in self.models.text().split(",") if item.strip()]
        common: dict[str, Any] = {
            "display_name": self.display_name.text().strip(),
            "role": self.role.currentData(),
            "daily_tasks": self.daily_tasks.value(),
            "daily_credits": self.daily_credits.value(),
            "concurrent_tasks": self.concurrent.value(),
            "daily_asset_mb": self.daily_asset.value(),
            "storage_mb": self.storage.value(),
            "allow_paid_models": self.paid.isChecked(),
            "allowed_models": models,
        }
        if self.account:
            common["status"] = self.status.currentData()
            if self.password.text():
                common["password"] = self.password.text()
        else:
            common.update({
                "username": self.username.text().strip(),
                "password": self.password.text(),
                "approved": self.approved.isChecked(),
            })
        return common


class DesktopAccountAdminDialog(QDialog):
    def __init__(self, client: DesktopAuthClient, parent=None):
        super().__init__(parent)
        self.client = client
        self.accounts: list[dict[str, Any]] = []
        self.setWindowTitle("CreativeEnginePro · 桌面端账号管理")
        self.resize(1040, 650)
        self.setMinimumSize(900, 560)
        self.setObjectName("desktopAccountAdmin")
        self.setStyleSheet("""
            QDialog#desktopAccountAdmin { background:#1e1e1e; color:#eeeeee; }
            QLabel#heading { color:#f2f2f2; font-size:22px; font-weight:700; }
            QLabel#subheading { color:#888888; font-size:11px; }
            QLabel#summary { color:#aaaaaa; background:#252525; border:1px solid #383838; border-radius:3px; padding:8px 12px; }
            QTableWidget { background:#1a1a1a; alternate-background-color:#1e1e1e; color:#dddddd;
                border:1px solid #333333; border-radius:3px; gridline-color:#2a2a2a; }
            QTableWidget::item { padding:7px; }
            QTableWidget::item:selected { background:#3d3d3d; color:#ffffff; }
            QHeaderView::section { color:#aaaaaa; background:#2d2d2d; border:0;
                border-right:1px solid #333333; border-bottom:1px solid #3a3a3a; padding:8px; font-weight:600; }
            QPushButton { min-height:36px; padding:0 15px; color:#cccccc;
                background:#2d2d2d; border:1px solid #444444; border-radius:3px; }
            QPushButton:hover { color:white; background:#383838; border-color:#666666; }
            QPushButton#primary { color:white; background:#3d8ef8; border:0; font-weight:700; }
            QPushButton#primary:hover { background:#4a9df9; }
            QPushButton#danger { color:#e47777; background:#2d1a1a; border-color:#6b2020; }
            QPushButton:disabled { color:#666666; background:#252525; border-color:#333333; }
        """)

        heading = QLabel("桌面端账号管理")
        heading.setObjectName("heading")
        subheading = QLabel("在这里统一添加、编辑和删除可登录桌面端的账号。Web 产品账号逻辑不受影响。")
        subheading.setObjectName("subheading")
        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        title_box.addWidget(heading)
        title_box.addWidget(subheading)
        self.summary = QLabel("正在读取账号…")
        self.summary.setObjectName("summary")
        header = QHBoxLayout()
        header.addLayout(title_box)
        header.addStretch(1)
        header.addWidget(self.summary)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "登录账号", "显示名称", "角色", "状态", "在线会话",
            "每日任务", "每日额度", "限定模型",
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.doubleClicked.connect(self._edit)

        self.add_button = QPushButton("＋ 新增账号")
        self.add_button.setObjectName("primary")
        self.add_button.clicked.connect(self._add)
        self.edit_button = QPushButton("编辑账号")
        self.edit_button.clicked.connect(self._edit)
        self.revoke_button = QPushButton("强制下线")
        self.revoke_button.clicked.connect(self._revoke)
        self.delete_button = QPushButton("删除账号")
        self.delete_button.setObjectName("danger")
        self.delete_button.clicked.connect(self._delete)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        toolbar = QHBoxLayout()
        toolbar.addWidget(self.add_button)
        toolbar.addWidget(self.edit_button)
        toolbar.addWidget(self.revoke_button)
        toolbar.addWidget(self.delete_button)
        toolbar.addStretch(1)
        toolbar.addWidget(refresh)
        toolbar.addWidget(close)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(16)
        layout.addLayout(header)
        layout.addWidget(self.table, 1)
        layout.addLayout(toolbar)
        self._selection_changed()
        self.refresh()

    def selected_account(self) -> dict[str, Any] | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.accounts):
            return None
        return self.accounts[row]

    def _selection_changed(self) -> None:
        enabled = self.selected_account() is not None
        self.edit_button.setEnabled(enabled)
        self.revoke_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)

    def refresh(self) -> None:
        try:
            self.accounts = list(self.client.request_json("GET", "/api/admin/users").get("users") or [])
        except (AuthenticationError, AuthenticationUnavailable) as error:
            QMessageBox.warning(self, "账号读取失败", _api_message(error))
            return
        self.table.setRowCount(len(self.accounts))
        active = 0
        for row, account in enumerate(self.accounts):
            limits = account.get("limits") or {}
            if account.get("status") == "active":
                active += 1
            values = [
                account.get("username") or "", account.get("display_name") or "",
                ROLE_LABELS.get(str(account.get("role")), str(account.get("role") or "")),
                STATUS_LABELS.get(str(account.get("status")), str(account.get("status") or "")),
                str(account.get("active_sessions") or 0), str(limits.get("daily_tasks") or 0),
                str(limits.get("daily_credits") or 0),
                ", ".join(str(item) for item in limits.get("allowed_models") or []) or "仅本地处理 / Edge TTS",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column in (4, 5, 6):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, column, item)
        self.table.clearSelection()
        self.summary.setText(f"共 {len(self.accounts)} 个账号  ·  {active} 个可登录")
        self._selection_changed()

    def _add(self) -> None:
        editor = AccountEditorDialog(parent=self)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.client.request_json("POST", "/api/admin/users", editor.payload(), write=True)
        except (AuthenticationError, AuthenticationUnavailable) as error:
            QMessageBox.warning(self, "创建失败", _api_message(error))
            return
        self.refresh()

    def _edit(self, *_args) -> None:
        account = self.selected_account()
        if not account:
            return
        editor = AccountEditorDialog(account, self)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.client.request_json(
                "PATCH", f"/api/admin/users/{account['id']}", editor.payload(), write=True)
        except (AuthenticationError, AuthenticationUnavailable) as error:
            QMessageBox.warning(self, "保存失败", _api_message(error))
            return
        self.refresh()

    def _revoke(self) -> None:
        account = self.selected_account()
        if not account:
            return
        answer = QMessageBox.question(
            self, "强制下线",
            f"确定让账号“{account.get('username')}”的所有已登录设备立即退出吗？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self.client.request_json(
                "POST", f"/api/admin/users/{account['id']}/revoke-sessions", write=True)
        except (AuthenticationError, AuthenticationUnavailable) as error:
            QMessageBox.warning(self, "操作失败", _api_message(error))
            return
        QMessageBox.information(self, "已强制下线", f"已撤销 {result.get('revoked', 0)} 个登录会话。")
        self.refresh()

    def _delete(self) -> None:
        account = self.selected_account()
        if not account:
            return
        answer = QMessageBox.warning(
            self, "确认删除账号",
            f"确定永久删除登录账号“{account.get('username')}”吗？\n\n"
            "如果该账号已有项目或生成记录，系统会阻止删除，届时可改为“已停用”。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.client.request_json(
                "DELETE", f"/api/admin/users/{account['id']}", write=True)
        except (AuthenticationError, AuthenticationUnavailable) as error:
            QMessageBox.warning(self, "删除失败", _api_message(error))
            return
        self.refresh()
