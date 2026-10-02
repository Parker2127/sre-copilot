# Demo guide — SRE Copilot

Two ways to see it work: a 2-minute local run (no cluster), and the full
Kubernetes run on `kind` with a real crash-looping pod.

## Option A: local, no cluster (2 minutes)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# terminal 1
uvicorn app.main:app --port 8080

# terminal 2
python demo/send-test-alert.py
```

You'll get the heuristic diagnosis for a `KubePodCrashLooping` alert. Enrichment
reports `kubernetes_unavailable` (there's no cluster) instead of failing — that's
the graceful-degradation path, and it's intentional.

## Option B: full run on kind (~15 minutes)

Prerequisites: `kind`, `kubectl`, `helm`, Docker.

```bash
# 1. Cluster
kind create cluster --name sre-demo

# 2. Build + load the image
docker build -t sre-copilot:demo .
kind load docker-image sre-copilot:demo --name sre-demo

# 3. Deploy (heuristic mode; no secrets needed)
helm install sre-copilot ./helm/sre-copilot \
  --namespace monitoring --create-namespace \
  --set image.repository=sre-copilot --set image.tag=demo \
  --set image.pullPolicy=IfNotPresent

kubectl -n monitoring wait --for=condition=available deploy/sre-copilot --timeout=120s

# 4. Create a crash-looping victim app
kubectl create namespace payments
kubectl -n payments run api --image=busybox -- sh -c 'echo boom; exit 1'
# (busybox exits immediately -> CrashLoopBackOff within a minute)

# 5. Fire the webhook through a port-forward
kubectl -n monitoring port-forward svc/sre-copilot 8080:8080 &
python demo/send-test-alert.py
```

This time the response includes **live enrichment**: the crash-looping pod's
phase, restart count, container state/reason, recent events, and the tail of the
pod's logs — all collected from the kind cluster's API.

## Option C: with a real LLM

```bash
export LLM_API_KEY="sk-..."          # or a local Ollama/vLLM endpoint:
export LLM_BASE_URL="http://localhost:11434/v1"
export LLM_MODEL="llama3.1"
python demo/send-test-alert.py        # diagnosis.source is now "llm"
```

With Helm: `--set secrets.llmApiKey="$LLM_API_KEY"`.

## Wiring real Prometheus alerts

1. Apply `prometheus/alert-rules.yaml` (PrometheusRule CRD, kube-prometheus-stack).
2. Add the receiver from `prometheus/alertmanager.yaml` to your Alertmanager config,
   adjusting the service URL to your release name/namespace.
3. Deploy a bad image on purpose and watch Slack (set `SLACK_WEBHOOK_URL`).

## Cleaning up

```bash
kind delete cluster --name sre-demo
```
