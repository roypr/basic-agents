"""LLM-powered summarizer utility.

Unlike the agent framework, this is a plain utility function —
no agent class, no tool-calling loop, no session database.
"""

from utils.llm_client import call_llm_streaming, get_adapter
from utils.provider_config import ProviderError, resolve_provider


SUMMARIZE_SYSTEM_PROMPT = (
    "You are a precise summarizer. Condense the following conversation "
    "into a dense factual summary preserving key decisions, file changes, "
    "unresolved issues, and current task state."
)


def summarize_history(
    messages: list[dict],
    *,
    provider: str | None = None,
    model: str | None = None,
    llm_base: str | None = None,
    api_key: str = "",
) -> str:
    """Summarize a conversation history using an LLM.

    Makes a single non-tool call to the given provider/model.
    The result is a condensed text summary suitable for use as a
    user message in a compressed session.

    Args:
        messages: The (already stripped) message list to summarize.
        provider: Provider name from providers.json.
        model: Model name for the chosen provider.
        llm_base: Override LLM base URL (bypasses providers.json).
        api_key: Override API key.

    Returns:
        The summary text.
    """
    if llm_base is not None:
        # Direct bypass — same pattern as main.py
        resolved_base = llm_base
        resolved_api_key = api_key
        resolved_model = model or "local"
    else:
        try:
            provider_cfg = resolve_provider(provider, model)
        except ProviderError as exc:
            raise ProviderError(
                f"Failed to resolve provider for summarization: {exc}"
            ) from exc
        resolved_base = provider_cfg.api_base_url
        resolved_api_key = provider_cfg.api_key
        resolved_model = provider_cfg.model

    adapter = get_adapter(resolved_model)

    # Build a one-shot payload: system instruction + user messages
    payload = [
        {"role": "system", "content": SUMMARIZE_SYSTEM_PROMPT},
    ]
    # Serialise the stripped messages into a single user message
    import json

    payload.append(
        {
            "role": "user",
            "content": json.dumps(messages, indent=2, ensure_ascii=False),
        }
    )

    result = call_llm_streaming(
        payload,
        model=resolved_model,
        llm_base=resolved_base,
        api_key=resolved_api_key,
        adapter=adapter,
        use_tools=False,
    )

    return adapter.extract_content(result)
