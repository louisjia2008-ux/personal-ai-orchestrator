from pathlib import Path

from personal_ai_orchestrator.daemon import (
    _should_register_supervised_auto,
    parse_args as parse_daemon_args,
)
from personal_ai_orchestrator.product_daemon import build_daemon_argv
from personal_ai_orchestrator.runtime_config import ApplicationSupportLayout


def _layout(tmp_path: Path) -> ApplicationSupportLayout:
    app_root = tmp_path / "app"
    return ApplicationSupportLayout(
        app_support_root=app_root,
        runtime_config=app_root / "runtime.json",
        state_db=app_root / "state.sqlite3",
        runtime_state_root=app_root / "runtime-state",
        logs_root=app_root / "logs",
        socket_path=tmp_path / "control.sock",
    )


def test_generic_control_only_remains_heartbeat_only(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    args = parse_daemon_args(
        [
            "--config",
            str(layout.runtime_config),
            "--state-db",
            str(layout.state_db),
            "--runtime-state-root",
            str(layout.runtime_state_root),
            "--control-socket",
            str(layout.socket_path),
            "--control-only",
        ]
    )
    assert args.control_only is True
    assert args.enable_supervised_auto_tick is False
    assert _should_register_supervised_auto(args) is False


def test_product_control_daemon_explicitly_enables_supervised_auto_tick(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    argv = build_daemon_argv(layout, host="127.0.0.1", port=8765)
    args = parse_daemon_args(argv)

    assert args.control_only is True
    assert args.enable_supervised_auto_tick is True
    assert args.control_socket == layout.socket_path
    assert _should_register_supervised_auto(args) is True


def test_non_control_only_socket_daemon_keeps_existing_supervised_auto_behavior(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    args = parse_daemon_args(
        [
            "--config",
            str(layout.runtime_config),
            "--state-db",
            str(layout.state_db),
            "--runtime-state-root",
            str(layout.runtime_state_root),
            "--control-socket",
            str(layout.socket_path),
        ]
    )
    assert args.control_only is False
    assert args.enable_supervised_auto_tick is False
    assert _should_register_supervised_auto(args) is True
