"""Typed, credential-free runtime configuration for the headless Shadow daemon."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_ai_orchestrator.model_registry import ModelRegistry
from personal_ai_orchestrator.scheduler import RoutingPolicy, TargetTelemetry, TaskProfile

APP_SUPPORT_DIR_NAME = "Personal AI Orchestrator"
CONTROL_SOCKET_NAME = "control.sock"
MACOS_AF_UNIX_PATH_LIMIT = 104


class RuntimeConfig(BaseModel):
    """Static startup inputs; provider credentials are deliberately excluded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1)
    catalog_snapshot_id: str = Field(min_length=1)
    registry: ModelRegistry
    policy: RoutingPolicy = Field(default_factory=RoutingPolicy)
    project_policy_overrides: dict[str, RoutingPolicy] = Field(default_factory=dict)
    task_policy_overrides: dict[str, RoutingPolicy] = Field(default_factory=dict)
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
                f"runtime_availability references unknown targets: {sorted(unknown_runtime)}"
            )
        if unknown_telemetry:
            raise ValueError(f"telemetry references unknown targets: {sorted(unknown_telemetry)}")
        task_ids = [profile.task_id for profile in self.task_profiles]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task_profiles contain duplicate task_id values")
        return self


class ApplicationSupportLayout(BaseModel):
    """Per-user product runtime layout; never points at source worktrees by default."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    app_support_root: Path
    runtime_config: Path
    state_db: Path
    runtime_state_root: Path
    logs_root: Path
    socket_path: Path

    @model_validator(mode="after")
    def validate_socket_length(self) -> ApplicationSupportLayout:
        if len(str(self.socket_path).encode("utf-8")) > MACOS_AF_UNIX_PATH_LIMIT:
            raise ValueError("control socket path exceeds macOS AF_UNIX limit")
        return self


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def default_application_support_root(home: Path | None = None) -> Path:
    base = home or _home()
    return base / "Library" / "Application Support" / APP_SUPPORT_DIR_NAME


def default_cache_root(home: Path | None = None) -> Path:
    base = home or _home()
    return base / "Library" / "Caches" / APP_SUPPORT_DIR_NAME


def default_application_support_layout(home: Path | None = None) -> ApplicationSupportLayout:
    app_root = default_application_support_root(home)
    cache_root = default_cache_root(home)
    return ApplicationSupportLayout(
        app_support_root=app_root,
        runtime_config=app_root / "runtime.json",
        state_db=app_root / "state.sqlite3",
        runtime_state_root=app_root / "runtime-state",
        logs_root=app_root / "logs",
        socket_path=cache_root / CONTROL_SOCKET_NAME,
    )


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        temp_name = handle.name
    os.replace(temp_name, path)


def ensure_runtime_config_is_credential_free(config: RuntimeConfig) -> None:
    """Reject static config that embeds provider credential references."""

    offenders = [
        account.id
        for account in config.registry.accounts.values()
        if account.credential_ref is not None
    ]
    if offenders:
        raise ValueError(
            "runtime config must not contain credential_ref values for accounts: "
            f"{sorted(offenders)}"
        )


def bootstrap_application_support(
    config: RuntimeConfig,
    *,
    layout: ApplicationSupportLayout | None = None,
    overwrite: bool = False,
) -> ApplicationSupportLayout:
    """Create the macOS runtime layout and write a validated runtime.json atomically."""

    resolved = layout or default_application_support_layout()
    ensure_runtime_config_is_credential_free(config)
    resolved.app_support_root.mkdir(parents=True, exist_ok=True)
    resolved.runtime_state_root.mkdir(parents=True, exist_ok=True)
    resolved.logs_root.mkdir(parents=True, exist_ok=True)
    resolved.socket_path.parent.mkdir(parents=True, exist_ok=True)
    if resolved.runtime_config.exists() and not overwrite:
        loaded = RuntimeConfig.model_validate_json(
            resolved.runtime_config.read_text(encoding="utf-8")
        )
        ensure_runtime_config_is_credential_free(loaded)
        return resolved
    _atomic_write(resolved.runtime_config, config.model_dump_json(indent=2))
    return resolved


__all__ = [
    "ApplicationSupportLayout",
    "RuntimeConfig",
    "bootstrap_application_support",
    "default_application_support_layout",
    "ensure_runtime_config_is_credential_free",
]
