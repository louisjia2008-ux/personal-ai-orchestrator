"""JSON bridge for provider-native Telegram hosts and local DeskPet tools.

The bridge intentionally has no Telegram token option.  A bot host keeps that
credential in Telegram's/provider-native secret store and pipes a normalized
update to this process.  The only downstream connection is PAO's owner-only
Unix socket.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from personal_ai_orchestrator.client_gateway import (
    DeskPetClientAdapter,
    DeskPetToolRequest,
    ExternalClientGateway,
    TaskStateNotifier,
    TelegramAllowlist,
    TelegramClientAdapter,
    TelegramUpdate,
)
from personal_ai_orchestrator.control_api import ControlPlaneError
from personal_ai_orchestrator.control_client import ControlPlaneClient, ControlPlaneUnavailable


def _default_socket() -> str:
    return str(Path.home() / "Library" / "Caches" / "Personal AI Orchestrator" / "control.sock")


def _socket_path(args: argparse.Namespace) -> str:
    return args.socket or os.environ.get("PAO_CONTROL_SOCKET") or _default_socket()


def _read_object() -> dict[str, Any]:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise ValueError("json_object_required")
    return payload


def _write(payload: object) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pao-client-bridge",
        description="Typed Telegram/DeskPet bridge for the PAO Unix socket",
    )
    parser.add_argument("--socket", help="PAO control socket (or PAO_CONTROL_SOCKET)")
    subparsers = parser.add_subparsers(dest="client", required=True)

    telegram = subparsers.add_parser(
        "telegram",
        help="read one sanitized TelegramUpdate JSON object from stdin",
    )
    telegram.add_argument("--allow-user", action="append", type=int, required=True)
    telegram.add_argument("--allow-chat", action="append", type=int, required=True)

    deskpet = subparsers.add_parser(
        "deskpet",
        help="read one DeskPetToolRequest JSON object from stdin",
    )
    deskpet.add_argument("--installation-id", required=True)

    watch = subparsers.add_parser(
        "watch",
        help="emit JSON-lines notifications when durable task state changes",
    )
    watch.add_argument("--interval", type=float, default=2.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = ControlPlaneClient(_socket_path(args))
    gateway = ExternalClientGateway(client)
    try:
        if args.client == "telegram":
            adapter = TelegramClientAdapter(
                gateway,
                TelegramAllowlist(
                    user_ids=frozenset(args.allow_user),
                    chat_ids=frozenset(args.allow_chat),
                ),
            )
            result = adapter.handle_update(TelegramUpdate.model_validate(_read_object()))
            _write(
                {"accepted": False, "reason": "sender_not_allowed"}
                if result is None
                else {"accepted": True, "result": result.model_dump(mode="json")}
            )
            return 0
        if args.client == "deskpet":
            adapter = DeskPetClientAdapter(gateway, installation_id=args.installation_id)
            result = adapter.call(DeskPetToolRequest.model_validate(_read_object()))
            _write(result.model_dump(mode="json"))
            return 0

        if args.interval < 0.25 or args.interval > 300:
            raise ValueError("watch_interval_out_of_range")
        notifier = TaskStateNotifier(client)
        notifier.poll()  # establish a baseline; do not replay historical tasks as new alerts
        while True:
            time.sleep(args.interval)
            for notification in notifier.poll():
                _write(notification.model_dump(mode="json"))
                sys.stdout.flush()
    except KeyboardInterrupt:
        return 0
    except ValidationError:
        _write({"error": "invalid_client_request"})
    except json.JSONDecodeError:
        _write({"error": "invalid_json"})
    except ValueError as error:
        _write({"error": str(error)})
    except ControlPlaneUnavailable:
        _write({"error": "control_plane_unavailable"})
    except ControlPlaneError as error:
        _write({"error": error.code, "status": error.status})
    return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
