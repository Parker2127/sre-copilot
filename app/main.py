"""FastAPI service: Alertmanager webhook receiver + diagnosis pipeline."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Header, HTTPException

from . import __version__
from .config import settings
from .enricher import enrich
from .llm import diagnose
from .models import AlertmanagerPayload
from .notifier import notify

logging.basicConfig(level=settings.log_level.upper(),
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("sre-copilot")

app = FastAPI(title="SRE Copilot", version=__version__,
              description="AI-assisted on-call companion: alert -> K8s context -> diagnosis -> Slack")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/")
def index() -> dict[str, Any]:
    return {
        "service": "sre-copilot",
        "version": __version__,
        "webhook": "POST /alerts",
        "llm_mode": "llm" if settings.llm_enabled else "heuristic",
        "auth": "enabled" if settings.auth_enabled else "disabled",
    }


def _authorized(x_api_key: str | None, authorization: str | None) -> bool:
    if not settings.auth_enabled:
        return True
    if x_api_key and x_api_key == settings.api_key:
        return True
    return authorization == f"Bearer {settings.api_key}"


@app.post("/alerts")
def receive_alerts(payload: AlertmanagerPayload,
                   x_api_key: str | None = Header(default=None),
                   authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not _authorized(x_api_key, authorization):
        raise HTTPException(status_code=401, detail="invalid or missing API key")

    results: list[dict[str, Any]] = []
    skipped_resolved = 0
    for alert in payload.alerts:
        if alert.status == "resolved":
            skipped_resolved += 1
            continue
        log.info("Processing firing alert %s (%s)", alert.name, alert.fingerprint[:12])
        context = enrich(alert)
        diagnosis = diagnose(alert, context)
        notify(alert, diagnosis)
        results.append({
            "alert": alert.name,
            "severity": alert.severity,
            "fingerprint": alert.fingerprint,
            "context": context,
            "diagnosis": diagnosis,
        })
    return {"processed": len(results), "skipped_resolved": skipped_resolved,
            "results": results}
