from pathlib import Path

import pytest

from personal_ai_orchestrator.model_registry import Account, ModelRegistry, Provider
from personal_ai_orchestrator.runtime_config import (
    ApplicationSupportLayout,
    RuntimeConfig,
    bootstrap_application_support,
    default_application_support_layout,
    ensure_runtime_config_is_credential_free,
)


def _empty_config() -> RuntimeConfig:
    return RuntimeConfig(
        catalog_snapshot_id="catalog-p42",
        registry=ModelRegistry(),
    )


def _test_layout(tmp_path: Path) -> ApplicationSupportLayout:
    app_root = tmp_path / "Application Support" / "Personal AI Orchestrator"
    return ApplicationSupportLayout(
        app_support_root=app_root,
        runtime_config=app_root / "runtime.json",
        state_db=app_root / "state.sqlite3",
        runtime_state_root=app_root / "runtime-state",
        logs_root=app_root / "logs",
        socket_path=Path("/tmp") / f"{tmp_path.name}.sock",
    )


def test_default_application_support_layout_uses_user_library_and_short_cache_socket():
    layout = default_application_support_layout(Path("/Users/example"))
    assert layout.runtime_config == Path(
        "/Users/example/Library/Application Support/Personal AI Orchestrator/runtime.json"
    )
    assert layout.state_db == Path(
        "/Users/example/Library/Application Support/Personal AI Orchestrator/state.sqlite3"
    )
    assert layout.runtime_state_root.name == "runtime-state"
    assert layout.logs_root.name == "logs"
    assert layout.socket_path == Path(
        "/Users/example/Library/Caches/Personal AI Orchestrator/control.sock"
    )
    assert len(str(layout.socket_path).encode("utf-8")) <= 104


def test_bootstrap_application_support_writes_runtime_config_atomically(tmp_path):
    layout = _test_layout(tmp_path)
    resolved = bootstrap_application_support(_empty_config(), layout=layout)
    assert resolved == layout
    assert layout.runtime_config.exists()
    assert layout.state_db.parent.exists()
    assert layout.runtime_state_root.exists()
    assert layout.logs_root.exists()
    assert layout.socket_path.parent.exists()
    loaded = RuntimeConfig.model_validate_json(layout.runtime_config.read_text(encoding="utf-8"))
    assert loaded.catalog_snapshot_id == "catalog-p42"


def test_bootstrap_rejects_credential_refs_in_static_runtime_config(tmp_path):
    config = RuntimeConfig(
        catalog_snapshot_id="catalog-p42",
        registry=ModelRegistry(
            providers={"p": Provider(id="p", display_name="Provider")},
            accounts={
                "a": Account(
                    id="a",
                    provider_id="p",
                    label="account",
                    credential_ref="provider-secret-ref",
                )
            },
        ),
    )
    with pytest.raises(ValueError, match="credential_ref"):
        ensure_runtime_config_is_credential_free(config)
    with pytest.raises(ValueError, match="credential_ref"):
        bootstrap_application_support(config, layout=_test_layout(tmp_path))
