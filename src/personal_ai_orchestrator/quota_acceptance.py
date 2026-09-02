"""Provider-scoped, read-only quota acceptance — fail closed without a target.

The footgun this module closes
------------------------------
A previous acceptance run meant to read *one* provider's quota and called
``POST /v1/quota/refresh`` with no provider filter. The daemon did exactly what
that endpoint means and refreshed **every** connected provider, including one
whose credential was known-compromised and pending rotation. Nothing failed; the
call simply had a wider blast radius than the operator intended.

The lesson is not "remember the argument". A scoped operation whose target is
optional will eventually be invoked without one, and its unscoped meaning —
contact every provider we hold a credential for — is precisely the outcome that
must never happen by omission.

So the two operations are separated by *shape* rather than by discipline:

``scoped_quota_refresh(client, provider_id=...)``
    Refreshes exactly one provider. ``provider_id`` is required, is rejected
    when blank, and the result is verified to name that provider and no other.
    Omitting it raises :class:`ScopedRefreshTargetMissing`; it can never widen
    into an all-provider refresh.

``refresh_all_provider_quota(client)``
    The product-wide "刷新全部额度" operation. Still available, but it must be
    named to be invoked, so an all-provider read is always a decision.

Both paths are read-only: they reach the same P3 collectors, each of which
performs one authenticated ``GET`` against a documented quota endpoint. Neither
issues a model generation, and neither prints, logs, or returns a credential.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Protocol

from personal_ai_orchestrator.control_api import QuotaRefreshResultView


class ScopedRefreshTargetMissing(ValueError):
    """Raised when a provider-scoped refresh was requested without a provider.

    Deliberately *not* a fallback to refreshing everything: the caller asked for
    a narrow operation, and the safe answer to "which one?" is to stop.
    """


class ScopedRefreshWidened(RuntimeError):
    """Raised when a scoped refresh touched a provider it was not asked to.

    The check is cheap and the failure it catches is the exact one that already
    happened once, so it is enforced rather than assumed.
    """


class SupportsQuotaRefresh(Protocol):
    def refresh_quota(self, provider_id: str | None = None) -> QuotaRefreshResultView: ...


def scoped_quota_refresh(
    client: SupportsQuotaRefresh,
    *,
    provider_id: str | None,
) -> QuotaRefreshResultView:
    """Read one provider's quota, or fail — never fall back to all providers.

    ``provider_id`` is keyword-only and has no default, so the unscoped call is
    not expressible by accident. A ``None`` or blank value is a caller bug and
    is reported as one.
    """

    if provider_id is None or not provider_id.strip():
        raise ScopedRefreshTargetMissing(
            "provider-scoped quota refresh requires an explicit provider_id; "
            "use refresh_all_provider_quota() to refresh every connected provider"
        )
    target = provider_id.strip()
    result = client.refresh_quota(target)
    unexpected = tuple(
        item for item in result.refreshed_provider_ids if item != target
    )
    if unexpected:
        raise ScopedRefreshWidened(
            f"scoped refresh of {target!r} also refreshed {unexpected!r}"
        )
    return result


def refresh_all_provider_quota(
    client: SupportsQuotaRefresh,
) -> QuotaRefreshResultView:
    """The deliberate product-wide refresh, reachable only by naming it."""

    return client.refresh_quota(None)


def _sanitized_report(result: QuotaRefreshResultView, *, provider_id: str) -> dict:
    """Evidence a scoped acceptance run can print.

    Carries plan state, windows, and workload scope — never a credential, a
    token, an endpoint with query parameters, or a raw provider body.
    """

    cards = [
        card for card in result.overview.providers if card.provider_id == provider_id
    ]
    return {
        "provider_id": provider_id,
        "refreshed_provider_ids": list(result.refreshed_provider_ids),
        "page_state": result.overview.state,
        "summary": {
            "connected_provider_count": result.overview.summary.connected_provider_count,
            "quota_observable_provider_count": (
                result.overview.summary.quota_observable_provider_count
            ),
        },
        "providers": [
            {
                "provider_id": card.provider_id,
                "quota_state": card.quota_state,
                "confidence": card.confidence,
                "failure_reason": card.failure_reason,
                "credential_source": card.credential_source,
                "plan": (
                    None
                    if card.plan is None
                    else {
                        "display_name": card.plan.display_name,
                        "quota_semantics": card.plan.quota_semantics,
                        "active_workload_scope": card.plan.active_workload_scope,
                        "workload_scope_notes": list(card.plan.workload_scope_notes),
                        "state": card.plan.state,
                        "confidence": card.plan.confidence,
                        "unknown_reason": card.plan.unknown_reason,
                        "windows": [
                            {
                                "window_id": window.window_id,
                                "window_kind": window.window_kind,
                                "remaining_fraction": window.remaining_fraction,
                                "confidence": window.confidence,
                                "reset_at": window.reset_at,
                            }
                            for window in card.plan.windows
                        ],
                        "binding_window_id": (
                            None
                            if card.plan.binding_window is None
                            else card.plan.binding_window.window_id
                        ),
                        "scope_views": [
                            {
                                "scope_id": item.scope_id,
                                "scope_kind": item.scope_kind,
                                "workload_scope": item.workload_scope,
                                "window_id": item.window_id,
                                "remaining_fraction": item.remaining_fraction,
                            }
                            for item in card.plan.model_equivalents
                        ],
                    }
                ),
            }
            for card in cards
        ],
    }


def main(argv: list[str] | None = None) -> int:
    """CLI for a single-provider read-only quota acceptance.

    ``--provider-id`` is required by the parser, so the command cannot degrade
    into an all-provider refresh through a forgotten flag.
    """

    parser = argparse.ArgumentParser(
        description=(
            "Read exactly one connected provider's quota through its documented "
            "read-only endpoint. Never refreshes other providers."
        )
    )
    parser.add_argument(
        "--provider-id",
        required=True,
        help="The provider surface to refresh, e.g. minimax-cn-coding-plan.",
    )
    parser.add_argument(
        "--socket",
        type=Path,
        default=None,
        help="Control-plane unix socket path (defaults to the daemon's own).",
    )
    args = parser.parse_args(argv)

    from personal_ai_orchestrator.control_client import ControlPlaneClient
    from personal_ai_orchestrator.runtime_config import (
        default_application_support_layout,
    )

    socket_path = Path(
        args.socket
        or os.environ.get("PAO_CONTROL_SOCKET")
        or default_application_support_layout().socket_path
    )
    client = ControlPlaneClient(socket_path)
    try:
        result = scoped_quota_refresh(client, provider_id=args.provider_id)
    except (ScopedRefreshTargetMissing, ScopedRefreshWidened) as exc:
        print(f"SCOPED_REFRESH_REFUSED: {exc}")
        return 2
    print(
        json.dumps(
            _sanitized_report(result, provider_id=args.provider_id.strip()),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


__all__ = [
    "ScopedRefreshTargetMissing",
    "ScopedRefreshWidened",
    "SupportsQuotaRefresh",
    "refresh_all_provider_quota",
    "scoped_quota_refresh",
]


if __name__ == "__main__":
    raise SystemExit(main())
