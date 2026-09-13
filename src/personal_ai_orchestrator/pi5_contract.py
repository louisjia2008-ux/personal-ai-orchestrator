"""Typed PAO contract for bounded delegation requests."""

from __future__ import annotations

from pydantic import Field, model_validator

from personal_ai_orchestrator.model_registry import RegistryModel

PI5_SCHEMA_VERSION = 1
PI5_MAX_REQUESTS_PER_PARENT_RUN = 3
PI5_MAX_INTENT_CHARS = 2000
PI5_MAX_REASON_CHARS = 512


class DelegationRequest(RegistryModel):
    """A worker suggestion that carries no provider/runtime authority."""

    schema_version: int = PI5_SCHEMA_VERSION
    tool_call_id: str = Field(min_length=1, max_length=256)
    ordinal: int = Field(ge=1, le=PI5_MAX_REQUESTS_PER_PARENT_RUN)
    intent: str = Field(min_length=1, max_length=PI5_MAX_INTENT_CHARS)
    reason: str = Field(min_length=1, max_length=PI5_MAX_REASON_CHARS)

    @model_validator(mode="after")
    def validate_request(self) -> DelegationRequest:
        if self.schema_version != PI5_SCHEMA_VERSION:
            raise ValueError("unsupported PI-5 schema version")
        if not self.intent.strip():
            raise ValueError("intent must not be blank")
        if not self.reason.strip():
            raise ValueError("reason must not be blank")
        return self


__all__ = [
    "PI5_MAX_INTENT_CHARS",
    "PI5_MAX_REASON_CHARS",
    "PI5_MAX_REQUESTS_PER_PARENT_RUN",
    "PI5_SCHEMA_VERSION",
    "DelegationRequest",
]
