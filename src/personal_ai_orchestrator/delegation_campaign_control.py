"""Typed local control-plane extension for the PI-5B3F calibration campaign."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from personal_ai_orchestrator.control_api import (
    ControlPlaneError,
    ControlPlaneService,
    UnixThreadingHTTPServer,
    handler_for_control,
)
from personal_ai_orchestrator.delegation_campaign import (
    MAX_CAMPAIGN_OBSERVATIONS,
    MAX_CAMPAIGN_PROJECTS,
    DelegationCalibrationCampaignStore,
    DelegationCampaignSnapshot,
)


class _CampaignModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DelegationCampaignStartRequest(_CampaignModel):
    max_observations: int = Field(ge=1, le=MAX_CAMPAIGN_OBSERVATIONS)
    project_ids: tuple[str, ...] = Field(default=(), max_length=MAX_CAMPAIGN_PROJECTS)


class DelegationCampaignView(_CampaignModel):
    campaign_id: str | None = None
    state: str
    max_observations: int | None = None
    consumed_observations: int
    remaining_observations: int | None = None
    project_ids: tuple[str, ...] = ()
    started_at: str | None = None
    stopped_at: str | None = None
    updated_at: str | None = None
    reason_code: str


def campaign_view(snapshot: DelegationCampaignSnapshot) -> DelegationCampaignView:
    return DelegationCampaignView(
        campaign_id=snapshot.campaign_id,
        state=snapshot.state.value,
        max_observations=snapshot.max_observations,
        consumed_observations=snapshot.consumed_observations,
        remaining_observations=snapshot.remaining_observations,
        project_ids=snapshot.project_ids,
        started_at=(snapshot.started_at.isoformat() if snapshot.started_at else None),
        stopped_at=(snapshot.stopped_at.isoformat() if snapshot.stopped_at else None),
        updated_at=(snapshot.updated_at.isoformat() if snapshot.updated_at else None),
        reason_code=snapshot.reason_code,
    )


def handler_for_campaign_control(
    service: ControlPlaneService,
    campaign: DelegationCalibrationCampaignStore,
):
    base = handler_for_control(service)

    class Handler(base):
        def _route(
            self,
            method: str,
            rest: tuple[str, ...],
            query: dict[str, list[str]],
            request_service: ControlPlaneService,
        ) -> None:
            root = ("settings", "delegation-calibration-campaign")
            if rest == root:
                if method != "GET":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                self._view(200, campaign_view(campaign.snapshot()))
                return

            if rest == (*root, "start"):
                if method != "POST":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                payload = self._read_json()
                if payload is None:
                    return
                request = DelegationCampaignStartRequest.model_validate(payload)
                for project_id in request.project_ids:
                    request_service._validate_identifier("project_id", project_id)
                    try:
                        request_service.store.get_project(project_id)
                    except KeyError:
                        raise ControlPlaneError(404, "project_not_found") from None
                try:
                    snapshot = campaign.start(
                        max_observations=request.max_observations,
                        project_ids=request.project_ids,
                    )
                except ValueError as error:
                    code = str(error)
                    if code == "campaign_already_active":
                        raise ControlPlaneError(409, code) from None
                    raise ControlPlaneError(400, code or "invalid_campaign_request") from None
                self._view(200, campaign_view(snapshot))
                return

            if rest == (*root, "stop"):
                if method != "POST":
                    self._json(405, {"error": "method_not_allowed"})
                    return
                self._view(200, campaign_view(campaign.stop()))
                return

            super()._route(method, rest, query, request_service)

    return Handler


class DelegationCampaignControlPlaneServer:
    """ControlPlaneServer-compatible UDS server with the campaign routes added."""

    def __init__(
        self,
        service: ControlPlaneService,
        socket_path: str | Path,
        *,
        campaign: DelegationCalibrationCampaignStore,
        socket_mode: int = 0o600,
    ) -> None:
        if service.store.path == ":memory:":
            raise ValueError("threaded control plane requires a durable file-backed state store")
        self.service = service
        self.campaign = campaign
        self.httpd = UnixThreadingHTTPServer(
            socket_path,
            handler_for_campaign_control(service, campaign),
            socket_mode=socket_mode,
        )
        self._thread: threading.Thread | None = None

    @property
    def socket_path(self) -> Path:
        return self.httpd._socket_path

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def start_background(self) -> None:
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=10)
        self.httpd.server_close()


__all__ = [
    "DelegationCampaignControlPlaneServer",
    "DelegationCampaignStartRequest",
    "DelegationCampaignView",
    "campaign_view",
    "handler_for_campaign_control",
]
