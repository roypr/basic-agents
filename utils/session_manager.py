from pathlib import Path
from db.session_db import SessionDB


SYSTEM_PROMPT = "You are a helpful assistant."


def load_system_prompt(prompt_file: str = None) -> str:
    if not prompt_file:
        return SYSTEM_PROMPT

    prompt_path = Path(prompt_file)
    if not prompt_path.exists():
        print(f"Warning: System prompt file '{prompt_file}' not found. Using default prompt.")
        return SYSTEM_PROMPT

    try:
        return prompt_path.read_text(encoding="utf-8").strip()
    except Exception as e:
        print(f"Warning: Could not read system prompt file '{prompt_file}': {e}. Using default prompt.")
        return SYSTEM_PROMPT


def init_session_db(resume_session: int = None, session_name: str = "Default Session", system_prompt: str = ""):
    session_db = SessionDB()

    if resume_session:
        session = session_db.get_session(resume_session)
        if session:
            print(f"[Session] Resuming session '{session['name']}' (ID: {resume_session})")
            return resume_session, session['system_prompt'] or SYSTEM_PROMPT
        print(f"[Warning] Session {resume_session} not found, creating a new session")

    session_id = session_db.create_session(session_name, system_prompt)
    print(f"[Session] Created new session '{session_name}' (ID: {session_id})")
    return session_id, system_prompt
