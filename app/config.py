"""Runtime configuration, loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    # Web server
    host: str = field(default_factory=lambda: _get("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(_get("PORT", "8080")))
    # Optional shared secret for the webhook. Empty = auth disabled (demo mode).
    api_key: str = field(default_factory=lambda: _get("API_KEY", ""))

    # Kubernetes enrichment
    k8s_default_namespace: str = field(default_factory=lambda: _get("K8S_DEFAULT_NAMESPACE", "default"))
    k8s_log_tail_lines: int = field(default_factory=lambda: int(_get("K8S_LOG_TAIL_LINES", "50")))
    k8s_timeout_seconds: int = field(default_factory=lambda: int(_get("K8S_TIMEOUT_SECONDS", "8")))

    # LLM diagnosis — any OpenAI-compatible endpoint (OpenAI, Azure OpenAI, Ollama, vLLM).
    llm_base_url: str = field(default_factory=lambda: _get("LLM_BASE_URL", "https://api.openai.com/v1"))
    llm_model: str = field(default_factory=lambda: _get("LLM_MODEL", "gpt-4o-mini"))
    llm_api_key: str = field(default_factory=lambda: _get("LLM_API_KEY", ""))
    llm_timeout_seconds: int = field(default_factory=lambda: int(_get("LLM_TIMEOUT_SECONDS", "30")))

    # Notifications
    slack_webhook_url: str = field(default_factory=lambda: _get("SLACK_WEBHOOK_URL", ""))

    log_level: str = field(default_factory=lambda: _get("LOG_LEVEL", "INFO"))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_key)


settings = Settings()
