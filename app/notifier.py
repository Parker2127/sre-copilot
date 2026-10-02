"""Notifications: structured Slack message, always backed by a JSON log line."""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from . import __version__
from .config import settings
from .models import Alert

log = logging.getLogger("sre-copilot.notifier")

SEVERITY_EMOJI = {"critical": ":rotating_light:", "warning": ":warning:"}


def _slack_payload(alert: Alert, diagnosis: dict[str, Any]) -> dict[str, Any]:
    emoji = SEVERITY_EMOJI.get(alert.severity, ":information_source:")
    actions = "\n".join(f"{i + 1}. {a}" for i, a in enumerate(diagnosis.get("suggested_actions", [])[:5]))
    source = diagnosis.get("source", "unknown")
    return {
        "text": f"{emoji} {alert.severity.upper()} {alert.name} ({alert.namespace or 'no namespace'})",
        "blocks": [
            {"type": "header",
             "text": {"type": "plain_text",
                      "text": f"{emoji} {alert.name}", "emoji": True}},
            {"type": "section",
             "fields": [
                 {"type": "mrkdwn", "text": f"*Severity:*\n{alert.severity}"},
                 {"type": "mrkdwn", "text": f"*Namespace:*\n{alert.namespace or '—'}"},
                 {"type": "mrkdwn", "text": f"*Status:*\n{alert.status}"},
                 {"type": "mrkdwn", "text": f"*Diagnosis:*\n{source}"},
             ]},
            {"type": "section",
             "text": {"type": "mrkdwn", "text": f"*Summary*\n{diagnosis.get('summary', '')}"}},
            {"type": "section",
             "text": {"type": "mrkdwn", "text": f"*Suggested actions*\n{actions or '—'}"}},
            {"type": "context",
             "elements": [{"type": "mrkdwn",
                           "text": f"sre-copilot v{__version__} · fingerprint `{alert.fingerprint[:12]}`"}]},
        ],
    }


def notify(alert: Alert, diagnosis: dict[str, Any]) -> None:
    """Log the full result as JSON; post to Slack when configured. Never raises."""
    record = {
        "event": "alert_diagnosed",
        "alert": alert.name,
        "severity": alert.severity,
        "namespace": alert.namespace,
        "fingerprint": alert.fingerprint,
        "diagnosis_source": diagnosis.get("source"),
        "summary": diagnosis.get("summary"),
    }
    log.info(json.dumps(record))
    if not settings.slack_webhook_url:
        return
    try:
        resp = httpx.post(settings.slack_webhook_url,
                          json=_slack_payload(alert, diagnosis), timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        log.warning("Slack notification failed: %s: %s", type(exc).__name__, exc)
