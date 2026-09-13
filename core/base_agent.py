import json
import logging
from db.session_db import SessionDB
from core.permissions import AutoAllowResolver, PermissionPolicy
from core.rule_store import InMemoryRuleStore
from utils.http_utils import close_request_session
from utils.llm_client import call_llm_streaming, get_adapter
from utils.session_manager import init_session_db
from utils.tool_executor import execute_tool_calls, shutdown_tool_executor

SYSTEM_PROMPT = "You are a helpful assistant."

# Content used to heal an assistant tool_call that never got a result because a
# previous run died mid-turn (e.g. while prompting). The OpenAI contract needs
# 1:1 tool_call/tool-result correspondence or the next request 400s.
ORPHANED_TOOL_RESULT = (
    "Error: tool call was not completed — the previous run was interrupted "
    "before a result was produced. Do not assume this tool ran."
)


logging.basicConfig(
    filename="logs/app_errors.log",
    filemode="a",  # 'a' appends data, 'w' overwrites the file each run
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.ERROR,
)


class BaseAgent:
    def __init__(
        self,
        name: str = "Agent",
        model: str = "local",
        llm_base: str = "http://localhost:8080",
        max_turns: int = 10,
        resume_session: int | None = None,
        session_name: str = "Default Session",
        api_key: str = "",
        use_tools: bool = True,
        permission_policy: PermissionPolicy | None = None,
        permission_resolver=None,
        stream_sink=None,
    ):
        self.name = name
        self.model = model
        self.llm_base = llm_base
        self.max_turns = max_turns
        self.resume_session = resume_session
        self.session_name = session_name
        self.api_key = api_key
        self.use_tools = use_tools
        # Optional rendering sink for streamed output (None -> print directly).
        self.stream_sink = stream_sink
        self._shutdown_requested = False
        # Bound on the first run(); reused across REPL turns until reset.
        self.session_id: int | None = None

        self.adapter = get_adapter(model)
        self.session_db = SessionDB()
        self.tools = self.get_tools()
        self.tool_map = self.get_tool_map()
        self.system_prompt = self.resolve_system_prompt()

        # Per-agent policy: grading is scoped to the tools this agent exposes.
        # Default resolver keeps one-shot `run` behavior backward compatible.
        self.permission_policy = permission_policy or PermissionPolicy(
            agent_tool_names=self.tool_map.keys(),
            rule_store=InMemoryRuleStore(),
            resolver=permission_resolver or AutoAllowResolver(),
        )

    def resolve_system_prompt(self) -> str:
        return self.get_system_prompt()

    def get_system_prompt(self) -> str:
        return SYSTEM_PROMPT

    def get_tools(self) -> list:
        raise NotImplementedError("Agent must implement get_tools()")

    def get_tool_map(self) -> dict:
        raise NotImplementedError("Agent must implement get_tool_map()")

    def _agent_dir_name(self) -> str:
        """Return the agent subdirectory name (e.g. 'code', 'plan').

        Used for agent-switch detection. Override in subclasses if the
        directory name differs from the agent name.
        """
        import inspect
        import pathlib

        mod = inspect.getmodule(self.__class__)
        if mod and mod.__file__:
            return pathlib.Path(mod.__file__).resolve().parent.name
        return self.name.lower()

    @staticmethod
    def _deserialize_content(content) -> str | list:
        """Deserialize message content from DB — JSON array back to list, string as-is."""
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
        return content

    def _build_user_content(self, query: str, image_data: dict | None):
        """Build user message content — string for text-only, list for multimodal."""
        if image_data is None:
            return query

        data_uri = f"data:{image_data['mime']};base64,{image_data['data']}"
        content = [{"type": "text", "text": query or "What's in this image?"}]
        content.append({"type": "image_url", "image_url": {"url": data_uri}})
        return content

    def _tool_content(self, result: str) -> str | list:
        """Convert structured tool output (e.g. read_image JSON) into multimodal
        content so the model can actually see the image in the tool result."""
        try:
            parsed = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return result

        if isinstance(parsed, dict) and parsed.get("type") == "image":
            data_uri = f"data:{parsed['mime']};base64,{parsed['data']}"
            note = parsed.get("note") or "Image loaded."
            return [
                {"type": "text", "text": note},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ]
        return result

    def _serialize_content(self, content) -> str:
        """Serialize message content to string for DB storage."""
        if isinstance(content, list):
            return json.dumps(content)
        return content

    @staticmethod
    def _parse_tool_args(tc: dict) -> dict:
        """Parse a tool call's arguments, mirroring the executor's parsing."""
        raw_args = tc.get("function", {}).get("arguments")
        try:
            return (
                json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            )
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _denial_text(outcome) -> str:
        """Synthesize the tool-result text for a denied call."""
        if outcome.message:
            return (
                f'Permission denied by user. The user says: "{outcome.message}". '
                "Do not retry this tool unless explicitly asked."
            )
        return (
            "Permission denied by user. Do not retry this tool unless explicitly asked."
        )

    def _recover_orphaned_tool_calls(self, session_id: int, messages: list) -> list:
        """Heal assistant tool_calls that never got a matching tool result.

        A run that dies mid-turn (e.g. while prompting) leaves persisted
        tool_calls without results, which 400s on resume. We synthesize an abort
        result AND persist it, so the session is fixed once and stays fixed.
        """
        answered = {
            m.get("tool_call_id")
            for m in messages
            if m.get("role") == "tool" and m.get("tool_call_id")
        }

        recovered = []
        for msg in messages:
            recovered.append(msg)
            if msg.get("role") != "assistant" or not msg.get("tool_calls"):
                continue
            for tc in msg["tool_calls"]:
                call_id = tc.get("id")
                if not call_id or call_id in answered:
                    continue
                recovered.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": ORPHANED_TOOL_RESULT,
                    }
                )
                self.session_db.add_message(
                    session_id, "tool", ORPHANED_TOOL_RESULT, tool_call_id=call_id
                )
                answered.add(call_id)
        return recovered

    def _gate_and_execute(self, tool_calls: list) -> list:
        """Run the permission gate, then execute approved calls concurrently.

        The gate runs in the main thread, strictly before the executor. Results
        are merged back in the original tool_call order (OpenAI contract).
        """
        approved = []
        results: dict[int, tuple] = {}

        for index, tc in enumerate(tool_calls):
            fn_name = tc["function"]["name"]
            fn_args = self._parse_tool_args(tc)
            outcome = self.permission_policy.check(
                fn_name, fn_args, tool_call_id=tc.get("id")
            )
            if outcome.error:
                results[index] = (tc, fn_name, fn_args, outcome.message)
            elif outcome.allowed:
                approved.append((index, tc))
            else:
                results[index] = (tc, fn_name, fn_args, self._denial_text(outcome))

        if approved:
            approved_results = execute_tool_calls(
                [tc for _, tc in approved], self.tool_map, self.tools
            )
            for (index, _tc), result in zip(approved, approved_results):
                results[index] = result

        return [results[i] for i in range(len(tool_calls))]

    def run(self, query: str, image_data: dict | None = None):
        print(
            f"[{self.name}] Using adapter: {type(self.adapter).__name__} for model '{self.model}'"
        )

        agent_dir = self._agent_dir_name()

        # Session binding: bind on the first run(), reuse it on every
        # subsequent call (REPL turns) until reset via /new.
        if self.session_id is not None:
            session_id = self.session_id
        else:
            session_id = init_session_db(
                self.resume_session,
                self.session_name,
                self.system_prompt,
                agent_name=agent_dir,
            )
            self.session_id = session_id

        # Standing permission rules are scoped to the bound session.
        self.permission_policy.scope_key = session_id

        # Agent's own prompt is the authority — never overridden from DB
        final_system_prompt = self.system_prompt

        messages = self.session_db.get_messages(session_id)
        # Heal sessions where a previous run died before producing tool results.
        messages = self._recover_orphaned_tool_calls(session_id, messages)
        # Deserialize any multimodal content stored as JSON
        for msg in messages:
            if msg["role"] == "user":
                msg["content"] = self._deserialize_content(msg["content"])

        if messages:
            print(
                f"[{self.name}] Session Loaded {len(messages)} messages from session history"
            )

            if messages[0]["role"] != "system":
                messages.insert(0, {"role": "system", "content": final_system_prompt})
            else:
                messages[0]["content"] = final_system_prompt

            if query or image_data:
                user_content = self._build_user_content(query, image_data)
                messages.append({"role": "user", "content": user_content})
                self.session_db.add_message(
                    session_id, "user", self._serialize_content(user_content)
                )
        else:
            user_content = self._build_user_content(query, image_data)
            messages = [
                {"role": "system", "content": final_system_prompt},
                {"role": "user", "content": user_content},
            ]
            self.session_db.add_message(
                session_id, "user", self._serialize_content(user_content)
            )

        for turn in range(self.max_turns):
            if self._shutdown_requested:
                print(
                    f"[{self.name}] Shutdown requested. Exiting before next turn. To resume session id {session_id}"
                )
                return

            print(f"\n--- Turn {turn + 1} ---")

            logging.debug("Messages: %s", messages)

            msg = call_llm_streaming(
                messages,
                model=self.model,
                llm_base=self.llm_base,
                api_key=self.api_key,
                adapter=self.adapter,
                use_tools=self.use_tools,
                tools=self.tools,
                sink=self.stream_sink,
            )

            tool_calls = self.adapter.extract_tool_calls(msg)
            reasoning = self.adapter.extract_reasoning(msg)
            content = self.adapter.extract_content(msg)

            if hasattr(self.adapter, "build_assistant_message"):
                assistant_message = self.adapter.build_assistant_message(
                    content, reasoning, tool_calls
                )
            else:
                assistant_message = {
                    "role": "assistant",
                    "content": content,
                    **({"tool_calls": tool_calls} if tool_calls else {}),
                }

            messages.append(assistant_message)
            self.session_db.add_message(session_id, "assistant", content, tool_calls)

            if not tool_calls:
                return

            tool_results = self._gate_and_execute(tool_calls)
            if self._shutdown_requested:
                print(
                    f"[{self.name}] Shutdown requested during tool execution. Exiting. To resume session id {session_id}"
                )
                return

            for tc, fn_name, fn_args, result in tool_results:
                tool_call_id = tc.get("id")
                content = self._tool_content(result)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": content,
                    }
                )
                self.session_db.add_message(
                    session_id,
                    "tool",
                    self._serialize_content(content),
                    tool_call_id=tool_call_id,
                )

            if any(fn_name == "finish" for _, fn_name, _, _ in tool_results):
                print(
                    f"\n[{self.name}] Finish tool called — stopping loop. To resume session id {session_id}"
                )
                return

        print(
            f"\n[{self.name}] Max turns reached No final answer produced. To resume session id {session_id}"
        )

    def shutdown(self):
        """Request agent shutdown and close active HTTP/tool resources."""
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        close_request_session()
        shutdown_tool_executor()
