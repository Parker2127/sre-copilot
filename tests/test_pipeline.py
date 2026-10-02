"""End-to-end pipeline test: webhook -> enrichment -> heuristic diagnosis.

Runs with no cluster, no LLM key, no Slack — the graceful-degradation path.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, enricher
from app.main import app

SAMPLE = json.loads(
    (Path(__file__).parent.parent / "demo" / "sample-alert.json").read_text())


@pytest.fixture()
def client(monkeypatch):
    # Force demo mode regardless of ambient environment.
    monkeypatch.setattr(config.settings, "api_key", "")
    monkeypatch.setattr(config.settings, "llm_api_key", "")
    monkeypatch.setattr(config.settings, "slack_webhook_url", "")
    enricher.reset_clients_for_tests()
    return TestClient(app)


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_alert_pipeline_heuristic(client):
    resp = client.post("/alerts", json=SAMPLE)
    assert resp.status_code == 200
    body = resp.json()
    assert body["processed"] == 1
    result = body["results"][0]
    assert result["alert"] == "KubePodCrashLooping"

    diagnosis = result["diagnosis"]
    assert diagnosis["source"] == "heuristic"
    assert "crash" in diagnosis["summary"].lower()
    assert diagnosis["suggested_actions"], "should suggest concrete kubectl commands"
    assert diagnosis["runbook"], "should include remediation steps"

    # No cluster in test env -> enrichment records the failure, doesn't raise.
    assert any("kubernetes_unavailable" in e for e in result["context"]["errors"])


def test_resolved_alerts_skipped(client):
    payload = json.loads(json.dumps(SAMPLE))
    payload["alerts"][0]["status"] = "resolved"
    resp = client.post("/alerts", json=payload)
    assert resp.status_code == 200
    assert resp.json()["processed"] == 0
    assert resp.json()["skipped_resolved"] == 1


def test_auth_enforced_when_key_set(client, monkeypatch):
    monkeypatch.setattr(config.settings, "api_key", "s3cret")
    resp = client.post("/alerts", json=SAMPLE)
    assert resp.status_code == 401
    resp = client.post("/alerts", json=SAMPLE, headers={"X-API-Key": "s3cret"})
    assert resp.status_code == 200
