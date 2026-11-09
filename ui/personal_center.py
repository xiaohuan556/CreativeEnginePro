"""Desktop account centre with an explicit, unambiguous sign-out action."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from core.release_gate import DesktopAuthClient, ReleasePolicy
from ui.widgets.workbench_controls import workbench_icon
from utils.app_paths import resource_root


ROLE_LABELS = {
    "admin": "管理员",
    "producer": "制片员",
    "director": "导演",
    "editor": "剪辑师",
    "reviewer": "审核员",
    "viewer": "查看者",
}

MODEL_LABELS = {
    "openai": "OpenAI", "deepseek": "DeepSeek", "gptimage": "GPT Image",
    "seedream": "Seedream", "seedance": "Seedance", "veo": "Veo",
    "fish_audio": "Fish Audio", "elevenlabs": "ElevenLabs",
    "siliconflow": "SiliconFlow", "deepgram": "Deepgram", "edge_tts": "Edge TTS",
}

STYLE = """
    QDialog#personalCenter { background: #0d111c; }
    #personalCenter QWidget { background: transparent; color: #e9edf8; }
    #personalCenter QScrollArea { border: none; padding: 0; }
    #personalCenter QScrollBar:vertical { background: #111625; width: 8px; margin: 0; }
    #personalCenter QScrollBar::handle:vertical {
        background: #3b4563; border-radius: 4px; min-height: 26px;
    }
    #personalCenter QScrollBar::add-line:vertical,
    #personalCenter QScrollBar::sub-line:vertical { height: 0; }
    #personalCenter QScrollBar::add-page:vertical,
    #personalCenter QScrollBar::sub-page:vertical { background: transparent; }
    #personalCenter QLabel#pageTitle { color: #f6f7ff; font-size: 28px; font-weight: 700; }
    #personalCenter QLabel#brand { color: #c6ccdf; font-size: 13px; font-weight: 600; }
    #personalCenter QLabel#brandIcon {
        background: #eae9ff; border-radius: 9px;
    }
    #personalCenter QLabel#muted { color: #9da8bf; font-size: 12px; }
    #personalCenter QFrame#profileCard {
        background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #26234a,stop:0.58 #191e35,stop:1 #141c2d);
        border: 1px solid #3b3b5c; border-radius: 16px;
    }
    #personalCenter QLabel#avatar {
        background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #a493ff,stop:1 #6252d6);
        border: 1px solid #ae9dff; border-radius: 17px;
        color: #fff; font-size: 26px; font-weight: 700;
    }
    #personalCenter QLabel#displayName { color: #faf9ff; font-size: 22px; font-weight: 700; }
    #personalCenter QLabel#accountName { color: #c1c8e0; font-size: 12px; }
    #personalCenter QLabel#roleBadge, #personalCenter QLabel#statusBadge {
        border-radius: 9px; padding: 5px 10px; font-size: 12px; font-weight: 600;
    }
    #personalCenter QLabel#roleBadge { color: #ded7ff; background: #30294f; border: 1px solid #524575; }
    #personalCenter QLabel#statusBadge { color: #f3c792; background: #332921; border: 1px solid #5c4635; }
    #personalCenter QLabel#statusBadge[active="true"] {
        color: #a9ebd2; background: #17332f; border: 1px solid #2e5149;
    }
    #personalCenter QLabel#sectionTitle { color: #eff2ff; font-size: 16px; font-weight: 700; }
    #personalCenter QLabel#count {
        color: #c0b5ff; background: #221f3c; border-radius: 8px; padding: 4px 8px; font-size: 11px;
    }
    #personalCenter QFrame#metricCard, #personalCenter QFrame#modelCard {
        background: #151b2a; border: 1px solid #2b354c; border-radius: 12px;
    }
    #personalCenter QLabel#metricLabel { color: #aeb9cf; font-size: 12px; }
    #personalCenter QLabel#metricValue { color: #f0efff; font-size: 20px; font-weight: 700; }
    #personalCenter QLabel#metricValue[qualitative="true"] { color: #d3caff; font-size: 16px; }
    #personalCenter QLabel#modelChip {
        color: #dce3f7; background: #20283c; border: 1px solid #36415c;
        border-radius: 8px; padding: 6px 8px; font-size: 12px; font-weight: 600;
    }
    #personalCenter QFrame#footerDivider { background: #273147; border: 0; }
    #personalCenter QLabel#logoutHint { color: #b7c0d5; font-size: 12px; }
    #personalCenter QLabel#metadata { color: #94a0b8; font-size: 11px; }
    #personalCenter QPushButton {
        min-height: 36px; padding: 0 12px; border-radius: 9px;
        font-size: 12px; font-weight: 600;
    }
    #personalCenter QPushButton#manageAccounts {
        color: #e8e2ff; background: #302750; border: 1px solid #5b4d88;
    }
    #personalCenter QPushButton#manageAccounts:hover { background: #413466; border-color: #9582df; }
    #personalCenter QPushButton#manageAccounts:pressed { background: #26203f; }
    #personalCenter QPushButton#logoutAccount {
        color: #f6b4c0; background: #2a1d29; border: 1px solid #623c51;
    }
    #personalCenter QPushButton#logoutAccount:hover { background: #422536; border-color: #bb6d86; }
    #personalCenter QPushButton#logoutAccount:pressed { background: #311b29; }
    #personalCenter QPushButton#manageAccounts:focus,
    #personalCenter QPushButton#logoutAccount:focus { border-color: #bcb0ff; }
    #personalCenter QPushButton#manageAccounts:disabled,
    #personalCenter QPushButton#logoutAccount:disabled {
        color: #8690a9; background: #1a2030; border-color: #333c50;
    }
"""


def _label(text: str, name: str, *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


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
        self.setWindowTitle("小欢ovo · 个人中心")
        self.setModal(True)
        self.setObjectName("personalCenter")
        self.setWindowIcon(QIcon(str(resource_root() / "assets" / "icon.ico")))
        self.setStyleSheet(STYLE)

        role = str(self.user.get("role") or "viewer")
        limits = dict(self.user.get("limits") or {})
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 18)
        root.setSpacing(14)
        root.addLayout(self._header())

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(16)
        content_layout.addWidget(self._profile_card(role))
        content_layout.addLayout(self._limits_section(limits))
        content_layout.addLayout(self._models_section(limits))
        content_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        root.addLayout(self._footer(role, policy))

        screen = parent.screen() if parent is not None else self.screen()
        available = screen.availableGeometry()
        self.setMinimumSize(min(540, available.width() - 24), min(440, available.height() - 64))
        self.resize(min(720, available.width() - 24), min(666, available.height() - 64))

    @staticmethod
    def _header() -> QHBoxLayout:
        heading = QVBoxLayout()
        heading.setSpacing(3)
        heading.addWidget(_label("个人中心", "pageTitle"))
        heading.addWidget(_label("查看账号信息、使用额度和模型权限", "muted", wrap=True))

        brand_icon = _label("", "brandIcon")
        brand_icon.setFixedSize(30, 30)
        brand_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo = QPixmap(str(resource_root() / "assets" / "icon.png"))
        brand_icon.setPixmap(logo.scaled(
            22, 22, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addLayout(heading, 1)
        header.addWidget(brand_icon)
        header.addWidget(_label("小欢ovo", "brand"))
        return header

    def _profile_card(self, role: str) -> QFrame:
        username = str(self.user.get("username") or "未命名账号")
        display_name = str(self.user.get("display_name") or "").strip() or username
        status = str(self.user.get("status") or "active")
        status_text = {"active": "正常", "pending": "待审批", "suspended": "已停用"}.get(status, status)

        avatar = _label(display_name[:1].upper(), "avatar")
        avatar.setFixedSize(60, 60)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        identity = QVBoxLayout()
        identity.setSpacing(3)
        identity.addWidget(_label(display_name, "displayName", wrap=True))
        account = _label(f"登录账号 · {username}", "accountName", wrap=True)
        account.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        identity.addWidget(account)

        badge = _label(ROLE_LABELS.get(role, role), "roleBadge")
        state = _label(status_text, "statusBadge")
        state.setProperty("active", status == "active")

        card = QFrame()
        card.setObjectName("profileCard")
        row = QHBoxLayout(card)
        row.setContentsMargins(17, 15, 17, 15)
        row.setSpacing(12)
        row.addWidget(avatar)
        row.addLayout(identity, 1)
        row.addWidget(badge, alignment=Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(state, alignment=Qt.AlignmentFlag.AlignVCenter)
        return card

    @staticmethod
    def _section_heading(title: str, icon: str) -> QHBoxLayout:
        heading = QHBoxLayout()
        heading.setSpacing(8)
        mark = _label("", "sectionIcon")
        mark.setPixmap(workbench_icon(icon, "#b7a8ff", 17).pixmap(17, 17))
        heading.addWidget(mark)
        heading.addWidget(_label(title, "sectionTitle"))
        heading.addStretch(1)
        return heading

    def _limits_section(self, limits: dict) -> QVBoxLayout:
        metrics = [
            ("每日任务上限", self._limit_text(limits.get("daily_tasks"), " 次")),
            ("并发任务上限", self._limit_text(limits.get("concurrent_tasks"), " 个")),
            ("每日积分上限", self._limit_text(limits.get("daily_credits"), "")),
            ("每日素材上限", self._limit_text(limits.get("daily_asset_mb"), " MB")),
            ("存储空间上限", self._limit_text(limits.get("storage_mb"), " MB")),
            ("付费模型权限", "允许使用" if limits.get("allow_paid_models") else "未开放"),
        ]
        grid = QGridLayout()
        grid.setSpacing(8)
        for column in range(3):
            grid.setColumnStretch(column, 1)
        for index, (name, value) in enumerate(metrics):
            card = QFrame()
            card.setObjectName("metricCard")
            card.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            layout = QVBoxLayout(card)
            layout.setContentsMargins(12, 9, 12, 9)
            layout.setSpacing(3)
            layout.addWidget(_label(name, "metricLabel"))
            value_label = _label(value, "metricValue")
            value_label.setProperty("qualitative", value in ("未开放", "允许使用"))
            layout.addWidget(value_label)
            grid.addWidget(card, index // 3, index % 3)

        section = QVBoxLayout()
        section.setSpacing(8)
        section.addLayout(self._section_heading("账号权限与额度", "layout-grid"))
        section.addLayout(grid)
        return section

    def _models_section(self, limits: dict) -> QVBoxLayout:
        models = list(dict.fromkeys(
            model.strip() for model in (limits.get("allowed_models") or [])
            if isinstance(model, str) and model.strip()))
        heading = self._section_heading("允许使用的模型", "sparkles")
        if models:
            heading.addWidget(_label(f"{len(models)} 项授权", "count"))

        card = QFrame()
        card.setObjectName("modelCard")
        grid = QGridLayout(card)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setSpacing(7)
        if models:
            columns = 4
            for column in range(columns):
                grid.setColumnStretch(column, 1)
            for index, model in enumerate(models):
                chip = _label(MODEL_LABELS.get(model, model), "modelChip", wrap=True)
                chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
                chip.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                chip.setToolTip(model)
                grid.addWidget(chip, index // columns, index % columns)
        else:
            grid.addWidget(_label("当前可使用本地功能与 Edge TTS。", "muted", wrap=True))

        section = QVBoxLayout()
        section.setSpacing(8)
        section.addLayout(heading)
        section.addWidget(card)
        return section

    def _footer(self, role: str, policy: ReleasePolicy) -> QVBoxLayout:
        self.manage_accounts = QPushButton("管理员账号管理")
        self.manage_accounts.setObjectName("manageAccounts")
        self.manage_accounts.setIcon(workbench_icon("lock-keyhole", "#ddd1ff"))
        self.manage_accounts.setVisible(role == "admin")
        self.manage_accounts.clicked.connect(self._open_account_admin)

        self.logout_button = QPushButton("退出当前账号")
        self.logout_button.setObjectName("logoutAccount")
        self.logout_button.clicked.connect(self._confirm_logout)
        for button in (self.manage_accounts, self.logout_button):
            button.setIconSize(QSize(15, 15))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAutoDefault(False)

        self.logout_status = _label(
            "退出后返回登录页，本地项目和素材会保留。", "logoutHint", wrap=True)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(self.logout_status, 1)
        actions.addWidget(self.manage_accounts)
        actions.addWidget(self.logout_button)

        metadata = QHBoxLayout()
        metadata.setSpacing(8)
        metadata.addWidget(_label(f"桌面版本 {policy.version}", "metadata", wrap=True), 1)
        expiry = _label(f"授权有效至 {policy.expiry_label}", "metadata", wrap=True)
        expiry.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        metadata.addWidget(expiry, 1)

        divider = QFrame()
        divider.setObjectName("footerDivider")
        divider.setFixedHeight(1)
        footer = QVBoxLayout()
        footer.setSpacing(10)
        footer.addWidget(divider)
        footer.addLayout(actions)
        footer.addLayout(metadata)
        return footer

    @staticmethod
    def _limit_text(value, suffix: str) -> str:
        try:
            number = int(value or 0)
        except (TypeError, ValueError):
            number = 0
        return f"{number:,}{suffix}" if number > 0 else "未开放"

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
