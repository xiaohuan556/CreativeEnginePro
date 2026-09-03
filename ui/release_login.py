"""Desktop login dialog and periodic account/session validation."""
from __future__ import annotations

import urllib.parse

from PyQt6.QtCore import QObject, QSettings, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
)

from core.release_gate import (
    AuthenticationError, AuthenticationUnavailable, DesktopAuthClient,
    LoginResult, ReleasePolicy,
)


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
        self.setWindowTitle("CreativeEnginePro 登录")
        self.setModal(True)
        self.setFixedSize(520, 620)
        self.setObjectName("releaseLogin")
        self.setStyleSheet("""
            QDialog#releaseLogin {
                background:#1e1e1e; color:#cccccc;
                font-family:'Segoe UI','Microsoft YaHei',sans-serif;
            }
            QFrame#loginCard {
                background:#202020; border:1px solid #383838;
                border-radius:5px;
            }
            QFrame#accentLine { background:#3d8ef8; border:0; border-radius:1px; }
            QLabel#brandName { color:#eeeeee; font-size:15px; font-weight:700; }
            QLabel#brandCaption { color:#777777; font-size:10px; }
            QLabel#teamBadge {
                color:#79b7ff; background:#1c2938; border:1px solid #315678;
                border-radius:3px; padding:5px 9px; font-size:10px;
            }
            QLabel#loginTitle { color:#f2f2f2; font-size:21px; font-weight:700; }
            QLabel#loginSubtitle { color:#888888; font-size:11px; }
            QLabel#fieldLabel { color:#b7b7b7; font-size:11px; font-weight:600; }
            QLineEdit {
                min-height:40px; padding:0 11px; color:#eeeeee;
                background:#181818; border:1px solid #444444;
                border-radius:3px; font-size:12px;
            }
            QLineEdit:focus { border:1px solid #3d8ef8; background:#1b1b1b; }
            QLineEdit:disabled { color:#777777; background:#222222; }
            QPushButton#passwordToggle {
                min-width:52px; min-height:40px; color:#bcbcbc;
                background:#2d2d2d; border:1px solid #444444;
                border-radius:3px; font-size:11px;
            }
            QPushButton#passwordToggle:hover { color:#fff; background:#383838; border-color:#666666; }
            QCheckBox { color:#a8a8a8; spacing:7px; font-size:11px; }
            QLabel#serverState {
                color:#75c997; background:#18251f; border:1px solid #28523a;
                border-radius:3px; padding:7px 10px; font-size:10px;
            }
            QLabel#loginStatus { color:#e47777; font-size:11px; }
            QPushButton#loginPrimary {
                min-height:42px; color:#fff; border:0; border-radius:4px;
                background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #3d8ef8,stop:1 #1c6ed8);
                font-size:13px; font-weight:700;
            }
            QPushButton#loginPrimary:hover { background:#4a9df9; }
            QPushButton#loginPrimary:pressed { background:#0b5ed7; }
            QPushButton#loginPrimary:disabled { color:#8d8d8d; background:#353535; }
            QPushButton#adminManage {
                min-height:38px; color:#bdbdbd; border:1px solid #444444;
                border-radius:3px; background:#2d2d2d; font-size:11px;
            }
            QPushButton#adminManage:hover { color:#fff; border-color:#3d8ef8; background:#343434; }
            QPushButton#adminManage:disabled { color:#666666; border-color:#333333; background:#252525; }
            QPushButton#loginExit {
                min-height:32px; color:#777777; border:0; background:transparent;
                font-size:11px;
            }
            QPushButton#loginExit:hover { color:#cccccc; }
            QLabel#policyText { color:#696969; font-size:9px; }
        """)

        brand = QHBoxLayout()
        brand.setSpacing(11)
        logo = QLabel()
        logo.setFixedSize(42, 42)
        logo.setPixmap(QApplication.windowIcon().pixmap(42, 42))
        brand_text = QVBoxLayout()
        brand_text.setSpacing(2)
        brand_name = QLabel("CreativeEnginePro")
        brand_name.setObjectName("brandName")
        brand_caption = QLabel("AI 制片工作台 · Desktop")
        brand_caption.setObjectName("brandCaption")
        brand_text.addWidget(brand_name)
        brand_text.addWidget(brand_caption)
        team_badge = QLabel("桌面授权")
        team_badge.setObjectName("teamBadge")
        brand.addWidget(logo)
        brand.addLayout(brand_text)
        brand.addStretch(1)
        brand.addWidget(team_badge)

        title = QLabel("桌面端授权登录")
        title.setObjectName("loginTitle")
        note = QLabel("登录后即可进入 AI 制片画布，账号和模型权限由管理员统一分配。")
        note.setWordWrap(True)
        note.setObjectName("loginSubtitle")

        self.username = QLineEdit()
        self.username.setPlaceholderText("请输入管理员分配的账号")
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
        password_row = QHBoxLayout()
        password_row.setSpacing(8)
        password_row.addWidget(self.password, 1)
        self.password_toggle = QPushButton("显示")
        self.password_toggle.setObjectName("passwordToggle")
        self.password_toggle.setCheckable(True)
        self.password_toggle.clicked.connect(self._toggle_password)
        password_row.addWidget(self.password_toggle)

        self.remember = QCheckBox("在此电脑保持登录")
        self.remember.setToolTip(
            "只保存由 Windows 当前用户加密的登录会话，不保存账号密码；管理员可随时使会话失效")
        self.settings = QSettings("CreativeEnginePro", "Desktop")
        remembered = str(self.settings.value("login/username", "") or "")
        if remembered:
            self.username.setText(remembered)
            self.remember.setChecked(True)

        host = urllib.parse.urlparse(policy.auth_base_url).hostname or "授权服务器"
        server_state = QLabel(f"●  已连接团队授权服务  ·  {host}")
        server_state.setObjectName("serverState")

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(18)
        self.status.setObjectName("loginStatus")
        cancel = QPushButton("退出应用")
        cancel.setObjectName("loginExit")
        cancel.clicked.connect(self.reject)
        self.submit = QPushButton("安全登录")
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

        policy_text = QLabel(
            f"版本 {policy.version}  ·  可使用至 {policy.expiry_label}\n"
            "登录、模型调用和额度变化会记录在管理员审计日志中。")
        policy_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        policy_text.setObjectName("policyText")

        card = QFrame()
        card.setObjectName("loginCard")
        accent_line = QFrame()
        accent_line.setObjectName("accentLine")
        accent_line.setFixedHeight(3)
        content = QVBoxLayout(card)
        content.setContentsMargins(28, 20, 28, 20)
        content.setSpacing(9)
        content.addWidget(accent_line)
        content.addSpacing(3)
        content.addLayout(brand)
        content.addSpacing(10)
        content.addWidget(title)
        content.addWidget(note)
        content.addSpacing(5)
        content.addWidget(server_state)
        content.addSpacing(3)
        content.addWidget(username_label)
        content.addWidget(self.username)
        content.addWidget(password_label)
        content.addLayout(password_row)
        content.addWidget(self.remember)
        content.addWidget(self.status)
        content.addWidget(self.submit)
        content.addWidget(self.admin_manage)
        content.addWidget(cancel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 13)
        layout.setSpacing(10)
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
        self.submit.setText("安全登录")
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
