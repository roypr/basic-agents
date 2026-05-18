from pathlib import Path

from core.base_agent import BaseAgent
from .tools import tools, TOOL_MAP


class SearchAgent(BaseAgent):
    def get_system_prompt(self) -> str:
        prompt_file = Path(__file__).resolve().parent / "system_prompt.txt"
        if prompt_file.exists():
            return prompt_file.read_text(encoding="utf-8").strip()
        prompt_file = prompt_file.with_suffix(".md")
        if prompt_file.exists():
            return prompt_file.read_text(encoding="utf-8").strip()
        return "You are a search assistant. Use only the available tools to search the web and fetch URL content. Avoid inventing facts, and prefer the tool results when answering queries."

    def get_tools(self) -> list:
        return tools

    def get_tool_map(self) -> dict:
        return TOOL_MAP
