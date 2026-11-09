"""Desktop login dialog and periodic account/session validation."""
from __future__ import annotations

import urllib.parse
from datetime import timedelta

from PyQt6.QtCore import QObject, QSettings, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
)

from core.release_gate import (
    AuthenticationError, AuthenticationUnavailable, DesktopAuthClient,
    LoginResult, ReleasePolicy,
)
from ui.widgets import CheckMarkBox
from utils.app_paths import resource_root


class _LoginWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, client: DesktopAuthClient, username: str, password: str, parent=None):
        super().__init__(parent)
        self.client = client
        self.username = username
        self.password = password

    def run(self) -> None:
        try:
            self.completed.emit(self.client.login(self.username, self.password))
        except Exception as error:
            self.failed.emit(str(error))


class ReleaseLoginDialog(QDialog):
    def __init__(self, policy: ReleasePolicy, client: DesktopAuthClient, parent=None):
        super().__init__(parent)
        self.policy = policy
        self.client = client
        self.result: LoginResult | None = None
        self._worker: QThread | None = None
        self._login_intent = "app"
        self.setWindowTitle("小欢ovo · 登录")
        self.setModal(True)
        self.setFixedSize(560, 710)
        self.setObjectName("releaseLogin")
        self.setStyleSheet("""
            QDialog#releaseLogin {
                background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #0b1220,stop:1 #111a2b);
                color:#dce4f3;
                font-family:'Segoe UI','Noto Sans SC',sans-serif;
            }
            QFrame#loginCard {
                background:#141d2c; border:1px solid #27364b;
                border-radius:18px;
            }
            QLabel#brandLogo {
                background:#f5f6fb; border:1px solid #e0e4ef;
                border-radius:14px; padding:5px;
            }
            QLabel#brandName { color:#f6f8ff; font-size:18px; font-weight:700; }
            QLabel#brandCaption { color:#8e9ab0; font-size:10px; }
            QLabel#teamBadge {
                color:#c1baff; background:#242346; border:1px solid #474276;
                border-radius:10px; padding:7px 12px; font-size:10px; font-weight:600;
            }
            QLabel#loginTitle { color:#f5f7fd; font-size:26px; font-weight:700; }
            QLabel#loginSubtitle { color:#97a4ba; font-size:12px; }
            QLabel#fieldLabel { color:#d3dced; font-size:11px; font-weight:600; }
            QLabel#fieldHint { color:#8290a7; font-size:10px; }
            QLineEdit {
                min-height:46px; padding:0 14px; color:#f1f4fb;
                background:#0f1725; border:1px solid #2c3b50;
                border-radius:10px; font-size:12px;
            }
            QLineEdit:focus { border:1px solid #8b80ff; background:#111b2b; }
            QLineEdit:disabled { color:#77849a; background:#151e2c; }
            QPushButton#passwordToggle {
                min-width:64px; min-height:46px; color:#c0c9d9;
                background:#1a2637; border:1px solid #304157;
                border-radius:10px; font-size:11px;
            }
            QPushButton#passwordToggle:hover { color:#fff; background:#25354a; border-color:#596985; }
            QCheckBox { color:#b7c2d4; spacing:8px; font-size:11px; }
            QLabel#serverState {
                color:#b6c3d6; background:#101a29; border:1px solid #293a50;
                border-radius:10px; padding:11px 13px; font-size:10px;
            }
            QLabel#loginStatus { color:#ff9a9e; font-size:11px; }
            QPushButton#loginPrimary {
                min-height:50px; color:#fff; border:0; border-radius:11px;
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #7569f6,stop:1 #5b63df);
                font-size:13px; font-weight:700;
            }
            QPushButton#loginPrimary:hover {
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #8479ff,stop:1 #6b73ed);
            }
            QPushButton#loginPrimary:pressed { background:#514fc0; }
            QPushButton#loginPrimary:disabled { color:#9399b1; background:#343750; }
            QPushButton#adminManage {
                min-height:40px; color:#c5cddd; border:1px solid #35445a;
                border-radius:10px; background:#1a2637; font-size:11px;
            }
            QPushButton#adminManage:hover { color:#fff; border-color:#8178ef; background:#222c43; }
            QPushButton#adminManage:disabled { color:#6f7b8e; border-color:#293649; background:#182332; }
            QPushButton#loginExit {
                min-height:30px; color:#8190a7; border:0; background:transparent;
                font-size:11px;
            }
            QPushButton#loginExit:hover { color:#d3dced; }
            QLabel#policyText { color:#8592a9; font-size:10px; }
        """)

        brand = QHBoxLayout()
        brand.setSpacing(11)
        logo = QLabel()
        logo.setObjectName("brandLogo")
        logo.setFixedSize(44, 44)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_pixmap = QPixmap(str(resource_root() / "assets" / "icon.png"))
        logo.setPixmap(logo_pixmap.scaled(
            32, 32, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        brand_text = QVBoxLayout()
        brand_text.setSpacing(2)
        brand_name = QLabel("小欢ovo")
        brand_name.setObjectName("brandName")
        brand_caption = QLabel("AI 创作工作台")
        brand_caption.setObjectName("brandCaption")
        brand_text.addWidget(brand_name)
        brand_text.addWidget(brand_caption)
        team_badge = QLabel("体验版" if policy.channel == "demo" else "桌面版")
        team_badge.setObjectName("teamBadge")
        brand.addWidget(logo)
        brand.addLayout(brand_text)
        brand.addStretch(1)
        brand.addWidget(team_badge)

        title = QLabel("欢迎回来")
        title.setObjectName("loginTitle")
        note = QLabel("登录小欢ovo，继续你的创作。")
        note.setWordWrap(True)
        note.setObjectName("loginSubtitle")

        self.username = QLineEdit()
        self.username.setPlaceholderText("请输入账号")
        self.username.setClearButtonEnabled(True)
        self.username.setAccessibleName("登录账号")
        self.password = QLineEdit()
        self.password.setPlaceholderText("请输入登录密码")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setAccessibleName("登录密码")
        self.password.returnPressed.connect(self._submit)
        self.username.returnPressed.connect(self.password.setFocus)

        username_label = QLabel("账号")
        username_label.setObjectName("fieldLabel")
        password_label = QLabel("密码")
        password_label.setObjectName("fieldLabel")
        field_hint = QLabel("账号由管理员分配 · 忘记密码请联系管理员重置")
        field_hint.setObjectName("fieldHint")
        password_row = QHBoxLayout()
        password_row.setSpacing(8)
        password_row.addWidget(self.password, 1)
        self.password_toggle = QPushButton("显示")
        self.password_toggle.setObjectName("passwordToggle")
        self.password_toggle.setCheckable(True)
        self.password_toggle.clicked.connect(self._toggle_password)
        password_row.addWidget(self.password_toggle)

        self.remember = CheckMarkBox("在此电脑保持登录")
        self.remember.setToolTip(
            "只保存由 Windows 当前用户加密的登录会话，不保存账号密码；管理员可随时使会话失效")
        self.settings = QSettings("CreativeEnginePro", "Desktop")
        remembered = str(self.settings.value("login/username", "") or "")
        if remembered:
            self.username.setText(remembered)
            self.remember.setChecked(True)

        host = urllib.parse.urlparse(policy.auth_base_url).hostname or "授权服务器"
        server_state = QLabel(f"登录服务  ·  {host}")
        server_state.setObjectName("serverState")

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(18)
        self.status.setObjectName("loginStatus")
        cancel = QPushButton("退出应用")
        cancel.setObjectName("loginExit")
        cancel.clicked.connect(self.reject)
        self.submit = QPushButton("登录并继续")
        self.submit.setObjectName("loginPrimary")
        self.submit.setDefault(True)
        self.submit.clicked.connect(self._submit)
        self.admin_manage = QPushButton("管理员账号管理")
        self.admin_manage.setObjectName("adminManage")
        self.admin_manage.setToolTip("登录管理员账号后，添加、编辑或删除桌面端登录账号")
        self.admin_manage.clicked.connect(self._open_admin)
        self.admin_manage.setVisible(False)
        self._admin_shortcut = QShortcut(QKeySequence("Ctrl+Shift+F12"), self)
        self._admin_shortcut.activated.connect(self._toggle_admin_entry)

        if policy.channel == "demo":
            last_valid_day = (
                policy.expires_at.astimezone() - timedelta(microseconds=1)
            ).strftime("%Y年%m月%d日")
            authorization_label = f"体验授权  ·  可使用至 {last_valid_day}"
        else:
            authorization_label = f"桌面授权  ·  有效至 {policy.expiry_label}"
        policy_text = QLabel(
            f"{authorization_label}\n账号及功能权限由管理员统一管理。")
        policy_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        policy_text.setObjectName("policyText")

        card = QFrame()
        card.setObjectName("loginCard")
        content = QVBoxLayout(card)
        content.setContentsMargins(32, 28, 32, 24)
        content.setSpacing(10)
        content.addLayout(brand)
        content.addSpacing(12)
        content.addWidget(title)
        content.addWidget(note)
        content.addSpacing(5)
        content.addWidget(server_state)
        content.addSpacing(3)
        content.addWidget(username_label)
        content.addWidget(self.username)
        content.addWidget(password_label)
        content.addLayout(password_row)
        content.addWidget(field_hint)
        content.addWidget(self.remember)
        content.addWidget(self.status)
        content.addWidget(self.submit)
        content.addWidget(self.admin_manage)
        content.addWidget(cancel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 14)
        layout.setSpacing(12)
        layout.addWidget(card, 1)
        layout.addWidget(policy_text)

        restored = self.client.restore_saved_session()
        if restored:
            self.remember.setChecked(True)

        if remembered and not restored:
            self.password.setFocus()
        elif restored:
            self.status.setStyleSheet("color:#aaa8b5;")
            self.status.setText("正在恢复此电脑的登录…")
            QTimer.singleShot(0, self._begin_restore)
        else:
            self.username.setFocus()

    def _toggle_password(self, checked: bool) -> None:
        self.password.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)
        self.password_toggle.setText("隐藏" if checked else "显示")

    def _submit(self) -> None:
        self._begin_login("app")

    def _open_admin(self) -> None:
        self._begin_login("admin")

    def _toggle_admin_entry(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.admin_manage.setVisible(not self.admin_manage.isVisible())

    def _begin_restore(self) -> None:
        self.submit.setEnabled(False)
        self.admin_manage.setEnabled(False)
        self.username.setEnabled(False)
        self.password.setEnabled(False)
        self.password_toggle.setEnabled(False)
        self.submit.setText("正在恢复登录…")
        self.status.setStyleSheet("color:#aaa8b5;")
        self.status.setText("正在向授权服务器确认账号仍然有效…")
        self._worker = _SessionWorker(self.client, self)
        self._worker.completed.connect(self._restored_session)
        self._worker.failed.connect(self._restore_failed)
        self._worker.start()

    def _restored_session(self, result: LoginResult) -> None:
        if self.policy.is_expired(result.server_time):
            self.client.forget_saved_session()
            self._failed("当前版本已经到期，请联系管理员获取新版本。")
            return
        self.result = result
        username = str(result.user.get("username") or "")
        if username:
            self.settings.setValue("login/username", username)
        self.accept()

    def _restore_failed(self, error: Exception) -> None:
        if isinstance(error, AuthenticationError):
            self.client.forget_saved_session()
            message = "已保存的登录已失效，请重新输入密码。"
        else:
            message = f"暂时无法恢复登录，请输入密码重试：{error}"
        self._restore_controls()
        self.status.setStyleSheet("color:#ff7373;")
        self.status.setText(message)
        self.password.setFocus()

    def _begin_login(self, intent: str) -> None:
        username = self.username.text().strip()
        password = self.password.text()
        if not username or not password:
            self.status.setText("请输入账号和密码。")
            return
        self._login_intent = intent
        self.submit.setEnabled(False)
        self.admin_manage.setEnabled(False)
        self.username.setEnabled(False)
        self.password.setEnabled(False)
        self.password_toggle.setEnabled(False)
        if intent == "admin":
            self.admin_manage.setText("正在验证管理员…")
        else:
            self.submit.setText("正在验证账号…")
        self.status.setStyleSheet("color:#aaa8b5;")
        self.status.setText("正在安全验证…")
        self._worker = _LoginWorker(self.client, username, password, self)
        self._worker.completed.connect(self._logged_in)
        self._worker.failed.connect(self._failed)
        self._worker.start()

    def _logged_in(self, result: LoginResult) -> None:
        if self.policy.is_expired(result.server_time):
            self._failed("当前版本已经到期，请联系管理员获取新版本。")
            return
        if self._login_intent == "admin":
            if str(result.user.get("role") or "") != "admin":
                self._failed("只有管理员账号可以进入账号管理。")
                return
            self.password.clear()
            from ui.desktop_account_admin import DesktopAccountAdminDialog
            DesktopAccountAdminDialog(self.client, self).exec()
            self._restore_controls()
            self.status.setStyleSheet("color:#9ccdb4;")
            self.status.setText("账号管理已关闭，可继续登录桌面端。")
            self.password.setFocus()
            return
        self.result = result
        username = str(result.user.get("username") or self.username.text().strip())
        if self.remember.isChecked():
            self.settings.setValue("login/username", username)
            self.client.save_session()
        else:
            self.settings.remove("login/username")
            self.client.forget_saved_session()
        self.password.clear()
        self.accept()

    def _failed(self, message: str) -> None:
        self._restore_controls()
        self.status.setStyleSheet("color:#ff7373;")
        self.status.setText(message)
        self.password.clear()
        self.password.setFocus()

    def _restore_controls(self) -> None:
        self.submit.setEnabled(True)
        self.admin_manage.setEnabled(True)
        self.username.setEnabled(True)
        self.password.setEnabled(True)
        self.password_toggle.setEnabled(True)
        self.submit.setText("登录并继续")
        self.admin_manage.setText("管理员账号管理")
        self._login_intent = "app"


class _SessionWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(object)

    def __init__(self, client: DesktopAuthClient, parent=None):
        super().__init__(parent)
        self.client = client

    def run(self) -> None:
        try:
            self.completed.emit(self.client.validate_session())
        except Exception as error:
            self.failed.emit(error)


class DesktopLogoutWorker(QThread):
    """Revoke an account session without blocking the desktop UI."""

    completed = pyqtSignal(str)

    def __init__(self, client: DesktopAuthClient, parent=None):
        super().__init__(parent)
        self.client = client

    def run(self) -> None:
        try:
            self.completed.emit(self.client.logout())
        except Exception as error:
            # ``DesktopAuthClient.logout`` already clears local state in a
            # finally block.  Keep the transition usable if an unexpected
            # transport/runtime error still reaches this boundary.
            self.client.forget_saved_session()
            self.completed.emit(f"本机登录记录已清除；服务器注销异常：{error}")


class DesktopSessionGuard(QObject):
    """Re-check revoked/expired accounts while the desktop app stays open."""
    invalidated = pyqtSignal(str)

    def __init__(self, policy: ReleasePolicy, client: DesktopAuthClient, parent=None):
        super().__init__(parent)
        self.policy = policy
        self.client = client
        self._network_failures = 0
        self._worker: _SessionWorker | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(policy.session_check_minutes * 60 * 1000)
        self._timer.timeout.connect(self._check)
        self._timer.start()

    def _check(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._worker = _SessionWorker(self.client, self)
        self._worker.completed.connect(self._valid)
        self._worker.failed.connect(self._failed)
        self._worker.start()

    def _valid(self, result: LoginResult) -> None:
        self._network_failures = 0
        if self.policy.is_expired(result.server_time):
            self.invalidated.emit("当前版本已经到期，请联系管理员获取新版本。")

    def _failed(self, error: object) -> None:
        if isinstance(error, AuthenticationError):
            self.invalidated.emit(str(error) or "账号已停用或登录已失效。")
            return
        if isinstance(error, AuthenticationUnavailable):
            self._network_failures += 1
            if self._network_failures >= 3:
                self.invalidated.emit("登录服务器连续三次无法连接，应用将退出。")


def close_for_invalid_session(message: str) -> None:
    QMessageBox.critical(None, "授权已失效", message)
    QApplication.instance().quit()
