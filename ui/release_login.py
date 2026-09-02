"""Desktop login dialog and periodic account/session validation."""
from __future__ import annotations

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QVBoxLayout,
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
        self._worker: _LoginWorker | None = None
        self.setWindowTitle("CreativeEnginePro 登录")
        self.setModal(True)
        self.setMinimumWidth(420)

        title = QLabel("登录 CreativeEnginePro")
        title.setStyleSheet("font-size:20px;font-weight:600;")
        note = QLabel("账号由管理员创建，不开放自行注册。")
        note.setStyleSheet("color:#8c8c96;")
        expiry = QLabel(f"本版本可使用至 {policy.expiry_label}")
        expiry.setStyleSheet("color:#d8a94c;")

        self.username = QLineEdit()
        self.username.setPlaceholderText("账号")
        self.username.setClearButtonEnabled(True)
        self.password = QLineEdit()
        self.password.setPlaceholderText("密码")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.returnPressed.connect(self._submit)
        form = QFormLayout()
        form.addRow("账号", self.username)
        form.addRow("密码", self.password)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#ff7373;")
        cancel = QPushButton("退出")
        cancel.clicked.connect(self.reject)
        self.submit = QPushButton("登录")
        self.submit.setDefault(True)
        self.submit.clicked.connect(self._submit)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self.submit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(note)
        layout.addWidget(expiry)
        layout.addLayout(form)
        layout.addWidget(self.status)
        layout.addLayout(buttons)

    def _submit(self) -> None:
        username = self.username.text().strip()
        password = self.password.text()
        if not username or not password:
            self.status.setText("请输入账号和密码。")
            return
        self.submit.setEnabled(False)
        self.status.setStyleSheet("color:#8c8c96;")
        self.status.setText("正在安全验证…")
        self._worker = _LoginWorker(self.client, username, password, self)
        self._worker.completed.connect(self._logged_in)
        self._worker.failed.connect(self._failed)
        self._worker.start()

    def _logged_in(self, result: LoginResult) -> None:
        if self.policy.is_expired(result.server_time):
            self._failed("当前版本已经到期，请联系管理员获取新版本。")
            return
        self.result = result
        self.password.clear()
        self.accept()

    def _failed(self, message: str) -> None:
        self.submit.setEnabled(True)
        self.status.setStyleSheet("color:#ff7373;")
        self.status.setText(message)
        self.password.clear()
        self.password.setFocus()


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
