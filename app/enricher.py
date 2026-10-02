"""Enrich an alert with live Kubernetes context.

Every lookup is defensive: enrichment must never fail the request. Partial
context with a recorded error is always better than a 500 during an incident.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from .config import settings
from .models import Alert

log = logging.getLogger("sre-copilot.enricher")

_apps_v1 = None
_core_v1 = None
_k8s_load_error: str | None = None
_clients_initialized = False

# Label -> workload kind, checked in priority order.
WORKLOAD_LABEL_PRIORITY = [
    ("deployment", "Deployment"),
    ("statefulset", "StatefulSet"),
    ("daemonset", "DaemonSet"),
    ("job", "Job"),
    ("cronjob", "CronJob"),
]

MAX_PODS_INSPECTED = 10
MAX_EVENTS = 8
MAX_LOGGED_PODS = 3


def _clients():
    """Lazily build k8s API clients: in-cluster config first, then kubeconfig."""
    global _apps_v1, _core_v1, _k8s_load_error, _clients_initialized
    if _clients_initialized:
        return _apps_v1, _core_v1
    _clients_initialized = True
    try:
        from kubernetes import client
        from kubernetes import config as k8s_config

        try:
            k8s_config.load_incluster_config()
        except Exception:
            k8s_config.load_kube_config()
        _apps_v1 = client.AppsV1Api()
        _core_v1 = client.CoreV1Api()
    except Exception as exc:
        _k8s_load_error = f"{type(exc).__name__}: {exc}"
        log.warning("Kubernetes client unavailable: %s", _k8s_load_error)
    return _apps_v1, _core_v1


def reset_clients_for_tests() -> None:
    """Test helper: clear the cached clients so each test starts fresh."""
    global _apps_v1, _core_v1, _k8s_load_error, _clients_initialized
    _apps_v1 = _core_v1 = None
    _k8s_load_error = None
    _clients_initialized = False


def _detect_workload(alert: Alert) -> tuple[str | None, str | None]:
    for label_key, kind in WORKLOAD_LABEL_PRIORITY:
        name = alert.labels.get(label_key)
        if name:
            return kind, name
    return None, None


def _pod_summary(pod) -> dict[str, Any]:
    container_statuses = pod.status.container_statuses or []
    restarts = sum(cs.restart_count for cs in container_statuses)
    containers = []
    for cs in container_statuses:
        state = "unknown"
        reason = ""
        if cs.state.waiting:
            state, reason = "waiting", cs.state.waiting.reason or ""
        elif cs.state.terminated:
            state, reason = "terminated", cs.state.terminated.reason or ""
        elif cs.state.running:
            state = "running"
        containers.append(
            {"name": cs.name, "ready": cs.ready, "restarts": cs.restart_count,
             "state": state, "reason": reason}
        )
    return {
        "name": pod.metadata.name,
        "phase": pod.status.phase,
        "ready": all(c["ready"] for c in containers) if containers else False,
        "restarts": restarts,
        "node": pod.spec.node_name,
        "containers": containers,
    }


def _interesting_pods(core_v1, namespace: str, kind: str | None,
                      workload: str | None, pod_name: str) -> list:
    """Find pods related to the alert: the named pod, or pods of the workload."""
    if pod_name:
        try:
            return [core_v1.read_namespaced_pod(pod_name, namespace)]
        except Exception as exc:
            log.warning("Could not read pod %s/%s: %s", namespace, pod_name, exc)
    if workload:
        pods = core_v1.list_namespaced_pod(namespace, limit=100).items
        prefix = f"{workload}-"
        matched = [p for p in pods
                   if p.metadata.name == workload or p.metadata.name.startswith(prefix)]
        return matched[:MAX_PODS_INSPECTED]
    return []


def _recent_events(core_v1, namespace: str, names: list[str]) -> list[dict[str, Any]]:
    events = core_v1.list_namespaced_event(namespace, limit=200).items

    def _ts(e):
        return e.last_timestamp or e.metadata.creation_timestamp

    relevant = [e for e in events
                if e.involved_object and e.involved_object.name in names]
    relevant.sort(key=_ts, reverse=True)
    out = []
    for e in relevant[:MAX_EVENTS]:
        msg = e.message or ""
        out.append({
            "reason": e.reason,
            "type": e.type,
            "object": e.involved_object.name,
            "message": msg[:300],
            "last_seen": _ts(e).isoformat() if _ts(e) else None,
        })
    return out


def _pod_logs(core_v1, namespace: str, pods: list, container: str) -> dict[str, str]:
    """Tail logs for troubled pods only — healthy pods don't need log reads."""
    logs: dict[str, str] = {}
    candidates = [p for p in pods
                  if p.status.phase != "Running"
                  or sum((cs.restart_count or 0) for cs in (p.status.container_statuses or [])) > 3]
    for pod in candidates[:MAX_LOGGED_PODS]:
        name = pod.metadata.name
        try:
            kwargs: dict[str, Any] = {"tail_lines": settings.k8s_log_tail_lines}
            if container:
                kwargs["container"] = container
            logs[name] = core_v1.read_namespaced_pod_log(name, namespace, **kwargs)
        except Exception as exc:
            logs[name] = f"<log read failed: {type(exc).__name__}: {exc}>"
    return logs


def _deployment_status(apps_v1, namespace: str, name: str) -> dict[str, Any] | None:
    try:
        d = apps_v1.read_namespaced_deployment(name, namespace)
        s = d.status
        return {
            "desired": d.spec.replicas,
            "replicas": s.replicas,
            "ready": s.ready_replicas,
            "updated": s.updated_replicas,
            "unavailable": s.unavailable_replicas,
        }
    except Exception as exc:
        log.warning("Could not read deployment %s/%s: %s", namespace, name, exc)
        return None


def enrich(alert: Alert) -> dict[str, Any]:
    """Build the enrichment context for one alert. Never raises."""
    namespace = alert.namespace or settings.k8s_default_namespace
    context: dict[str, Any] = {
        "namespace": namespace,
        "workload": None,
        "pods": [],
        "events": [],
        "logs": {},
        "deployment_status": None,
        "errors": [],
    }
    apps_v1, core_v1 = _clients()
    if core_v1 is None:
        context["errors"].append(
            f"kubernetes_unavailable: {_k8s_load_error or 'client init failed'}")
        return context

    kind, workload = _detect_workload(alert)
    pod_name = alert.labels.get("pod", "")
    container = alert.labels.get("container", "")
    try:
        pods = _interesting_pods(core_v1, namespace, kind, workload, pod_name)
        if kind and workload:
            context["workload"] = {"kind": kind, "name": workload}
        context["pods"] = [_pod_summary(p) for p in pods]
        names = [p.metadata.name for p in pods]
        if workload:
            names.append(workload)
        context["events"] = _recent_events(core_v1, namespace, names)
        context["logs"] = _pod_logs(core_v1, namespace, pods, container)
        if kind == "Deployment" and workload and apps_v1 is not None:
            context["deployment_status"] = _deployment_status(apps_v1, namespace, workload)
    except Exception as exc:  # belt and braces: enrichment never 500s
        context["errors"].append(f"enrichment_failed: {type(exc).__name__}: {exc}")
        log.exception("Enrichment failed for alert %s", alert.fingerprint)
    return context


def summarize_for_prompt(context: dict[str, Any]) -> str:
    """Compact the context into text the LLM (or a human) can scan quickly."""
    lines = [f"namespace: {context['namespace']}"]
    if context["workload"]:
        w = context["workload"]
        lines.append(f"workload: {w['kind']}/{w['name']}")
    for p in context["pods"]:
        lines.append(
            f"pod {p['name']}: phase={p['phase']} ready={p['ready']} "
            f"restarts={p['restarts']} node={p['node']}")
        for c in p["containers"]:
            lines.append(
                f"  container {c['name']}: state={c['state']} reason={c['reason']} "
                f"restarts={c['restarts']}")
    if context["deployment_status"]:
        d = context["deployment_status"]
        lines.append(
            f"deployment: desired={d['desired']} ready={d['ready']} "
            f"updated={d['updated']} unavailable={d['unavailable']}")
    for e in context["events"]:
        lines.append(f"event [{e['type']}] {e['reason']} on {e['object']}: {e['message']}")
    for pod_name, tail in context["logs"].items():
        snippet = tail[-1500:] if len(tail) > 1500 else tail
        lines.append(f"logs {pod_name} (tail):\n{snippet}")
    for err in context["errors"]:
        lines.append(f"enrichment_error: {err}")
    return "\n".join(lines)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
