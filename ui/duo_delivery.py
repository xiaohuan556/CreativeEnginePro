"""Direct submission of finished work from the desktop workbenches."""
from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import QDate, QPointF, QSettings, QThread, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import (
    QComboBox, QCompleter, QDateEdit, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from core.duo_delivery import (
    DEFAULT_DUO_URL, MEDIA_TYPES, MISSING_REQUEST, DeliveryJournal, DuoClient, DuoError, duo_url, inspect_media,
)
from utils.app_paths import output_root, user_file

log = logging.getLogger(__name__)
DELIVERY_LABEL = "交付到需求对接平台"

_FORM_STYLE = """
QDialog#requestDelivery, QDialog#requestPublication { background:#181c24; color:#eef2f9; }
QLabel { color:#e2e8f2; background:transparent; }
QLabel#deliveryTitle { color:#f4f7fc; font-size:17px; font-weight:700; }
QLabel#deliveryMuted, QLabel#deliveryStatus { color:#aab6ca; font-size:12px; }
QLabel#deliveryMedia { color:#eaf1fc; font-size:13px; font-weight:600; }
QLabel#deliveryIcon { background:#304570; color:#b9d4ff; border-radius:8px;
    font-size:19px; font-weight:700; }
QFrame#deliveryCard { background:#242b38; border:1px solid #363f51; border-radius:10px; }
QFrame#deliverySettings { background:#202632; border:1px solid #343d4e; border-radius:8px; }
QLineEdit, QComboBox, QPlainTextEdit, QDateEdit, QListWidget { background:#202632; color:#eef2fa; border:1px solid #3b4659;
    border-radius:7px; min-height:18px; padding:7px 9px; }
QComboBox QLineEdit { border:0; padding:0; background:transparent; }
QComboBox::drop-down { border:none; width:24px; }
QComboBox::down-arrow { width:0; height:0; }
QComboBox QAbstractItemView, QAbstractItemView#deliveryMatches {
    background:#242b38; color:#eef2fa; selection-background-color:#344e78;
    selection-color:white; border:1px solid #44516a; padding:4px; outline:0; }
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QDateEdit:focus { border-color:#689eff; }
QListWidget::item:selected { background:#344e78; color:white; }
QLineEdit:disabled, QComboBox:disabled { color:#aab6ca; }
QPushButton { color:#dce6f7; background:#252d3b; border:1px solid #3a465b;
    border-radius:7px; padding:7px 12px; min-height:17px; }
QPushButton:hover { background:#303c51; border-color:#536582; }
QPushButton:focus { border-color:#7faaff; }
QPushButton:disabled { color:#8996ac; background:#242b36; border-color:#303949; }
QPushButton#deliveryLink { background:transparent; border:1px solid transparent; color:#a9c7f7;
    padding:4px 5px; }
QPushButton#deliveryLink:hover, QPushButton#deliveryLink:checked { background:#293850; }
QPushButton#deliveryLink:focus { border-color:#7faaff; }
QPushButton#deliveryLink:disabled { color:#8996ac; }
QPushButton#deliverySubmit { font-weight:600; padding:7px 18px; min-width:72px; }
QPushButton#deliverySubmit:enabled { background:#387ced; border-color:#5c94f9; color:#fff; }
QPushButton#deliverySubmit:hover { background:#498afb; }
"""


class _RequestPicker(QComboBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#afc1dc" if self.isEnabled() else "#748196"))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        x, y = self.width() - 13, self.height() / 2
        painter.drawPolyline(QPolygonF([QPointF(x - 4, y - 2), QPointF(x, y + 2), QPointF(x + 4, y - 2)]))


class _DeliveryWorker(QThread):
    progress = pyqtSignal(str)
    completed = pyqtSignal(object, object)

    def __init__(self, run, parent):
        super().__init__(parent)
        self._run = run

    def run(self):
        try:
            self.completed.emit(self._run(self.progress.emit), None)
        except DuoError as error:
            self.completed.emit(None, error)
        except OSError as error:
            self.completed.emit(None, DuoError(f"本地文件操作失败：{error.strerror or error}"))
        except Exception:
            log.exception("Duo delivery operation failed")
            self.completed.emit(None, DuoError("交付失败，请检查本地文件和平台连接。"))
        finally:
            self._run = None


class _RequestPublication(QDialog):
    def __init__(self, parent, options, user):
        super().__init__(parent)
        self.setObjectName("requestPublication")
        self.setWindowTitle("发布需求")
        self.setFixedWidth(520)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.publication_id = str(uuid4())
        self.attachments = []
        self._locked = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        title = QLabel("发布需求")
        title.setObjectName("deliveryTitle")
        layout.addWidget(title)
        self.inputs = QWidget()
        grid = QGridLayout(self.inputs)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(5)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.description = QPlainTextEdit()
        self.description.setPlaceholderText("说明制作内容、尺寸、文案和其他要求")
        self.description.setAccessibleName("需求内容")
        self.description.setFixedHeight(82)
        grid.addWidget(QLabel("需求内容"), 0, 0, 1, 2)
        grid.addWidget(self.description, 1, 0, 1, 2)

        self.fields = {}
        for index, (key, label) in enumerate((
            ("product", "产品"), ("platform", "平台"), ("channel", "渠道"), ("priority", "优先级"),
            ("requester", "提需人"), ("designer", "设计师"), ("scheduleEnd", "完成时间"), ("tag", "标签（可选）"),
        )):
            row, column = 2 + 2 * (index // 2), index % 2
            if key == "scheduleEnd":
                widget = QDateEdit()
                today = QDate.fromString(options["today"], Qt.DateFormat.ISODate)
                widget.setCalendarPopup(True)
                widget.setDisplayFormat("yyyy-MM-dd")
                widget.setMinimumDate(today)
                widget.setDate(today.addDays(5))
            else:
                widget = _RequestPicker()
                widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                widget.setMinimumContentsLength(11)
                if key == "product":
                    widget.addItem("选择产品", "")
                    for product in options["products"]:
                        widget.addItem(product["name"], product["platforms"])
                elif key == "tag":
                    widget.addItem("无标签", "")
                    for tag in options["tags"]:
                        widget.addItem(tag["name"], tag["id"])
                elif key != "platform":
                    items = options[{"channel": "channels", "priority": "priorities",
                                     "requester": "requesters", "designer": "designers"}[key]]
                    if key in {"requester", "designer"} and user["name"] not in items:
                        widget.addItem(f"选择{label}", "")
                    for item in items:
                        widget.addItem(item, item)
                    if key in {"requester", "designer"} and user["name"] in items:
                        widget.setCurrentIndex(widget.findData(user["name"]))
            widget.setAccessibleName(label)
            self.fields[key] = widget
            grid.addWidget(QLabel(label), row, column)
            grid.addWidget(widget, row + 1, column)
        self.fields["product"].currentIndexChanged.connect(self._platforms)
        self._platforms()

        reference_row = QHBoxLayout()
        reference_row.addWidget(QLabel("参考附件（可选）"))
        reference_row.addStretch()
        add = QPushButton("添加文件")
        add.setObjectName("deliveryLink")
        add.clicked.connect(self._choose_files)
        reference_row.addWidget(add)
        self.remove_file = QPushButton("移除选中")
        self.remove_file.setObjectName("deliveryLink")
        self.remove_file.clicked.connect(self._remove_file)
        reference_row.addWidget(self.remove_file)
        grid.addLayout(reference_row, 10, 0, 1, 2)
        self.file_list = QListWidget()
        self.file_list.setFixedHeight(68)
        self.file_list.currentRowChanged.connect(lambda row: self.remove_file.setEnabled(row >= 0))
        grid.addWidget(self.file_list, 11, 0, 1, 2)
        self._show_files()
        layout.addWidget(self.inputs)

        self.status = QLabel()
        self.status.setObjectName("deliveryStatus")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        footer = QHBoxLayout()
        web = QPushButton("去网页发布")
        web.setObjectName("deliveryLink")
        web.clicked.connect(parent._open_duo)
        footer.addWidget(web)
        footer.addStretch()
        self.cancel = QPushButton("取消")
        self.cancel.clicked.connect(self.reject)
        footer.addWidget(self.cancel)
        self.publish = QPushButton("发布需求")
        self.publish.setObjectName("deliverySubmit")
        self.publish.clicked.connect(self._submit)
        footer.addWidget(self.publish)
        layout.addLayout(footer)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self.description.setFocus()

    def _platforms(self):
        available = self.fields["product"].currentData() or {}
        field = self.fields["platform"]
        previous = field.currentData()
        field.clear()
        for value, text in (("AND", "安卓"), ("IOS", "iOS"), ("BOTH", "双端")):
            if value in available:
                field.addItem(text, value)
        index = field.findData(previous)
        if index >= 0:
            field.setCurrentIndex(index)
        field.setEnabled(bool(available))

    def _choose_files(self):
        patterns = " ".join("*" + extension for extension in MEDIA_TYPES)
        paths, _ = QFileDialog.getOpenFileNames(self, "选择参考附件", "", f"图片和视频 ({patterns})")
        errors = []
        for path in paths:
            try:
                item = inspect_media(path)
            except DuoError as error:
                errors.append(str(error))
                continue
            if not any(saved["source"] == item["source"] for saved in self.attachments):
                self.attachments.append(item)
        self.status.setText("\n".join(errors))
        self._show_files()

    def _remove_file(self):
        if (row := self.file_list.currentRow()) >= 0:
            self.attachments.pop(row)
            self._show_files()

    def _show_files(self):
        self.file_list.clear()
        for item in self.attachments:
            self.file_list.addItem(item["original_name"])
            self.file_list.item(self.file_list.count() - 1).setToolTip(item["source"])
        self.file_list.setVisible(bool(self.attachments))
        self.remove_file.setVisible(bool(self.attachments))
        self.remove_file.setEnabled(False)
        if self.isVisible():
            self.adjustSize()

    def _submit(self):
        fields = {key: field.currentData() for key, field in self.fields.items() if key != "scheduleEnd"}
        fields["product"] = self.fields["product"].currentText() if fields["product"] else ""
        fields["scheduleEnd"] = self.fields["scheduleEnd"].date().toString(Qt.DateFormat.ISODate)
        fields["description"] = self.description.toPlainText().strip()
        if not fields["description"] or len(fields["description"].encode("utf-16-le")) // 2 > 2000:
            self.status.setText("请填写需求内容，最多 2000 字。")
            return
        if any(not value for key, value in fields.items() if key != "tag"):
            self.status.setText("请补齐产品、平台、渠道、优先级、提需人、设计师和完成时间。")
            return
        self.parent()._publish(self, fields)

    def set_busy(self, busy, *, unconfirmed=False):
        self._locked = busy
        self.inputs.setEnabled(not busy and not unconfirmed)
        self.cancel.setEnabled(not busy)
        self.publish.setEnabled(not busy)
        self.publish.setText("重试确认" if unconfirmed else "发布需求")

    def reject(self):
        if not self._locked:
            super().reject()


class DuoDeliveryDialog(QDialog):
    pendingChanged = pyqtSignal(bool)

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("requestDelivery")
        self.setWindowTitle(DELIVERY_LABEL)
        self.setFixedWidth(520)
        self.setStyleSheet(_FORM_STYLE)
        self.settings = QSettings("CreativeEnginePro", "DuoDelivery")
        self.journal = DeliveryJournal(user_file("duo_delivery_pending.json"))
        self.client = None
        self.pending = None
        self._journal_error = ""
        self._worker = None
        self._listing = []
        self._last_folder = None
        self._paths = ()
        self._source = ""
        self._completed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        heading = QHBoxLayout()
        title = QLabel(DELIVERY_LABEL)
        title.setObjectName("deliveryTitle")
        heading.addWidget(title)
        heading.addStretch()
        self.session_label = QLabel()
        self.session_label.setObjectName("deliveryMuted")
        self.session_label.setTextFormat(Qt.TextFormat.PlainText)
        heading.addWidget(self.session_label)
        self.switch_button = QPushButton("换账号")
        self.switch_button.setObjectName("deliveryLink")
        self.switch_button.clicked.connect(self._switch_account)
        heading.addWidget(self.switch_button)
        self.settings_button = QPushButton("设置")
        self.settings_button.setObjectName("deliveryLink")
        self.settings_button.setCheckable(True)
        self.settings_button.toggled.connect(self._toggle_settings)
        heading.addWidget(self.settings_button)
        layout.addLayout(heading)

        card = QFrame()
        card.setObjectName("deliveryCard")
        card_row = QHBoxLayout(card)
        card_row.setContentsMargins(11, 9, 11, 9)
        card_row.setSpacing(10)
        icon = QLabel("↑")
        icon.setObjectName("deliveryIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(34, 34)
        card_row.addWidget(icon)
        media = QVBoxLayout()
        media.setSpacing(2)
        self.media_title = QLabel()
        self.media_title.setObjectName("deliveryMedia")
        self.media_info = QLabel()
        self.media_info.setObjectName("deliveryMuted")
        self.media_info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        for label in (self.media_title, self.media_info):
            label.setTextFormat(Qt.TextFormat.PlainText)
            media.addWidget(label)
        card_row.addLayout(media, 1)
        layout.addWidget(card)

        self.login_form = QWidget()
        login_row = QHBoxLayout(self.login_form)
        login_row.setContentsMargins(0, 0, 0, 0)
        self.account = QLineEdit()
        self.account.setPlaceholderText("平台账号")
        self.password = QLineEdit()
        self.password.setPlaceholderText("密码")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.login_button = QPushButton("登录")
        self.login_button.clicked.connect(self._login)
        self.password.returnPressed.connect(self._login)
        login_row.addWidget(self.account, 1)
        login_row.addWidget(self.password, 1)
        login_row.addWidget(self.login_button)
        layout.addWidget(self.login_form)

        self.request_form = QWidget()
        request_layout = QVBoxLayout(self.request_form)
        request_layout.setContentsMargins(0, 0, 0, 0)
        request_layout.setSpacing(5)
        request_heading = QHBoxLayout()
        request_heading.addWidget(QLabel("待交付需求"))
        request_heading.addStretch()
        self.publish_button = QPushButton("＋ 发布需求")
        self.publish_button.setObjectName("deliveryLink")
        self.publish_button.clicked.connect(self._open_publication)
        request_heading.addWidget(self.publish_button)
        request_layout.addLayout(request_heading)
        request_row = QHBoxLayout()
        self.request = _RequestPicker()
        self.request.setEditable(True)
        self.request.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.request.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.request.setMinimumContentsLength(20)
        self.request.setAccessibleName("选择已发布需求")
        self.request.lineEdit().setPlaceholderText("搜索编号或名称，选择对应需求")
        completer = self.request.completer()
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.popup().setObjectName("deliveryMatches")
        completer.activated[str].connect(self._choose_request_match)
        self.request.currentIndexChanged.connect(self._request_edited)
        self.request.editTextChanged.connect(self._request_edited)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self._refresh)
        request_row.addWidget(self.request, 1)
        request_row.addWidget(self.refresh_button)
        request_layout.addLayout(request_row)
        layout.addWidget(self.request_form)

        self.advanced = QFrame()
        self.advanced.setObjectName("deliverySettings")
        options = QVBoxLayout(self.advanced)
        options.setContentsMargins(10, 9, 10, 9)
        options.setSpacing(6)
        address_row = QHBoxLayout()
        address_row.addWidget(QLabel("平台地址"))
        self.address = QLineEdit(self.settings.value("address", DEFAULT_DUO_URL, str))
        self.address.setPlaceholderText(DEFAULT_DUO_URL)
        address_row.addWidget(self.address, 1)
        options.addLayout(address_row)
        archive_row = QHBoxLayout()
        archive_row.addWidget(QLabel("本地归档"))
        self.archive = QLineEdit(self.settings.value("archive", str(output_root() / "需求交付"), str))
        self.browse_button = QPushButton("更改")
        self.browse_button.clicked.connect(self._choose_archive)
        archive_row.addWidget(self.archive, 1)
        archive_row.addWidget(self.browse_button)
        options.addLayout(archive_row)
        layout.addWidget(self.advanced)
        self.advanced.hide()

        self.status = QLabel("登录后选择已发布需求。")
        self.status.setObjectName("deliveryStatus")
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        footer = QHBoxLayout()
        self.web_button = QPushButton("打开需求网页")
        self.web_button.setObjectName("deliveryLink")
        self.web_button.clicked.connect(self._open_duo)
        footer.addWidget(self.web_button)
        self.folder_button = QPushButton("打开归档")
        self.folder_button.setObjectName("deliveryLink")
        self.folder_button.clicked.connect(self._open_archive)
        footer.addWidget(self.folder_button)
        self.release_button = QPushButton("结束重试")
        self.release_button.setObjectName("deliveryLink")
        self.release_button.clicked.connect(self._release_pending)
        footer.addWidget(self.release_button)
        footer.addStretch()
        self.close_button = QPushButton("取消")
        self.close_button.clicked.connect(self.reject)
        footer.addWidget(self.close_button)
        self.submit_button = QPushButton("保存并提交")
        self.submit_button.setObjectName("deliverySubmit")
        self.submit_button.clicked.connect(self._submit)
        footer.addWidget(self.submit_button)
        layout.addLayout(footer)
        self.address.textChanged.connect(self._address_changed)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self._load_pending()
        self._fill_requests()

    @property
    def busy(self):
        return self._worker is not None

    def _load_pending(self):
        was_pending = self.pending is not None
        try:
            self.pending = self.journal.read()
            self._journal_error = ""
        except DuoError as error:
            self.pending = None
            self._journal_error = str(error)
            self.status.setText(self._journal_error)
        if self.pending:
            self.address.setText(self.pending["address"])
            self._last_folder = Path(self.pending["folder"])
            self.status.setText(f"请先处理上一次提交 · REQ-{self.pending['request_id']}。")
        if was_pending != (self.pending is not None):
            self.pendingChanged.emit(self.pending is not None)
        self._update_media()

    def _toggle_settings(self, expanded):
        self.advanced.setVisible(expanded)
        self.adjustSize()

    def show_submission(self):
        self._update_controls()
        self.adjustSize()
        self.show()
        self.raise_()
        self.activateWindow()

    def open_submission(self, paths, source="", *, request_id=None):
        if self.busy:
            self.show_submission()
            return
        self._paths = tuple(deliverable_paths(paths))
        self._source = source
        self._preferred_request = request_id
        self._completed = False
        self.settings_button.setChecked(False)
        self._reset_request()
        self._load_pending()
        if not self.pending and not self._journal_error:
            self.status.setText("登录后选择已发布需求。" if self.client is None else "请选择对应的已发布需求。")
        self.show_submission()
        if self.login_form.isVisible():
            (self.password if self.account.text() else self.account).setFocus()
        elif self.client is not None and not self.pending and not self._journal_error:
            self._refresh()

    def _update_media(self):
        if self.pending:
            items = self.pending["items"]
            names = [item["original_name"] for item in items]
            source = f"上一次提交 · REQ-{self.pending['request_id']}"
        else:
            names = [Path(path).name for path in self._paths]
            source = self._source
        count = len(names)
        if self._completed:
            self.media_title.setText("已交付成品")
        else:
            self.media_title.setText(f"{count} 个成品" if count else "成品交付")
        details = [source] if source else []
        if count:
            details.append(names[0] + (f" 等 {count} 个文件" if count > 1 else ""))
        label = "  ·  ".join(details)
        self.media_info.setText(self.media_info.fontMetrics().elidedText(label, Qt.TextElideMode.ElideMiddle, 403))
        self.media_info.setToolTip(label)
        self.media_info.setVisible(bool(label))

    def _choose_archive(self):
        path = QFileDialog.getExistingDirectory(self, "选择本地归档目录", self.archive.text())
        if path:
            self.archive.setText(path)

    def _open_duo(self):
        try:
            address = duo_url(self.address.text())
        except DuoError as error:
            self.status.setText(str(error))
            return
        QDesktopServices.openUrl(QUrl(address + "/desk"))

    def _address_changed(self, address):
        if self.client is not None and address.strip().rstrip("/") != self.client.address:
            self._clear_session()
            self.status.setText("平台地址已修改，请重新登录。")

    def _clear_session(self):
        if self.client is not None:
            self.client.close()
            self.client = None
        self.session_label.clear()
        self._listing = []
        self._fill_requests()

    def _switch_account(self):
        if not self.busy:
            self._clear_session()
            self.status.setText("请登录需求对接平台。")
            self.account.setFocus()

    def _open_archive(self):
        if self._last_folder and self._last_folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_folder)))

    def _request_id(self):
        index = self.request.currentIndex()
        if index >= 0 and self.request.currentText() == self.request.itemText(index):
            return self.request.itemData(index)
        return None

    def _reset_request(self):
        self.request.setCurrentIndex(-1)
        self.request.clearEditText()

    def _request_edited(self, *_):
        self._update_controls()
        if self.client is not None and not self.busy and not self.pending and self._listing:
            self.status.setText("成品会同时保存到本地归档。" if self._request_id() is not None
                                else "从匹配项里选择对应的已发布需求。")

    def _choose_request_match(self, label):
        index = self.request.findText(label, Qt.MatchFlag.MatchExactly)
        if index >= 0:
            self.request.setCurrentIndex(index)
            self.request.lineEdit().setCursorPosition(0)

    def _fill_requests(self):
        previous = self._request_id()
        self.request.blockSignals(True)
        try:
            self.request.clear()
            for row in self._listing:
                description = " ".join(str(row.get("description") or row.get("product") or "需求").split())
                label = f"REQ-{row['id']} · {description}"
                self.request.addItem(label, row["id"])
                self.request.setItemData(self.request.count() - 1, label, Qt.ItemDataRole.ToolTipRole)
            preferred = getattr(self, '_preferred_request', None)
            target = previous if previous is not None else preferred
            if (index := self.request.findData(target)) >= 0:
                self.request.setCurrentIndex(index)
                self.request.lineEdit().setCursorPosition(0)
                self._preferred_request = None
            else:
                self._reset_request()
            self.request.lineEdit().setPlaceholderText(
                "搜索编号或名称，选择对应需求" if self._listing else "暂无可交付的已发布需求")
        finally:
            self.request.blockSignals(False)
        self._update_controls()

    def _update_controls(self, *_):
        free = not self.busy
        connected = self.client is not None
        received = bool(self.pending and "receipt" in self.pending)
        self.login_form.setVisible(not connected and not received and not self._completed)
        self.request_form.setVisible(connected and not self.pending and not self._completed)
        self.session_label.setVisible(connected)
        self.switch_button.setVisible(connected and not received and not self._completed)
        self.switch_button.setEnabled(free)
        self.settings_button.setEnabled(free)
        self.address.setEnabled(free and not self.pending)
        for widget in (self.account, self.password, self.login_button):
            widget.setEnabled(free)
        self.request.setEnabled(free and connected and bool(self._listing))
        for widget in (self.archive, self.browse_button):
            widget.setEnabled(free and not self.pending)
        self.refresh_button.setEnabled(free and connected)
        self.publish_button.setEnabled(free and connected)
        self.release_button.setVisible(bool(self.pending and not received))
        self.release_button.setEnabled(free)
        self.folder_button.setVisible(bool((self.pending or self._completed) and self._last_folder
                                           and self._last_folder.is_dir()))
        self.close_button.setEnabled(free)
        self.close_button.setVisible(not self._completed)
        self.submit_button.setText("完成" if self._completed else "继续归档" if received
                                   else "继续提交" if self.pending else "保存并提交")
        can_submit = ((connected or received) if self.pending else
                      (connected and self._request_id() is not None and bool(self._paths)))
        self.submit_button.setEnabled(free and (self._completed or (not self._journal_error and can_submit)))

    def reject(self):
        if not self.busy:
            super().reject()

    def _run(self, run, done, progress=None):
        if self.busy:
            return
        worker = _DeliveryWorker(run, self)
        self._worker = worker
        worker.progress.connect(progress if progress is not None else self.status.setText)
        worker.completed.connect(done)
        worker.finished.connect(self._worker_finished)
        worker.finished.connect(worker.deleteLater)
        self._update_controls()
        worker.start()

    def _worker_finished(self):
        self._worker = None
        self._update_controls()
        self.adjustSize()

    def _login(self):
        account, password, address = self.account.text().strip(), self.password.text(), self.address.text()
        if not account or not password:
            self.status.setText("请输入平台账号和密码。")
            return
        self.password.clear()
        self.status.setText("正在登录需求对接平台…")

        def run(_):
            client = DuoClient(address)
            try:
                return client, client.login(account, password)
            except Exception:
                client.close()
                raise

        def done(result, error):
            if error:
                self.status.setText(str(error))
                return
            if self.client:
                self.client.close()
            self.client, listing = result
            self.address.setText(self.client.address)
            self.settings.setValue("address", self.client.address)
            self._reset_request()
            self._set_listing(listing)

        self._run(run, done)

    def _refresh(self):
        client = self.client
        if client is not None and not self.busy:
            self.status.setText("正在读取已发布需求…")
            self._run(lambda _: client.requests(), self._refresh_done)

    def _refresh_done(self, listing, error):
        if error:
            self._handle_error(error)
            return
        self._set_listing(listing)

    def _set_listing(self, listing):
        self._listing = [row for row in listing["requests"] if not row.get("fileCount")]
        name = str(listing["user"]["name"])
        self.session_label.setText(self.session_label.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, 94))
        self.session_label.setToolTip(name)
        self._fill_requests()
        if self.pending:
            self.status.setText(f"请先处理上一次提交 · REQ-{self.pending['request_id']}。")
        elif not self._listing:
            self.status.setText("暂无可交付的需求。可以在这里发布需求，或打开需求网页发布。")
        else:
            self.status.setText("成品会同时保存到本地归档。" if self._request_id() is not None
                                else "请选择对应的已发布需求。")

    def _handle_error(self, error):
        if error.status == 401 and self.client:
            self._clear_session()
        self.status.setText(str(error))

    def _open_publication(self):
        client = self.client
        if self.busy or client is None or self.pending:
            return
        self.status.setText("正在读取发布选项…")

        def ready(options, error):
            if error:
                self._handle_error(error)
                return
            self.status.setText("请选择待交付需求。" if self._listing else "暂无可交付的需求。")
            _RequestPublication(self, options, client.user).open()

        self._run(lambda _: client.publication_form(), ready)

    def _publish(self, dialog, fields):
        client = self.client
        if self.busy or client is None:
            return
        dialog.set_busy(True)
        dialog.status.setText("正在准备发布…")

        def run(progress):
            request_id = client.publish_request(dialog.publication_id, fields, dialog.attachments, progress)
            try:
                return request_id, client.requests(), None
            except DuoError as error:
                return request_id, None, error

        def done(result, error):
            if error:
                dialog.set_busy(False, unconfirmed=error.code == "publication_unconfirmed")
                if error.status == 401:
                    dialog.accept()
                    self._handle_error(error)
                else:
                    dialog.status.setText(str(error))
                return
            request_id, listing, refresh_error = result
            dialog.accept()
            if refresh_error:
                self._handle_error(refresh_error)
                self.status.setText(f"已发布 REQ-{request_id}。{refresh_error}")
                return
            self._set_listing(listing)
            index = self.request.findData(request_id)
            if index >= 0:
                self.request.setCurrentIndex(index)
                self.request.lineEdit().setCursorPosition(0)
                self.status.setText(f"已发布并选中 REQ-{request_id}，可以保存并提交成品。")
            else:
                self.status.setText(f"已发布 REQ-{request_id}，可由获分配的设计师交付。")

        self._run(run, done, dialog.status.setText)

    def _submit(self):
        if self.busy:
            return
        if self._completed:
            self.accept()
            return
        batch, client = self.pending, self.client
        request_id = self._request_id()
        archive = self.archive.text().strip()
        paths = self._paths
        if batch is None and (client is None or request_id is None):
            self.status.setText(MISSING_REQUEST)
            return
        if batch is None:
            self.settings.setValue("archive", archive)
        previous_paths = tuple(item["source"] for item in batch["items"]) if batch else paths
        self.status.setText("正在核对需求…")

        def run(progress):
            selected = batch or self.journal.start(client, request_id, paths, archive)
            return self.journal.resume(selected, client, progress)

        target = batch["request_id"] if batch else request_id
        self._run(run, lambda result, error: self._submit_done(result, error, previous_paths, target))

    def _submit_done(self, result, error, submitted_paths, target):
        self._load_pending()
        if error:
            self._handle_error(error)
            if (error.code == "already_delivered" or error.status == 404) and self.client and not self.pending:
                self._listing = [row for row in self._listing if row["id"] != target]
                self._fill_requests()
            return
        receipt, self._last_folder = result
        self._listing = [row for row in self._listing if row["id"] != receipt["requestId"]]
        self._fill_requests()
        self._completed = not self._paths or self._paths == submitted_paths
        if self._completed:
            self.status.setText(f"已提交 REQ-{receipt['requestId']} · {len(receipt['files'])} 个成品\n"
                                f"已归档到 {self._last_folder}")
            self.settings_button.setChecked(False)
        else:
            self._reset_request()
            self.status.setText(f"上一次提交已完成 · REQ-{receipt['requestId']}。请选择本次的需求。")
        self._update_media()

    def _release_pending(self):
        if self.busy or not self.pending or "receipt" in self.pending:
            return
        answer = QMessageBox.question(self, "结束重试", "本地成品会保留。若上次提交时连接中断，请先在需求对接平台核对是否已收到。结束重试？")
        if answer == QMessageBox.StandardButton.Yes:
            try:
                self.journal.path.unlink(missing_ok=True)
            except OSError as error:
                self.status.setText(f"无法清除待提交记录：{error.strerror}")
                return
            self._load_pending()
            self._update_controls()
            self.status.setText("已结束本批重试，本地成品仍在归档目录。")


def deliverable_paths(paths):
    return list(dict.fromkeys(str(Path(value).resolve()) for value in paths if value
                              and Path(value).suffix.lower() in MEDIA_TYPES and Path(value).is_file()))


def offer_duo_export(owner, paths, source, title, message, *, open_folder=None):
    """Keep the existing completion prompt, with one extra delivery action."""
    delivery = getattr(owner.window(), "duo_delivery", None)
    files = deliverable_paths(paths) if delivery is not None else []
    if not files:
        if open_folder is None:
            QMessageBox.information(owner, title, message)
            return False
        return QMessageBox.question(owner, title, f"{message}\n\n打开输出目录？") == QMessageBox.StandardButton.Yes
    box = QMessageBox(QMessageBox.Icon.Information, title, message, parent=owner)
    send = box.addButton(DELIVERY_LABEL, QMessageBox.ButtonRole.ActionRole)
    folder = box.addButton("打开输出目录", QMessageBox.ButtonRole.ActionRole) if open_folder else None
    box.addButton(QMessageBox.StandardButton.Ok)
    box.exec()
    clicked = box.clickedButton()
    if clicked is send:
        delivery.open_submission(files, source)
    return folder is not None and clicked is folder
