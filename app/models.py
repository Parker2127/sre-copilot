"""Pydantic models for the Alertmanager webhook payload (v4)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Alert(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    status: str = "firing"
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime | None = Field(default=None, alias="startsAt")
    ends_at: datetime | None = Field(default=None, alias="endsAt")
    generator_url: str = Field(default="", alias="generatorURL")
    fingerprint: str = ""

    @property
    def name(self) -> str:
        return self.labels.get("alertname", "UnknownAlert")

    @property
    def severity(self) -> str:
        return self.labels.get("severity", "unknown")

    @property
    def namespace(self) -> str:
        return self.labels.get("namespace", "")


class AlertmanagerPayload(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    version: str = "4"
    group_key: str = Field(default="", alias="groupKey")
    status: str = "firing"
    receiver: str = ""
    group_labels: dict[str, str] = Field(default_factory=dict, alias="groupLabels")
    common_labels: dict[str, str] = Field(default_factory=dict, alias="commonLabels")
    common_annotations: dict[str, str] = Field(default_factory=dict, alias="commonAnnotations")
    external_url: str = Field(default="", alias="externalURL")
    alerts: list[Alert] = Field(default_factory=list)
