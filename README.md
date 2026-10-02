# SRE Copilot

An AI-assisted on-call companion for Kubernetes. It receives Prometheus/Alertmanager
webhooks, enriches every alert with live cluster context (pods, events, logs,
workload status), asks an LLM for a diagnosis and remediation plan, and posts the
result to Slack — in seconds, while you're still opening your laptop.

Built as a portfolio project by [Shrikar Kaduluri](https://github.com/Parker2127).

## How it works

```
Prometheus ──▶ Alertmanager ──▶ SRE Copilot ──▶ Slack
                                │  /alerts
                                ├─ 1. Parse alert (alertname, severity, namespace, workload)
                                ├─ 2. Enrich from the Kubernetes API:
                                │     pods, restarts, events, container logs, deployment status
                                ├─ 3. Diagnose:
                                │     LLM (any OpenAI-compatible API) ──or── heuristic fallback
                                └─ 4. Notify: structured Slack message + JSON log
```

No LLM API key? No problem. The service ships with a deterministic heuristic engine,
so the full pipeline — webhook → enrichment → diagnosis → Slack — works out of the
box. Set `LLM_API_KEY` and it upgrades to LLM-generated diagnoses automatically.

## Quickstart (local demo, no cluster needed)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# terminal 1 — run the service (enrichment degrades gracefully without a cluster)
uvicorn app.main:app --port 8080

# terminal 2 — fire a sample Alertmanager webhook
python demo/send-test-alert.py
```

See [DEMO.md](DEMO.md) for the full Kubernetes demo on `kind`, including a live
CrashLoopBackOff scenario.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8080` | HTTP port |
| `API_KEY` | _(empty = auth off)_ | Shared secret; when set, `/alerts` requires `X-API-Key` or `Authorization: Bearer` |
| `K8S_DEFAULT_NAMESPACE` | `default` | Namespace used when the alert has none |
| `K8S_LOG_TAIL_LINES` | `50` | Log lines fetched per troubled pod |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible endpoint (OpenAI, Azure OpenAI, Ollama, vLLM) |
| `LLM_MODEL` | `gpt-4o-mini` | Model name for diagnosis |
| `LLM_API_KEY` | _(empty = heuristic mode)_ | Enables LLM diagnosis when set |
| `SLACK_WEBHOOK_URL` | _(empty = log only)_ | Incoming Slack webhook for notifications |
| `LOG_LEVEL` | `INFO` | Python log level |

## Repository layout

```
app/                  FastAPI service (receiver, enricher, LLM, notifier)
helm/sre-copilot/     Helm chart (deployment, RBAC, config, secret)
prometheus/           Example alert rules + Alertmanager routing to the copilot
terraform/            AKS cluster (resource group, VNet, node pool)
gitops/               Argo CD Application manifest
.github/workflows/    CI: lint, test, build/push image, helm lint, terraform validate
demo/                 Sample Alertmanager payload + sender script
```

## Design notes

- **Enrichment never fails the request.** Every Kubernetes lookup is defensive; partial
  context with a recorded error beats a 500 during an incident.
- **Runs as non-root**, with liveness/readiness probes and least-privilege RBAC
  (read-only on pods, logs, events, workloads).
- **Resolved alerts are acknowledged and skipped** — no noise for recoveries.
- LLM output is requested as strict JSON and validated before use; any LLM failure
  falls back to heuristics so paging never depends on an API.

## Roadmap

- Vector-store lookup of past incidents for "have we seen this before?" context
- Auto-generated runbook PRs from repeated diagnoses
- MS Teams / PagerDuty notifiers next to Slack
