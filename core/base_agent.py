from db.session_db import SessionDB
from utils.http_utils import close_request_session
from utils.llm_client import call_llm_streaming, get_adapter
from utils.session_manager import init_session_db
from utils.tool_executor import execute_tool_calls, shutdown_tool_executor


SYSTEM_PROMPT = "You are a helpful assistant."


class BaseAgent:
    def __init__(
        self,
        model: str = "local",
        llm_base: str = "http://localhost:8080",
        max_turns: int = 10,
        resume_session: int | None = None,
        session_name: str = "Default Session",
        api_key: str = "",
        use_tools: bool = True,
    ):
        self.model = model
        self.llm_base = llm_base
        self.max_turns = max_turns
        self.resume_session = resume_session
        self.session_name = session_name
        self.api_key = api_key
        self.use_tools = use_tools
        self._shutdown_requested = False

        self.adapter = get_adapter(model)
        self.session_db = SessionDB()
        self.tools = self.get_tools()
        self.tool_map = self.get_tool_map()
        self.system_prompt = self.resolve_system_prompt()

    def resolve_system_prompt(self) -> str:
        return self.get_system_prompt()

    def get_system_prompt(self) -> str:
        return SYSTEM_PROMPT

    def get_tools(self) -> list:
        raise NotImplementedError("Agent must implement get_tools()")

    def get_tool_map(self) -> dict:
        raise NotImplementedError("Agent must implement get_tool_map()")

    def run(self, query: str):
        print(f"[Agent] Using adapter: {type(self.adapter).__name__} for model '{self.model}'")

        session_id, session_prompt = init_session_db(
            self.resume_session,
            self.session_name,
            self.system_prompt,
        )

        final_system_prompt = session_prompt if session_prompt else self.system_prompt

        messages = self.session_db.get_messages(session_id)
        if messages:
            print(f"[Session] Loaded {len(messages)} messages from session history")
        else:
            messages = [
                {"role": "system", "content": final_system_prompt},
                {"role": "user", "content": query},
            ]
            self.session_db.add_message(session_id, "user", query)

        if messages and messages[-1]["role"] != "user" and query:
            messages.append({"role": "user", "content": query})
            self.session_db.add_message(session_id, "user", query)

        for turn in range(self.max_turns):
            if self._shutdown_requested:
                print("[Agent] Shutdown requested. Exiting before next turn.")
                return

            print(f"\n--- Turn {turn + 1} ---")
            if turn == 0 and messages[0]["role"] != "system":
                messages.insert(0, {"role": "system", "content": final_system_prompt})

            msg = call_llm_streaming(
                messages,
                model=self.model,
                llm_base=self.llm_base,
                api_key=self.api_key,
                adapter=self.adapter,
                use_tools=self.use_tools,
                tools=self.tools,
            )

            tool_calls = self.adapter.extract_tool_calls(msg)
            reasoning = self.adapter.extract_reasoning(msg)
            content = self.adapter.extract_content(msg)

            if hasattr(self.adapter, "build_assistant_message"):
                assistant_message = self.adapter.build_assistant_message(content, reasoning, tool_calls)
            else:
                assistant_message = {
                    "role": "assistant",
                    "content": content,
                    **({"tool_calls": tool_calls} if tool_calls else {}),
                }

            messages.append(assistant_message)
            self.session_db.add_message(session_id, "assistant", content, tool_calls)

            if not tool_calls:
                print(f"\n[Answer]\n{content}")
                return

            tool_results = execute_tool_calls(tool_calls, self.tool_map, self.tools)
            if self._shutdown_requested:
                print("[Agent] Shutdown requested during tool execution. Exiting.")
                return

            for tc, fn_name, fn_args, result in tool_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "content": result,
                })
                self.session_db.add_message(session_id, "tool", result)

            if any(fn_name == "finish" for _, fn_name, _, _ in tool_results):
                print("\n[Agent] Finish tool called — stopping loop.")
                return

        print("\n[Max turns reached] No final answer produced.")

    def shutdown(self):
        """Request agent shutdown and close active HTTP/tool resources."""
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        print("[Agent] Shutdown requested. Cleaning up HTTP session and tool executor.")
        close_request_session()
        shutdown_tool_executor()
