import argparse
import json
from datetime import datetime
from db.session_db import SessionDB
from utils.history_compressor import compress_session as compress_session_fn


def file_write(path: str, data: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)


def create_session(args):
    db = SessionDB()
    session_id = db.create_session(args.name, args.system_prompt)
    print(f"Session created with ID: {session_id}")


def list_sessions(_args):
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


def get_session(args):
    db = SessionDB()
    session = db.get_session(args.id)
    if not session:
        print(f"Session {args.id} not found or inactive.")
        return
    messages = db.get_messages(args.id)
    session_data = {
        "session_id": session["id"],
        "name": session["name"],
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "system_prompt": session["system_prompt"],
        "messages": messages,
    }
    filename = f"session_{args.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    file_write(filename, json.dumps(session_data, indent=2))
    print(f"Session data saved to {filename}")


def delete_session(args):
    db = SessionDB()
    success = db.delete_session(args.id)
    if success:
        print(f"Session {args.id} marked as deleted.")
    else:
        print(f"Session {args.id} not found or already deleted.")


def compress_session_cmd(args):
    """Compress a session by stripping tool artifacts."""
    db = SessionDB()
    new_id = compress_session_fn(db, args.id, args.output_dir)
    if new_id is None:
        print(f"Failed to compress session {args.id}.")
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description="Manage basic-agents sessions")
    subparsers = parser.add_subparsers(dest="command")

    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--name", required=True, help="Name of the session")
    create_parser.add_argument(
        "--system-prompt", default="", help="System prompt content"
    )

    list_parser = subparsers.add_parser("list")
    get_parser = subparsers.add_parser("get")
    get_parser.add_argument("--id", type=int, required=True, help="Session ID")

    delete_parser = subparsers.add_parser("delete")
    delete_parser.add_argument("--id", type=int, required=True, help="Session ID")

    compress_parser = subparsers.add_parser("compress")
    compress_parser.add_argument(
        "--id", type=int, required=True, help="Session ID to compress"
    )
    compress_parser.add_argument(
        "--output-dir", default="logs", help="Directory for raw export (default: logs/)"
    )

    args = parser.parse_args()
    if args.command == "create":
        create_session(args)
    elif args.command == "list":
        list_sessions(args)
    elif args.command == "get":
        get_session(args)
    elif args.command == "delete":
        delete_session(args)
    elif args.command == "compress":
        compress_session_cmd(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
