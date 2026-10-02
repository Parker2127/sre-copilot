"""Tests for the Alertmanager payload models."""
import json
from pathlib import Path

from app.models import AlertmanagerPayload

SAMPLE = Path(__file__).parent.parent / "demo" / "sample-alert.json"


def test_sample_payload_parses():
    payload = AlertmanagerPayload(**json.loads(SAMPLE.read_text()))
    assert len(payload.alerts) == 1
    alert = payload.alerts[0]
    assert alert.name == "KubePodCrashLooping"
    assert alert.severity == "critical"
    assert alert.namespace == "payments"
    assert alert.status == "firing"
    assert alert.fingerprint


def test_tolerant_of_extra_fields():
    payload = AlertmanagerPayload(alerts=[{
            "status": "firing",
            "labels": {"alertname": "SomethingNew", "custom": "whatever"},
            "annotations": {"summary": "x"},
            "startsAt": "2026-09-30T12:00:00Z",
            "someFutureField": {"nested": True},
        }])
    assert payload.alerts[0].name == "SomethingNew"
