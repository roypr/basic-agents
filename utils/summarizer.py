"""LLM-powered summarizer utility.

Unlike the agent framework, this is a plain utility function —
no agent class, no tool-calling loop, no session database.
"""

from utils.llm_client import call_llm_streaming, get_adapter
from utils.provider_config import ProviderError, resolve_provider


SUMMARIZE_SYSTEM_PROMPT = (
    "You are a precise summarizer. You are presented with a list of messages between User and AI Assistant."
    "Starts with User asking the AI Assistant to do a task, then Assistant uses tool calls to read files, understand the task, execute it using more tools."
    "You shall not see the tool calls and results of those. Occassionally you shall see reference to tools by name."
    "Your task is very precise. Try not to overthink."
    "Absolutely critical: Do not invent, do not assume. Act only on basis of the information provided."
    "You shall produce a snapshot of current state, not a timeline. How things happened is not important."
    "First try to extract and summarize Assistant's understanding of the overall project."
    "Then extract and summarize the tasks it attempted to do, the goals, and current status of each task / goal."
    "If you see mention of file changes, track and summarize the changes made to each file. Mention function names, what was achieved in plain english." 
    "Mention unfinished tasks in more detail than finished ones, which are to be passed on to assistant again or looked at by experts."
    "You might find a summary of the tasks completed in the end of the conversation. Do not copy paste it, but use it to create your response. Your response should follow"
    "structure stated above."
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
