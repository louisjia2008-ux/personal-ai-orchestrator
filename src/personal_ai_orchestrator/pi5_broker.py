"""PI-5B1 host-owned bounded delegation broker foundation.

The broker binds a worker request to host-supplied parent identity, enforces
one-level delegation and a per-parent request budget, and delegates actual
child execution to an injected host port. The wire request cannot select a
provider, model, runtime, worktree, verifier, quota policy, or child ID.

PI-5B1 does not wire this broker into production Pi argv. It is a synthetic,
feature-off foundation for PI-5B2.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import Field, ValidationError, model_validator

from personal_ai_orchestrator.model_registry import RegistryModel
from personal_ai_orchestrator.pi5_contract import (
    PI5_MAX_REQUESTS_PER_PARENT_RUN,
    PI5_SCHEMA_VERSION,
    DelegationRequest,
)
from personal_ai_orchestrator.provider_acceptance import assert_sanitized

PI5_BROKER_MAX_REQUEST_BYTES = 16 * 1024
PI5_BROKER_MAX_SUMMARY_CHARS = 1024
PI5_CHILD_DEPTH = 1


class DelegationBrokerStatus(StrEnum):
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class DelegationBrokerContext:
    """Host-bound parent identity; never accepted from worker JSON."""

    parent_task_id: str
    parent_run_id: str
    project_id: str
    base_sha: str
    working_subpath: str | None = None
    parent_depth: int = 0
    max_children: int = PI5_MAX_REQUESTS_PER_PARENT_RUN

    def __post_init__(self) -> None:
        for name in ("parent_task_id", "parent_run_id", "project_id", "base_sha"):
            if not getattr(self, name):
                raise ValueError(f"{name} is required")
        if self.parent_depth != 0:
            raise ValueError("PI-5B1 permits delegation only from depth-0 parents")
        if not 1 <= self.max_children <= PI5_MAX_REQUESTS_PER_PARENT_RUN:
            raise ValueError("max_children exceeds PI-5 bounded delegation policy")


class DelegationChildPlan(RegistryModel):
    """Host-minted child plan handed to the child execution port."""

    child_task_id: str = Field(min_length=1, max_length=128)
    parent_task_id: str = Field(min_length=1)
    parent_run_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    base_sha: str = Field(min_length=1)
    working_subpath: str | None = None
    ordinal: int = Field(ge=1, le=PI5_MAX_REQUESTS_PER_PARENT_RUN)
    intent: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    depth: int = PI5_CHILD_DEPTH
    delegation_allowed: bool = False

    @model_validator(mode="after")
    def validate_child_boundary(self) -> DelegationChildPlan:
        if self.depth != PI5_CHILD_DEPTH:
            raise ValueError("PI-5B1 child depth must be exactly 1")
        if self.delegation_allowed:
            raise ValueError("recursive child delegation is disabled")
        return self


class DelegationChildResult(RegistryModel):
    """Sanitized child result returned by the host execution port."""

    child_task_id: str = Field(min_length=1)
    final_state: str = Field(min_length=1, max_length=64)
    selected_execution_target_id: str | None = Field(default=None, max_length=256)
    verified: bool = False
    summary: str = Field(default="", max_length=PI5_BROKER_MAX_SUMMARY_CHARS)

    @model_validator(mode="after")
    def validate_verified_state(self) -> DelegationChildResult:
        if self.verified and self.final_state != "VERIFIED":
            raise ValueError("verified child result requires final_state=VERIFIED")
        assert_sanitized(self.model_dump(mode="json"))
        return self


class DelegationBrokerResponse(RegistryModel):
    schema_version: int = PI5_SCHEMA_VERSION
    tool_call_id: str | None = Field(default=None, max_length=256)
    ordinal: int | None = Field(
        default=None, ge=1, le=PI5_MAX_REQUESTS_PER_PARENT_RUN
    )
    status: DelegationBrokerStatus
    reason_code: str = Field(min_length=1, max_length=128)
    child_task_id: str | None = Field(default=None, max_length=128)
    selected_execution_target_id: str | None = Field(default=None, max_length=256)
    child_state: str | None = Field(default=None, max_length=64)
    verified: bool = False
    summary: str = Field(default="", max_length=PI5_BROKER_MAX_SUMMARY_CHARS)

    @model_validator(mode="after")
    def validate_response(self) -> DelegationBrokerResponse:
        if self.schema_version != PI5_SCHEMA_VERSION:
            raise ValueError("unsupported PI-5 broker schema version")
        if self.verified and self.child_state != "VERIFIED":
            raise ValueError("verified broker response requires VERIFIED child state")
        assert_sanitized(self.model_dump(mode="json"))
        return self


class DelegationChildExecutionPort(Protocol):
    async def execute_child(self, plan: DelegationChildPlan) -> DelegationChildResult: ...


def delegation_child_task_id(
    *, parent_task_id: str, parent_run_id: str, ordinal: int
) -> str:
    """Mint a stable host child ID without trusting worker-supplied identity."""

    digest = hashlib.sha256(
        f"{parent_task_id}\x00{parent_run_id}\x00{ordinal}".encode()
    ).hexdigest()[:20]
    return f"pi5-child-{digest}"


class DelegationBrokerSession:
    """One parent-run delegation budget and idempotency domain."""

    def __init__(
        self,
        *,
        context: DelegationBrokerContext,
        child_port: DelegationChildExecutionPort,
    ) -> None:
        self.context = context
        self.child_port = child_port
        self._consumed = 0
        self._responses: dict[str, tuple[str, DelegationBrokerResponse]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _fingerprint(request: DelegationRequest) -> str:
        rendered = json.dumps(
            request.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(rendered.encode("utf-8")).hexdigest()

    def _reject(
        self, request: DelegationRequest, reason_code: str
    ) -> DelegationBrokerResponse:
        return DelegationBrokerResponse(
            tool_call_id=request.tool_call_id,
            ordinal=request.ordinal,
            status=DelegationBrokerStatus.REJECTED,
            reason_code=reason_code,
        )

    async def handle(self, request: DelegationRequest) -> DelegationBrokerResponse:
        assert_sanitized(request.model_dump(mode="json"))
        fingerprint = self._fingerprint(request)
        async with self._lock:
            prior = self._responses.get(request.tool_call_id)
            if prior is not None:
                prior_fingerprint, prior_response = prior
                if prior_fingerprint == fingerprint:
                    return prior_response
                return self._reject(request, "TOOL_CALL_ID_CONFLICT")

            if self._consumed >= self.context.max_children:
                return self._reject(request, "DELEGATION_BUDGET_EXHAUSTED")
            if request.ordinal != self._consumed + 1:
                return self._reject(request, "DELEGATION_ORDINAL_MISMATCH")

            # Consume the budget before execution so worker-triggered failures
            # cannot be retried indefinitely within one parent run.
            self._consumed += 1
            child_task_id = delegation_child_task_id(
                parent_task_id=self.context.parent_task_id,
                parent_run_id=self.context.parent_run_id,
                ordinal=request.ordinal,
            )
            plan = DelegationChildPlan(
                child_task_id=child_task_id,
                parent_task_id=self.context.parent_task_id,
                parent_run_id=self.context.parent_run_id,
                project_id=self.context.project_id,
                base_sha=self.context.base_sha,
                working_subpath=self.context.working_subpath,
                ordinal=request.ordinal,
                intent=request.intent,
                reason=request.reason,
                depth=PI5_CHILD_DEPTH,
                delegation_allowed=False,
            )

            try:
                result = await self.child_port.execute_child(plan)
                if result.child_task_id != child_task_id:
                    response = DelegationBrokerResponse(
                        tool_call_id=request.tool_call_id,
                        ordinal=request.ordinal,
                        status=DelegationBrokerStatus.ERROR,
                        reason_code="CHILD_IDENTITY_MISMATCH",
                    )
                else:
                    response = DelegationBrokerResponse(
                        tool_call_id=request.tool_call_id,
                        ordinal=request.ordinal,
                        status=DelegationBrokerStatus.COMPLETED,
                        reason_code="CHILD_EXECUTION_FINISHED",
                        child_task_id=result.child_task_id,
                        selected_execution_target_id=(
                            result.selected_execution_target_id
                        ),
                        child_state=result.final_state,
                        verified=result.verified,
                        summary=result.summary,
                    )
            except Exception:
                # Worker-facing response is deliberately generic; provider,
                # credentials, raw transcripts, and exception text stay host-side.
                response = DelegationBrokerResponse(
                    tool_call_id=request.tool_call_id,
                    ordinal=request.ordinal,
                    status=DelegationBrokerStatus.ERROR,
                    reason_code="CHILD_EXECUTION_ERROR",
                )

            self._responses[request.tool_call_id] = (fingerprint, response)
            return response


class UnixDelegationBrokerServer:
    """One-line JSON over a parent-bound Unix domain socket."""

    def __init__(
        self,
        *,
        socket_path: Path,
        session: DelegationBrokerSession,
    ) -> None:
        self.socket_path = socket_path
        self.session = session
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        if self._server is not None:
            return
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.socket_path.parent, 0o700)
        if self.socket_path.exists():
            raise FileExistsError(self.socket_path)
        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self.socket_path),
            limit=PI5_BROKER_MAX_REQUEST_BYTES + 1,
        )
        os.chmod(self.socket_path, 0o600)

    async def stop(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()
        if self.socket_path.exists():
            mode = self.socket_path.stat().st_mode
            if not stat.S_ISSOCK(mode):
                raise RuntimeError("refusing to unlink non-socket broker path")
            self.socket_path.unlink()

    async def _write(
        self, writer: asyncio.StreamWriter, response: DelegationBrokerResponse
    ) -> None:
        payload = response.model_dump(mode="json")
        assert_sanitized(payload)
        writer.write(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            + b"\n"
        )
        await writer.drain()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            try:
                raw = await reader.readline()
            except ValueError:
                raw = b""
            if not raw or len(raw) > PI5_BROKER_MAX_REQUEST_BYTES:
                await self._write(
                    writer,
                    DelegationBrokerResponse(
                        status=DelegationBrokerStatus.REJECTED,
                        reason_code="INVALID_REQUEST",
                    ),
                )
                return
            try:
                payload = json.loads(raw.decode("utf-8", errors="strict"))
                request = DelegationRequest.model_validate(payload)
                assert_sanitized(request.model_dump(mode="json"))
            except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError):
                await self._write(
                    writer,
                    DelegationBrokerResponse(
                        status=DelegationBrokerStatus.REJECTED,
                        reason_code="INVALID_REQUEST",
                    ),
                )
                return
            await self._write(writer, await self.session.handle(request))
        finally:
            writer.close()
            await writer.wait_closed()


__all__ = [
    "PI5_BROKER_MAX_REQUEST_BYTES",
    "PI5_BROKER_MAX_SUMMARY_CHARS",
    "PI5_CHILD_DEPTH",
    "DelegationBrokerContext",
    "DelegationBrokerResponse",
    "DelegationBrokerSession",
    "DelegationBrokerStatus",
    "DelegationChildExecutionPort",
    "DelegationChildPlan",
    "DelegationChildResult",
    "UnixDelegationBrokerServer",
    "delegation_child_task_id",
]
