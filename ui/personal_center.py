"""Desktop account centre with an explicit, unambiguous sign-out action."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout,
)

from core.release_gate import DesktopAuthClient, ReleasePolicy


ROLE_LABELS = {
    "admin": "管理员",
    "producer": "制片员",
    "director": "导演",
    "editor": "剪辑师",
    "reviewer": "审核员",
    "viewer": "查看者",
}


class PersonalCenterDialog(QDialog):
    """Shows the active desktop identity and exposes one clear sign-out action."""

    logoutRequested = pyqtSignal()

    def __init__(
            self, policy: ReleasePolicy, client: DesktopAuthClient,
            user: dict, parent=None):
        super().__init__(parent)
        self.policy = policy
        self.client = client
        self.user = dict(user or {})
        self._logout_pending = False
        self.setWindowTitle("CreativeEnginePro · 个人中心")
        self.setModal(True)
        self.setFixedSize(620, 580)
        self.setObjectName("personalCenter")
        self.setStyleSheet("""
            QDialog#personalCenter { background:#1e1e1e; color:#d2d2d2; }
            QFrame#profileCard, QFrame#detailCard {
                background:#232323; border:1px solid #3a3a3a; border-radius:6px;
            }
            QLabel#avatar {
                color:white; background:#2f7de1; border-radius:27px;
                font-size:21px; font-weight:700;
            }
            QLabel#displayName { color:#f3f3f3; font-size:19px; font-weight:700; }
            QLabel#accountName { color:#858585; font-size:11px; }
            QLabel#roleBadge {
                color:#8fc3ff; background:#1b2c40; border:1px solid #31577e;
                border-radius:3px; padding:4px 9px; font-size:10px;
            }
            QLabel#sectionTitle { color:#eeeeee; font-size:13px; font-weight:700; }
            QLabel#fieldName { color:#858585; font-size:11px; }
            QLabel#fieldValue { color:#d6d6d6; font-size:11px; }
            QPlainTextEdit#modelList {
                color:#b9cce3; background:#191919; border:1px solid #333333;
                border-radius:4px; padding:7px; font-size:11px;
            }
            QLabel#logoutHint { color:#858585; font-size:10px; }
            QPushButton { min-height:38px; border-radius:4px; font-size:11px; }
            QPushButton#manageAccounts {
                color:#9bc9ff; background:#202b39; border:1px solid #35577e;
            }
            QPushButton#manageAccounts:hover { color:white; border-color:#3d8ef8; }
            QPushButton#logoutAccount {
                color:#ff9898; background:#332020; border:1px solid #6f3535;
            }
            QPushButton#logoutAccount:hover { color:white; background:#482525; border-color:#d86464; }
            QPushButton:disabled { color:#666; background:#292929; border-color:#363636; }
        """)

        username = str(self.user.get("username") or "未命名账号")
        display_name = str(self.user.get("display_name") or username)
        role = str(self.user.get("role") or "viewer")
        role_label = ROLE_LABELS.get(role, role)
        initial = (display_name.strip() or username)[:1].upper()

        avatar = QLabel(initial)
        avatar.setObjectName("avatar")
        avatar.setFixedSize(54, 54)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        names = QVBoxLayout()
        names.setSpacing(3)
        title = QLabel(display_name)
        title.setObjectName("displayName")
        account = QLabel(f"登录账号：{username}")
        account.setObjectName("accountName")
        names.addWidget(title)
        names.addWidget(account)
        badge = QLabel(role_label)
        badge.setObjectName("roleBadge")

        profile_row = QHBoxLayout()
        profile_row.setContentsMargins(18, 16, 18, 16)
        profile_row.setSpacing(13)
        profile_row.addWidget(avatar)
        profile_row.addLayout(names, 1)
        profile_row.addWidget(badge)
        profile_card = QFrame()
        profile_card.setObjectName("profileCard")
        profile_card.setLayout(profile_row)

        limits = dict(self.user.get("limits") or {})
        detail_grid = QGridLayout()
        detail_grid.setContentsMargins(18, 15, 18, 15)
        detail_grid.setHorizontalSpacing(24)
        detail_grid.setVerticalSpacing(10)
        detail_rows = [
            ("账号状态", "正常" if self.user.get("status", "active") == "active" else str(self.user.get("status"))),
            ("每日任务上限", self._limit_text(limits.get("daily_tasks"), "次")),
            ("同时任务上限", self._limit_text(limits.get("concurrent_tasks"), "个")),
            ("每日积分上限", self._limit_text(limits.get("daily_credits"), "")),
            ("每日素材上限", self._limit_text(limits.get("daily_asset_mb"), " MB")),
            ("存储空间上限", self._limit_text(limits.get("storage_mb"), " MB")),
            ("付费模型", "允许使用" if limits.get("allow_paid_models") else "未开放"),
        ]
        for row, (name, value) in enumerate(detail_rows):
            name_label = QLabel(name)
            name_label.setObjectName("fieldName")
            value_label = QLabel(value)
            value_label.setObjectName("fieldValue")
            value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            detail_grid.addWidget(name_label, row, 0)
            detail_grid.addWidget(value_label, row, 1)
        detail_card = QFrame()
        detail_card.setObjectName("detailCard")
        detail_card.setLayout(detail_grid)

        models = [str(value).strip() for value in limits.get("allowed_models", []) if str(value).strip()]
        models_text = "、".join(models) if models else "仅允许本地功能 / Edge TTS；其他 AI 模型尚未授权"
        models_label = QPlainTextEdit()
        models_label.setObjectName("modelList")
        models_label.setPlainText(models_text)
        models_label.setReadOnly(True)
        models_label.setMaximumHeight(82)

        self.manage_accounts = QPushButton("管理员账号管理")
        self.manage_accounts.setObjectName("manageAccounts")
        self.manage_accounts.setVisible(role == "admin")
        self.manage_accounts.clicked.connect(self._open_account_admin)

        self.logout_status = QLabel(
            "退出当前账号会清除本机登录记忆并返回登录页；本地项目和素材不会删除。")
        self.logout_status.setObjectName("logoutHint")
        self.logout_status.setWordWrap(True)

        self.logout_button = QPushButton("退出当前账号")
        self.logout_button.setObjectName("logoutAccount")
        self.logout_button.clicked.connect(self._confirm_logout)

        version = QLabel(
            f"桌面版本 {policy.version}  ·  授权有效至 {policy.expiry_label}")
        version.setObjectName("logoutHint")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 18)
        root.setSpacing(12)
        root.addWidget(profile_card)
        section = QLabel("账号权限与额度")
        section.setObjectName("sectionTitle")
        root.addWidget(section)
        root.addWidget(detail_card)
        model_title = QLabel("允许使用的模型")
        model_title.setObjectName("sectionTitle")
        root.addWidget(model_title)
        root.addWidget(models_label)
        root.addWidget(self.manage_accounts)
        root.addStretch(1)
        root.addWidget(self.logout_status)
        root.addWidget(self.logout_button)
        root.addWidget(version)

    @staticmethod
    def _limit_text(value, suffix: str) -> str:
        try:
            number = int(value or 0)
        except (TypeError, ValueError):
            number = 0
        return f"{number}{suffix}" if number > 0 else "未开放"

    def _open_account_admin(self) -> None:
        from ui.desktop_account_admin import DesktopAccountAdminDialog
        DesktopAccountAdminDialog(self.client, self).exec()

    def _confirm_logout(self) -> None:
        answer = QMessageBox.question(
            self,
            "退出当前账号",
            "确定退出当前账号吗？\n\n"
            "这会撤销服务器登录会话、清除本机的登录记忆，并返回登录界面。\n"
            "本地项目和素材不会删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.set_logout_pending(True)
        self.logoutRequested.emit()

    def set_logout_pending(self, pending: bool, message: str = "") -> None:
        self._logout_pending = pending
        self.logout_button.setEnabled(not pending)
        self.manage_accounts.setEnabled(not pending)
        self.logout_button.setText("正在退出登录…" if pending else "退出当前账号")
        if message:
            self.logout_status.setText(message)
