from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class CommandRequest(BaseModel):
    command_id: str = Field(min_length=8, max_length=128)
    expected_revision: int | None = Field(default=None, ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)


class PairRequest(BaseModel):
    token: str = Field(min_length=16, max_length=256)
    device_name: str = Field(default="Browser", max_length=80)


class Snapshot(BaseModel):
    project_instance_id: str
    revision: int
    data: Any


class ApiError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
