"""Typed, credential-free runtime configuration for the headless Shadow daemon."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.scheduler import RoutingPolicy, TargetTelemetry, TaskProfile


class RuntimeConfig(BaseModel):
    """Static startup inputs; provider credentials are deliberately excluded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1)
    catalog_snapshot_id: str = Field(min_length=1)
    registry: ModelRegistry
    policy: RoutingPolicy = Field(default_factory=RoutingPolicy)
    task_profiles: tuple[TaskProfile, ...] = ()
    runtime_availability: dict[str, bool] = Field(default_factory=dict)
    telemetry: dict[str, TargetTelemetry] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_runtime_refs(self) -> RuntimeConfig:
        target_ids = set(self.registry.execution_targets)
        unknown_runtime = set(self.runtime_availability) - target_ids
        unknown_telemetry = set(self.telemetry) - target_ids
        if unknown_runtime:
            raise ValueError(
                "runtime_availability references unknown targets: "
                f"{sorted(unknown_runtime)}"
            )
        if unknown_telemetry:
            raise ValueError(f"telemetry references unknown targets: {sorted(unknown_telemetry)}")
        task_ids = [profile.task_id for profile in self.task_profiles]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task_profiles contain duplicate task_id values")
        return self


__all__ = ["RuntimeConfig"]
