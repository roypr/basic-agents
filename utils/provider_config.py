"""Provider configuration loader.

Reads ``providers.json`` from the project root and resolves a provider+model
into ``(api_base_url, api_key, model)`` for use with the LLM client.

The config is intentionally lightweight — no router, fallback, or transformer
logic. It is loaded dynamically at runtime and is never persisted to the
session database.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


CONFIG_PATH = Path(__file__).resolve().parent.parent / "providers.json"


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    api_base_url: str
    api_key: str
    model: str


class ProviderError(ValueError):
    """Raised when a provider or model cannot be resolved."""


def load_providers() -> dict:
    if not CONFIG_PATH.exists():
        raise ProviderError(f"providers.json not found at {CONFIG_PATH}")
    try:
        with CONFIG_PATH.open(encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"providers.json is not valid JSON: {exc}") from exc


def list_providers() -> list[str]:
    return [p["name"] for p in load_providers().get("providers", [])]


def list_models(provider: Optional[str] = None) -> dict[str, list[str]]:
    data = load_providers()
    result: dict[str, list[str]] = {}
    for p in data.get("providers", []):
        if provider and p["name"] != provider:
            continue
        result[p["name"]] = list(p.get("models", []))
    return result


def resolve_provider(
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> ProviderConfig:
    """Resolve a (provider, model) pair into a :class:`ProviderConfig`.

    Falls back to ``default_provider`` / ``default_model`` from the config when
    either argument is omitted.
    """
    data = load_providers()
    providers = data.get("providers", [])
    if not providers:
        raise ProviderError("No providers defined in providers.json")

    default_provider = data.get("default_provider")
    default_model = data.get("default_model")

    provider_name = provider or default_provider
    if not provider_name:
        raise ProviderError(
            "No provider given and no default_provider set in providers.json"
        )

    selected = next((p for p in providers if p["name"] == provider_name), None)
    if selected is None:
        available = ", ".join(p["name"] for p in providers)
        raise ProviderError(
            f"Provider '{provider_name}' not found. Available: {available}"
        )

    model_name = model or default_model
    if not model_name:
        # Fall back to the first model the provider exposes.
        models = selected.get("models", [])
        if not models:
            raise ProviderError(
                f"Provider '{provider_name}' has no models configured"
            )
        model_name = models[0]

    if model_name not in selected.get("models", []):
        available = ", ".join(selected.get("models", []))
        raise ProviderError(
            f"Model '{model_name}' not available for provider "
            f"'{provider_name}'. Available: {available}"
        )

    return ProviderConfig(
        name=selected["name"],
        api_base_url=selected["api_base_url"],
        api_key=selected.get("api_key", ""),
        model=model_name,
    )
