from pathlib import Path
from db.session_db import SessionDB


SYSTEM_PROMPT = "You are a helpful assistant."


def load_system_prompt(prompt_file: str = None) -> str:
    if not prompt_file:
        return SYSTEM_PROMPT

    prompt_path = Path(prompt_file)
    if not prompt_path.exists():
        print(
            f"Warning: System prompt file '{prompt_file}' not found. Using default prompt."
        )
        return SYSTEM_PROMPT

    try:
        return prompt_path.read_text(encoding="utf-8").strip()
    except Exception as e:
        print(
            f"Warning: Could not read system prompt file '{prompt_file}': {e}. Using default prompt."
        )
        return SYSTEM_PROMPT


def init_session_db(
    resume_session: int | None = None,
    session_name: str = "Default Session",
    system_prompt: str = "",
    agent_name: str = "",
):
    """Initialise or resume a session.

    Args:
        resume_session: Session ID to resume, or None to create a new one.
        session_name: Name for a new session.
        system_prompt: System prompt (not persisted — kept for compatibility).
        agent_name: Name of the agent (stored for switch detection).

    Returns:
        The session ID (int).
    """
    session_db = SessionDB()

    if resume_session:
        session = session_db.get_session(resume_session)
        if session:
            print(
                f"[Session] Resuming session '{session['name']}' (ID: {resume_session})"
            )
            return resume_session
        print(f"[Warning] Session {resume_session} not found, creating a new session")

    session_id = session_db.create_session(
        session_name, system_prompt, agent_name=agent_name
    )
    print(f"[Session] Created new session '{session_name}' (ID: {session_id})")
    return session_id
