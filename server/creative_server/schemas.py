from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


Role = Literal["admin", "producer", "director", "editor", "reviewer", "viewer"]


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str
    display_name: str = ""
    password: str
    role: Role = "producer"
    approved: bool = True
    daily_tasks: int = Field(50, ge=0, le=10000)
    daily_credits: int = Field(5000, ge=0, le=10_000_000)
    concurrent_tasks: int = Field(2, ge=0, le=50)
    allow_paid_models: bool = False
    allowed_models: list[str] = []


class UserUpdate(BaseModel):
    display_name: str | None = None
    role: Role | None = None
    status: Literal["pending", "active", "suspended"] | None = None
    password: str | None = None
    daily_tasks: int | None = Field(None, ge=0, le=10000)
    daily_credits: int | None = Field(None, ge=0, le=10_000_000)
    concurrent_tasks: int | None = Field(None, ge=0, le=50)
    allow_paid_models: bool | None = None
    allowed_models: list[str] | None = None


class TaskCreate(BaseModel):
    project_id: str
    node_id: str
    kind: str
    provider: str = ""
    model: str = ""
    estimated_credits: int = Field(0, ge=0, le=10_000_000)
    input: dict[str, Any] = {}


class CanvasDocument(BaseModel):
    protocol: Literal["creative-engine-canvas"] = "creative-engine-canvas"
    version: int = Field(1, ge=1)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    storyboard: dict[str, Any] | None = None
    desktopSource: dict[str, Any] | None = None


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    canvas: CanvasDocument


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    title: str | None = Field(None, min_length=1, max_length=200)
    canvas: CanvasDocument
    expected_version: int = Field(ge=1, alias="expectedVersion")


class ProductionRunCreate(BaseModel):
    project_id: str
    node_id: str
    automation_mode: Literal["checkpoints", "auto", "manual"] = "checkpoints"
    provider_locks: dict[str, str] = {}


class ProductionCommand(BaseModel):
    command: Literal["start", "continue", "approve", "accept_risk", "pause", "resume", "rewind"]
    target_stage: int | None = Field(None, ge=1, le=7)
