"""Explicit production activation gate for model switching."""

from pydantic import BaseModel, ConfigDict


class ActiveRoutingGate(BaseModel):
    """Evidence flags required before a production session may be switched automatically."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    p0_safety_kernel_authoritative: bool = False
    p1_verifier_authoritative: bool = False
    adapter_fail_closed_validated: bool = False
    shadow_evidence_accepted: bool = False
    safe_bypass_validated: bool = False

    @property
    def authorized(self) -> bool:
        return all(
            (
                self.p0_safety_kernel_authoritative,
                self.p1_verifier_authoritative,
                self.adapter_fail_closed_validated,
                self.shadow_evidence_accepted,
                self.safe_bypass_validated,
            )
        )

    def blocking_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if not self.p0_safety_kernel_authoritative:
            reasons.append("P0 Safety Kernel authority missing")
        if not self.p1_verifier_authoritative:
            reasons.append("P1 deterministic verifier authority missing")
        if not self.adapter_fail_closed_validated:
            reasons.append("adapter fail-closed validation missing")
        if not self.shadow_evidence_accepted:
            reasons.append("P3.5 Shadow evidence not accepted")
        if not self.safe_bypass_validated:
            reasons.append("safe BYPASS not validated")
        return tuple(reasons)


__all__ = ["ActiveRoutingGate"]
