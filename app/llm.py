"""LLM-backed diagnosis, with a deterministic heuristic fallback.

When LLM_API_KEY is unset the service still works end-to-end: it returns a
clearly-labelled heuristic analysis, so demos never need a paid key and paging
never depends on an external API.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .config import settings
from .enricher import summarize_for_prompt
from .models import Alert

log = logging.getLogger("sre-copilot.llm")

SYSTEM_PROMPT = """You are an on-call SRE assistant. Given a Prometheus alert and live
Kubernetes context, produce a diagnosis for the engineer being paged.

Rules:
- Be specific and terse. No filler, no generic advice like "check the logs"
  (the logs are already in the context — cite what you see in them).
- Distinguish what the evidence shows from what you are guessing.
- Prefer the smallest safe remediation first.

Respond with strict JSON only, exactly these keys:
{
  "summary": "one or two sentences: what is happening",
  "likely_causes": ["ranked, most likely first"],
  "suggested_actions": ["concrete kubectl commands or checks, ordered by safety"],
  "runbook": ["numbered remediation steps, first step is always verify/confirm"],
  "severity_assessment": "one sentence: is the alert severity right, or over/under-stated?",
  "confidence": "low | medium | high"
}"""

# Heuristic knowledge base for well-known alerts. Used when no LLM key is
# configured, and as the fallback if the LLM call fails.
HEURISTICS: dict[str, dict[str, Any]] = {
    "KubePodCrashLooping": {
        "summary": "A pod is crash-looping: its container starts, fails, and gets restarted repeatedly.",
        "likely_causes": [
            "Application crash on startup (bad config, missing env var/secret, failed dependency)",
            "Liveness probe killing a slow-starting container",
            "OOMKilled — container exceeding its memory limit",
        ],
        "suggested_actions": [
            "kubectl logs <pod> --previous -n <namespace>  # logs from the crashed instance",
            "kubectl describe pod <pod> -n <namespace>      # check events: OOMKilled vs Error vs probe failures",
            "kubectl get events -n <namespace> --sort-by=.lastTimestamp | tail -20",
        ],
        "runbook": [
            "Confirm scope: is it one pod or all pods of the deployment?",
            "Read the previous container's logs — the crash reason is usually in the last 20 lines.",
            "If OOMKilled: raise the memory limit, or fix the leak if usage grows unbounded.",
            "If config/secret related: verify the mounted values, then rollout restart.",
            "If it started after a deploy: kubectl rollout undo deployment/<name> -n <namespace>.",
        ],
    },
    "KubeDeploymentReplicasMismatch": {
        "summary": "A deployment's available replicas don't match desired — the rollout is stuck or degraded.",
        "likely_causes": [
            "New pods failing readiness probes during a rollout",
            "Image pull failures (wrong tag, registry auth, rate limiting)",
            "Insufficient cluster resources (pending pods)",
        ],
        "suggested_actions": [
            "kubectl rollout status deployment/<name> -n <namespace>",
            "kubectl get pods -n <namespace> -l app=<name>  # look for Pending / ImagePullBackOff",
            "kubectl describe pod <pending-pod> -n <namespace>",
        ],
        "runbook": [
            "Identify which pods aren't ready and why (events tell you).",
            "ImagePullBackOff: fix the image tag or registry secret, then rollout restart.",
            "Pending on resources: scale the node pool or reduce requests.",
            "If the new revision is bad: kubectl rollout undo deployment/<name> -n <namespace>.",
        ],
    },
    "TargetDown": {
        "summary": "Prometheus can't scrape a target — monitoring is blind for that workload.",
        "likely_causes": [
            "The target pods are down or being restarted",
            "Service selector / endpoints misconfigured after a deploy",
            "NetworkPolicy blocking scrape traffic",
        ],
        "suggested_actions": [
            "kubectl get endpoints <service> -n <namespace>  # are there backing pods?",
            "kubectl get pods -n <namespace> -l <selector>",
            "Check ServiceMonitor/PodMonitor selectors match the service labels.",
        ],
        "runbook": [
            "Verify whether the workload itself is healthy (this may be monitoring-only).",
            "Fix endpoints or selectors so scrapes resume.",
            "If a NetworkPolicy changed recently, allow the Prometheus namespace.",
        ],
    },
    "HttpHighErrorRate": {
        "summary": "Elevated 5xx rate on an HTTP service — users are seeing failures.",
        "likely_causes": [
            "Bad deploy (new revision throwing exceptions)",
            "Downstream dependency outage (database, external API)",
            "Resource exhaustion (CPU throttling, connection pool saturation)",
        ],
        "suggested_actions": [
            "Check which endpoints/status codes spiked in Grafana.",
            "kubectl logs -l app=<name> -n <namespace> --tail=200 | grep -i 'error\\|exception'",
            "Correlate with deploy time: did errors start at a rollout?",
        ],
        "runbook": [
            "If errors correlate with a deploy: rollback first, investigate second.",
            "If a downstream is down: fail over or enable cached/degraded mode.",
            "Scale out if saturation, then find the bottleneck.",
        ],
    },
}

GENERIC_HEURISTIC = {
    "summary": "An alert fired; live context was collected but no specific heuristic matches this alert name.",
    "likely_causes": ["See the collected pod status, events, and log tail for the failing component."],
    "suggested_actions": [
        "kubectl get pods -n <namespace> | grep -v Running",
        "kubectl get events -n <namespace> --sort-by=.lastTimestamp | tail -20",
    ],
    "runbook": [
        "Confirm the blast radius from the pod and event data above.",
        "Check the log tail for the first error — fix forward or roll back.",
        "Add this alert's pattern to the heuristic knowledge base once resolved.",
    ],
}


def _heuristic(alert: Alert, context: dict[str, Any]) -> dict[str, Any]:
    base = HEURISTICS.get(alert.name, GENERIC_HEURISTIC)
    troubled = [p["name"] for p in context["pods"]
                if p["phase"] != "Running" or p["restarts"] > 5]
    diagnosis = {
        "summary": base["summary"],
        "likely_causes": list(base["likely_causes"]),
        "suggested_actions": list(base["suggested_actions"]),
        "runbook": list(base["runbook"]),
        "severity_assessment": (
            f"Alert severity is '{alert.severity}'. "
            + ("Troubled pods observed in context — severity looks justified."
               if troubled else "No troubled pods in current context — may already be recovering; verify before acting.")
        ),
        "confidence": "medium" if alert.name in HEURISTICS else "low",
        "troubled_pods": troubled,
        "source": "heuristic",
        "note": "Heuristic analysis (no LLM_API_KEY configured). Set LLM_API_KEY for LLM-generated diagnosis.",
    }
    return diagnosis


def _llm_diagnose(alert: Alert, context: dict[str, Any]) -> dict[str, Any]:
    user_prompt = (
        f"Alert: {alert.name}\n"
        f"Severity: {alert.severity}\n"
        f"Status: {alert.status}\n"
        f"Summary annotation: {alert.annotations.get('summary', '')}\n"
        f"Description annotation: {alert.annotations.get('description', '')}\n\n"
        f"Live Kubernetes context:\n{summarize_for_prompt(context)}"
    )
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    resp = httpx.post(
        url,
        headers={"Authorization": f"Bearer {settings.llm_api_key}"},
        json={
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        },
        timeout=settings.llm_timeout_seconds,
    )
    resp.raise_for_status()
    data = json.loads(resp.json()["choices"][0]["message"]["content"])
    data["source"] = "llm"
    data["model"] = settings.llm_model
    return data


def diagnose(alert: Alert, context: dict[str, Any]) -> dict[str, Any]:
    """Return a diagnosis dict. Falls back to heuristics on any LLM failure."""
    if settings.llm_enabled:
        try:
            return _llm_diagnose(alert, context)
        except Exception as exc:
            log.warning("LLM diagnosis failed (%s); using heuristic fallback", exc)
            result = _heuristic(alert, context)
            result["llm_error"] = f"{type(exc).__name__}: {exc}"
            return result
    return _heuristic(alert, context)
