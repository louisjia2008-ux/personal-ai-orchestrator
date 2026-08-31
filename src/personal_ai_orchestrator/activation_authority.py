"""Construct production ACTIVE gates from durable host evidence."""

from __future__ import annotations

from personal_ai_orchestrator.activation import ActiveRoutingGate
from personal_ai_orchestrator.approval import ApprovalAuthority, ApprovalKind


def build_active_gate(
    *,
    approvals: ApprovalAuthority,
    owner_approval_id: str,
    p0_safety_kernel_authoritative: bool,
    p1_verifier_authoritative: bool,
    adapter_fail_closed_validated: bool,
    shadow_evidence_accepted: bool,
    safe_bypass_validated: bool,
) -> ActiveRoutingGate:
    """Build the value object only after checking the persisted owner approval record."""

    owner_approved = approvals.is_approved(
        owner_approval_id,
        kind=ApprovalKind.PRODUCTION_ACTIVE_ROUTING,
    )
    return ActiveRoutingGate(
        p0_safety_kernel_authoritative=p0_safety_kernel_authoritative,
        p1_verifier_authoritative=p1_verifier_authoritative,
        adapter_fail_closed_validated=adapter_fail_closed_validated,
        shadow_evidence_accepted=shadow_evidence_accepted,
        safe_bypass_validated=safe_bypass_validated,
        owner_approved=owner_approved,
    )


__all__ = ["build_active_gate"]
