"""
AI 资源中心 — 人物 / 场景 / Prompt / 声音模型 + SQLite 存储。

解决角色一致性和重复创作问题：
- 人物：统一管理 Prompt + 参考图 + Embedding
- 场景：统一管理背景 Prompt + 参考图
- Prompt：复用模板，参数化替换
- 声音：克隆声音管理
"""

from __future__ import annotations

import os
import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ──────────────────────────────────────────────
# 数据模型
# ──────────────────────────────────────────────

@dataclass
class Character:
    """AI 角色资产；可以是人类、动物、怪物、机器人或拟人物体。"""
    id: str = ""
    name: str = ""                          # "小明"
    entity_type: str = "human"             # human / animal / monster / robot / object
    age: int = 0                              # 旧数据兼容；新界面使用 life_stage
    life_stage: str = ""                    # 可选：幼年 / 成年 / 古老 / 不适用
    gender: str = ""                        # "male" / "female"
    description: str = ""                   # 自然语言描述
    design_notes: str = ""                  # 不可漂移的轮廓、材质、配色、标志特征
    seedream_prompt: str = ""              # Seedream 专用 Prompt
    veo_prompt: str = ""                   # Veo 专用 Prompt（图生视频）
    reference_images: list[str] = field(default_factory=list)   # 参考图路径
    reference_views: dict[str, str] = field(default_factory=dict)  # front/side/back/three_quarter
    approved_reference: str = ""             # 分镜实际使用的已批准母版
    approval_status: str = "draft"           # draft / approved
    version: int = 0                          # 每次更换批准母版递增
    version_history: list[dict] = field(default_factory=list)
    outfit_states: dict[str, dict] = field(default_factory=dict)  # 场次服装状态
    embedding_path: str = ""                # IP-Adapter / LoRA 文件路径
    tags: list[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "age": self.age, "gender": self.gender,
            "entity_type": self.entity_type, "life_stage": self.life_stage,
            "description": self.description, "design_notes": self.design_notes,
            "seedream_prompt": self.seedream_prompt, "veo_prompt": self.veo_prompt,
            "reference_images": self.reference_images, "reference_views": self.reference_views,
            "approved_reference": self.approved_reference,
            "approval_status": self.approval_status, "version": self.version,
            "version_history": self.version_history, "outfit_states": self.outfit_states,
            "embedding_path": self.embedding_path,
            "tags": self.tags, "created_at": self.created_at, "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Character:
        values = {}
        for key, info in cls.__dataclass_fields__.items():
            if key in d:
                values[key] = d[key]
            elif info.default_factory is not __import__("dataclasses").MISSING:
                values[key] = info.default_factory()
            elif info.default is not __import__("dataclasses").MISSING:
                values[key] = info.default
        return _migrate_legacy_approval(
            cls(**values), legacy="approval_status" not in d)


@dataclass
class Scene:
    """AI 场景。"""
    id: str = ""
    name: str = ""
    description: str = ""
    seedream_prompt: str = ""
    reference_images: list[str] = field(default_factory=list)
    reference_views: dict[str, str] = field(default_factory=dict)  # master/empty_plate/camera_a/...
    lighting_states: dict[str, str] = field(default_factory=dict)  # day/night 等母版
    approved_reference: str = ""
    approval_status: str = "draft"
    version: int = 0
    version_history: list[dict] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, d: dict) -> Scene:
        values = {}
        for key, info in cls.__dataclass_fields__.items():
            if key in d:
                values[key] = d[key]
            elif info.default_factory is not __import__("dataclasses").MISSING:
                values[key] = info.default_factory()
            elif info.default is not __import__("dataclasses").MISSING:
                values[key] = info.default
        return _migrate_legacy_approval(
            cls(**values), legacy="approval_status" not in d)


@dataclass
class Element:
    """必须出现在画面中的元素：壁纸、Logo、UI、包装、产品或普通道具。"""
    id: str = ""
    name: str = ""
    element_type: str = "wallpaper"          # wallpaper/logo/ui/product/prop/sticker/other
    description: str = ""
    seedream_prompt: str = ""                 # AI 生成/图生图使用的固定 Prompt
    master_image: str = ""                   # 精确植入使用的原始母版
    reference_images: list[str] = field(default_factory=list)
    approved_reference: str = ""
    approval_status: str = "draft"
    version: int = 0
    version_history: list[dict] = field(default_factory=list)
    mask_path: str = ""
    placement_hint: str = ""                 # 例如“手机屏幕区域”“桌面中央”
    default_mode: str = "exact"              # exact / reference
    tags: list[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, d: dict) -> Element:
        values = {}
        for key, info in cls.__dataclass_fields__.items():
            if key in d:
                values[key] = d[key]
            elif info.default_factory is not __import__("dataclasses").MISSING:
                values[key] = info.default_factory()
            elif info.default is not __import__("dataclasses").MISSING:
                values[key] = info.default
        return _migrate_legacy_approval(
            cls(**values), legacy="approval_status" not in d)


def _legacy_master_path(item) -> str:
    """仅用于旧数据库迁移；新资产必须显式定稿。"""
    if isinstance(item, Element) and item.master_image:
        return item.master_image
    views = getattr(item, "reference_views", {}) or {}
    for role in ("master", "front", "three_quarter", "camera_a", "empty_plate"):
        if views.get(role):
            return views[role]
    refs = getattr(item, "reference_images", []) or []
    return refs[0] if refs else ""


def _migrate_legacy_approval(item, legacy: bool = False):
    if legacy and not getattr(item, "approved_reference", ""):
        master = _legacy_master_path(item)
        if master:
            item.approved_reference = master
            item.approval_status = "approved"
            item.version = max(1, int(getattr(item, "version", 0) or 0))
            if not getattr(item, "version_history", None):
                item.version_history = [{
                    "version": item.version,
                    "path": master,
                    "approved_at": 0.0,
                    "source": "legacy_migration",
                }]
    return item


def approved_asset_path(item) -> str:
    """返回生产链唯一允许使用的已批准母版。"""
    if item is None or getattr(item, "approval_status", "draft") != "approved":
        return ""
    return str(getattr(item, "approved_reference", "") or "")


def asset_is_approved(item, require_file: bool = True) -> bool:
    path = approved_asset_path(item)
    return bool(path and (not require_file or os.path.exists(path)))


def approve_asset_version(item, path: str, source: str = "manual") -> bool:
    """批准母版并创建不可变版本记录；返回是否产生了新版本。"""
    path = os.path.abspath(path) if path else ""
    if not path:
        raise ValueError("批准资产时缺少母版路径")
    refs = list(getattr(item, "reference_images", []) or [])
    refs = [value for value in refs if value != path]
    item.reference_images = [path] + refs
    old_path = str(getattr(item, "approved_reference", "") or "")
    was_approved = getattr(item, "approval_status", "draft") == "approved"
    changed = not was_approved or old_path != path
    if changed:
        item.version = max(0, int(getattr(item, "version", 0) or 0)) + 1
        history = list(getattr(item, "version_history", []) or [])
        history.append({
            "version": item.version,
            "path": path,
            "approved_at": __import__("time").time(),
            "source": source,
        })
        item.version_history = history[-50:]
    item.approved_reference = path
    item.approval_status = "approved"
    if isinstance(item, Element):
        item.master_image = path
    elif isinstance(item, Scene):
        views = dict(item.reference_views or {})
        views["master"] = path
        item.reference_views = views
    return changed


def assign_asset_view(item, role: str, path: str):
    """保存角色/场景视角，不隐式改变已批准母版。"""
    if not hasattr(item, "reference_views"):
        raise ValueError("当前资产不支持视角槽")
    role = str(role or "").strip()
    path = os.path.abspath(path) if path else ""
    if not role or not path:
        raise ValueError("视角角色或图片路径为空")
    refs = list(getattr(item, "reference_images", []) or [])
    if path not in refs:
        refs.append(path)
    item.reference_images = refs
    views = dict(getattr(item, "reference_views", {}) or {})
    views[role] = path
    item.reference_views = views


@dataclass
class PromptTemplate:
    """Prompt 模板（参数化）。"""
    id: str = ""
    name: str = ""
    category: str = ""                      # "portrait" / "landscape" / "product" / ...
    provider: str = ""                      # "seedream" / "flux" / "veo" / ...
    template: str = ""                      # "A {style} portrait of {character}, {background}"
    defaults: dict = field(default_factory=dict)   # {"style": "cinematic", "background": "studio"}
    tags: list[str] = field(default_factory=list)

    def render(self, **kwargs) -> str:
        """用参数填充模板。"""
        merged = {**self.defaults, **kwargs}
        try:
            return self.template.format(**merged)
        except KeyError as e:
            return self.template  # 缺参数时返回原始模板

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, d: dict) -> PromptTemplate:
        return cls(**{k: d.get(k, "") for k in cls.__dataclass_fields__})


@dataclass
class VoicePreset:
    """声音预设（克隆音色）。"""
    id: str = ""
    name: str = ""
    provider: str = ""                      # "fish_audio" / "voxcpm" / "elevenlabs"
    voice_id: str = ""                      # API 返回的 voice_id
    sample_path: str = ""                   # 参考音频路径
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, d: dict) -> VoicePreset:
        return cls(**{k: d.get(k, "") for k in cls.__dataclass_fields__})


# ──────────────────────────────────────────────
# SQLite 存储
# ──────────────────────────────────────────────

class AssetDB:
    """资源中心数据库。"""

    def __init__(self, db_path: str | Path = ""):
        if not db_path:
            db_path = Path(os.environ.get("CEP_DATA_DIR", Path.home() / ".cep_data")) / "ai_assets.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        # One connection is shared by the canvas, resource center and async
        # completion callbacks. Serialize reads as well as writes.
        self._lock = threading.RLock()

    @staticmethod
    def _is_transient_io_error(error: BaseException) -> bool:
        message = str(error or "").lower()
        return any(token in message for token in (
            "disk i/o error", "database is locked", "database table is locked",
            "locking protocol", "database schema has changed",
        ))

    def _discard_connection(self):
        connection, self._conn = self._conn, None
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass

    def _open_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.db_path), timeout=10.0, check_same_thread=False)
        try:
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            # Asset rows are small. Frequent checkpoints keep the WAL bounded
            # and reduce Windows filesystem/antivirus timing races.
            connection.execute("PRAGMA wal_autocheckpoint=200")
            connection.execute("PRAGMA temp_store=MEMORY")
            self._conn = connection
            self._migrate()
            return connection
        except Exception:
            self._conn = None
            connection.close()
            raise

    @property
    def conn(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is not None:
                return self._conn
            last_error = None
            for attempt in range(2):
                try:
                    return self._open_connection()
                except sqlite3.OperationalError as error:
                    last_error = error
                    if attempt or not self._is_transient_io_error(error):
                        raise
                    time.sleep(0.08)
            raise last_error  # pragma: no cover

    def _migrate(self):
        # Called after _open_connection assigned the connection.
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS characters (
                id TEXT PRIMARY KEY, data_json TEXT, created_at REAL, updated_at REAL
            );
            CREATE TABLE IF NOT EXISTS scenes (
                id TEXT PRIMARY KEY, data_json TEXT, created_at REAL, updated_at REAL
            );
            CREATE TABLE IF NOT EXISTS elements (
                id TEXT PRIMARY KEY, data_json TEXT, created_at REAL, updated_at REAL
            );
            CREATE TABLE IF NOT EXISTS prompts (
                id TEXT PRIMARY KEY, data_json TEXT, created_at REAL, updated_at REAL
            );
            CREATE TABLE IF NOT EXISTS voices (
                id TEXT PRIMARY KEY, data_json TEXT, created_at REAL, updated_at REAL
            );
        """)
        self._conn.commit()

    def _execute(self, statement: str, params=(), *, fetch="", commit=False):
        """Reconnect once for transient SQLite I/O/locking failures."""
        last_error = None
        for attempt in range(2):
            try:
                with self._lock:
                    cursor = self.conn.execute(statement, params)
                    if fetch == "one":
                        result = cursor.fetchone()
                    elif fetch == "all":
                        result = cursor.fetchall()
                    else:
                        result = None
                    if commit:
                        self.conn.commit()
                    return result
            except sqlite3.OperationalError as error:
                last_error = error
                if attempt or not self._is_transient_io_error(error):
                    raise
                with self._lock:
                    self._discard_connection()
                time.sleep(0.08)
        raise last_error  # pragma: no cover

    # ── 通用 CRUD ──

    def _save(self, table: str, item, ts: float = 0.0):
        import uuid as _uuid
        if ts == 0.0:
            ts = __import__("time").time()
        data = item.to_dict() if hasattr(item, "to_dict") else item
        if not data.get("id"):
            data["id"] = _uuid.uuid4().hex
        data["updated_at"] = ts
        if "created_at" not in data or not data["created_at"]:
            data["created_at"] = ts
        # 调用方通常会在 save_* 后立刻用 item.id 建立项目/镜头绑定；
        # 生成 ID 后必须同步回当前对象，不能只写进序列化副本。
        if hasattr(item, "id"):
            item.id = data["id"]
        if hasattr(item, "created_at"):
            item.created_at = data["created_at"]
        if hasattr(item, "updated_at"):
            item.updated_at = ts
        self._execute(
            f"INSERT OR REPLACE INTO {table} (id, data_json, created_at, updated_at) VALUES (?,?,?,?)",
            (data["id"], json.dumps(data, ensure_ascii=False), data["created_at"], ts),
            commit=True,
        )

    def _get(self, table: str, item_id: str, cls) -> Optional[object]:
        row = self._execute(
            f"SELECT data_json FROM {table} WHERE id = ?", (item_id,), fetch="one")
        if row:
            return cls.from_dict(json.loads(row[0]))
        return None

    def _list(self, table: str, cls, limit: int = 100, offset: int = 0) -> list:
        rows = self._execute(
            f"SELECT data_json FROM {table} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset), fetch="all")
        return [cls.from_dict(json.loads(r[0])) for r in rows]

    def _delete(self, table: str, item_id: str):
        self._execute(f"DELETE FROM {table} WHERE id = ?", (item_id,), commit=True)

    # ── 对外接口 ──

    def save_character(self, c: Character):    self._save("characters", c)
    def get_character(self, cid: str):         return self._get("characters", cid, Character)
    def list_characters(self, limit=100):      return self._list("characters", Character, limit)
    def delete_character(self, cid: str):      self._delete("characters", cid)

    def save_scene(self, s: Scene):            self._save("scenes", s)
    def get_scene(self, sid: str):             return self._get("scenes", sid, Scene)
    def list_scenes(self, limit=100):          return self._list("scenes", Scene, limit)
    def delete_scene(self, sid: str):          self._delete("scenes", sid)

    def save_element(self, item: Element):     self._save("elements", item)
    def get_element(self, item_id: str):       return self._get("elements", item_id, Element)
    def list_elements(self, limit=100):        return self._list("elements", Element, limit)
    def delete_element(self, item_id: str):    self._delete("elements", item_id)

    def save_prompt(self, p: PromptTemplate):  self._save("prompts", p)
    def get_prompt(self, pid: str):            return self._get("prompts", pid, PromptTemplate)
    def list_prompts(self, category="", limit=100):
        if category:
            rows = self._execute(
                "SELECT data_json FROM prompts WHERE json_extract(data_json, '$.category') = ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (category, limit), fetch="all")
        else:
            rows = self._execute(
                "SELECT data_json FROM prompts ORDER BY updated_at DESC LIMIT ?",
                (limit,), fetch="all")
        return [PromptTemplate.from_dict(json.loads(r[0])) for r in rows]
    def delete_prompt(self, pid: str):         self._delete("prompts", pid)

    def save_voice(self, v: VoicePreset):      self._save("voices", v)
    def get_voice(self, vid: str):             return self._get("voices", vid, VoicePreset)
    def list_voices(self, limit=100):          return self._list("voices", VoicePreset, limit)
    def delete_voice(self, vid: str):          self._delete("voices", vid)

    def close(self):
        with self._lock:
            self._discard_connection()
