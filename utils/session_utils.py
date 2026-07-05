"""Session management helpers — importable functions, no CLI entry point.

All session-management logic formerly behind ``session_utils.py`` CLI
is now dispatched from ``main.py`` via the ``session`` subcommand.
"""

import json
from datetime import datetime
from db.session_db import SessionDB
from utils.history_compressor import compress_session as compress_session_fn
from utils.summarizer import summarize_history


def file_write(path: str, data: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)


def create_session(name: str, system_prompt: str = "") -> int:
    db = SessionDB()
    session_id = db.create_session(name, system_prompt)
    print(f"Session created with ID: {session_id}")
    return session_id


def list_sessions():
    db = SessionDB()
    sessions = db.get_sessions()
    if not sessions:
        print("No active sessions found.")
        return
    print("ID\tName\tCreated\tUpdated\tSystem Prompt")
    print("--\t----\t-------\t-------\t-------------")
    for session in sessions:
        prompt = session["system_prompt"] or ""
        truncated = prompt[:50] + ("..." if len(prompt) > 50 else "")
        print(
            f"{session['id']}\t{session['name']}\t{session['created_at']}\t{session['updated_at']}\t{truncated}"
        )


def get_session(session_id: int):
    db = SessionDB()
    session = db.get_session(session_id)
    if not session:
        print(f"Session {session_id} not found or inactive.")
        return
    messages = db.get_messages(session_id)
    session_data = {
        "session_id": session["id"],
        "name": session["name"],
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "system_prompt": session["system_prompt"],
        "messages": messages,
    }
    filename = f"logs/session_{session_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    file_write(filename, json.dumps(session_data, indent=2))
    print(f"Session data saved to {filename}")


def delete_session(session_id: int):
    db = SessionDB()
    success = db.delete_session(session_id)
    if success:
        print(f"Session {session_id} marked as deleted.")
    else:
        print(f"Session {session_id} not found or already deleted.")


def compress_session_cmd(
    session_id: int,
    output_dir: str = "logs",
    summarize: bool = False,
    provider: str | None = None,
    model: str | None = None,
    llm_base: str | None = None,
    api_key: str = "",
) -> int:
    """Compress a session by stripping tool artifacts, optionally with summarization.

    Args:
        session_id: The session to compress.
        output_dir: Directory for raw export.
        summarize: If True, use LLM summarization on stripped messages.
        provider: Provider name for summarization.
        model: Model name for summarization.
        llm_base: Override LLM base URL for summarization.
        api_key: Override API key for summarization.

    Returns:
        0 on success, 1 on failure.
    """
    db = SessionDB()
    session = db.get_session(session_id)
    if not session:
        print(f"Session {session_id} not found.")
        return 1

    new_id = compress_session_fn(db, session_id, output_dir)
    if new_id is None:
        print(f"Failed to compress session {session_id}.")
        return 1

    if summarize:
        # Re-read the compressed session's messages (already stripped)
        compressed_messages = db.get_messages(new_id)
        # Filter out messages with empty content before summarization
        compressed_messages = [m for m in compressed_messages if m.get("content", "")]
        print(f"[Summarize] Summarizing {len(compressed_messages)} messages...")
        try:
            summary = summarize_history(
                compressed_messages,
                provider=provider,
                model=model,
                llm_base=llm_base,
                api_key=api_key,
            )
        except Exception as exc:
            print(f"[Summarize] Failed: {exc}")
            return 1

        # Update the compressed session: remove existing messages,
        # keep system prompt from original, add summary as single user message.
        # We'll create a new session from scratch with just 2 messages.
        agent_name = session["agent_name"] if "agent_name" in session.keys() else ""
        final_id = db.create_session(
            name=f"{session['name']} (summarized from {session_id})",
            system_prompt="",
            agent_name=agent_name,
        )
        # Add system message + summary as user message
        # db.add_message(
        #     final_id, role="system", content=""
        # )  # placeholder — agent fills at runtime
        db.add_message(final_id, role="user", content=summary)
        print(f"[Summarize] Summarized session created with ID: {final_id}")
        return 0

    return 0
