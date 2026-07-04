"""History compression: strip tool artifacts from session history.

Phase 1 of the upgrade plan. Pure functions with no side effects
(other than file I/O in export_raw_messages and compress_session).
"""

import json
from pathlib import Path
from typing import Optional
from db.session_db import SessionDB


def strip_message(msg: dict) -> Optional[dict]:
    """Strip a single message for compression.

    - tool messages -> return None (remove entirely)
    - assistant messages -> keep role + content, drop tool_calls
    - user/system messages -> keep as-is

    Args:
        msg: A message dict from SessionDB.get_messages() format.

    Returns:
        Cleaned message dict, or None if the message should be removed.
    """
    role = msg.get("role")

    if role == "tool":
        return None

    if role == "assistant":
        return {
            "role": "assistant",
            "content": msg.get("content", ""),
        }

    # system and user pass through unchanged
    return dict(msg)


def strip_history(messages: list) -> list:
    """Strip tool artifacts from a list of messages.

    Applies strip_message to each message and filters out Nones.

    Args:
        messages: List of message dicts from SessionDB.get_messages().

    Returns:
        Cleaned list containing only user, system, and stripped assistant messages.
    """
    return [
        stripped for msg in messages if (stripped := strip_message(msg)) is not None
    ]


def export_raw_messages(
    session_id: int,
    messages: list,
    output_dir: str = "logs",
) -> str:
    """Export full messages list (including tool artifacts) to a JSON log file.

    Args:
        session_id: The session ID (used for filename).
        messages: The raw message list to export.
        output_dir: Directory to write the log file into.

    Returns:
        The absolute path to the exported file.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    filename = output_path / f"session_{session_id}_raw.json"

    export_data = {
        "session_id": session_id,
        "message_count": len(messages),
        "messages": messages,
    }

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(export_data, f, indent=2, ensure_ascii=False)

    return str(filename)


def compress_session(
    db: SessionDB,
    session_id: int,
    output_dir: str = "logs",
) -> Optional[int]:
    """Compress a session by stripping tool artifacts and creating a new session.

    Order: export raw -> strip -> create new session.
    If creation fails, the raw export already exists as a fallback.

    Args:
        db: A SessionDB instance.
        session_id: The session to compress.
        output_dir: Directory to write raw export logs.

    Returns:
        The new session ID, or None if the original session was not found.
    """
    # 1. Get raw messages + session info
    session = db.get_session(session_id)
    if not session:
        print(f"[Error] Session {session_id} not found.")
        return None

    raw_messages = db.get_messages(session_id)
    session_name = session["name"]
    # sqlite3.Row supports .keys() — check column existence gracefully
    agent_name = session["agent_name"] if "agent_name" in session.keys() else ""

    # 2. Export raw messages to log
    export_path = export_raw_messages(session_id, raw_messages, output_dir)

    # 3. Strip history
    stripped = strip_history(raw_messages)

    # 4. Create new session (system prompt comes from agent, not DB)
    new_session_id = db.create_session(
        name=f"{session_name} (compressed from {session_id})",
        system_prompt="",
        agent_name=agent_name,
    )

    # 5. Add stripped messages to new session
    for msg in stripped:
        # 'content' is guaranteed to be present after strip_message
        content = msg.get("content", "")
        if content:
            db.add_message(
                new_session_id,
                role=msg["role"],
                content=content,
            )

    print(f"[Compress] Raw messages exported to {export_path}")
    print(f"[Compress] Compressed session created with ID: {new_session_id}")
    print(f"[Compress] Original session {session_id} preserved unchanged")

    return new_session_id
