from pathlib import Path

from core.base_agent import BaseAgent
from .tools import tools, TOOL_MAP


class DefaultAgent(BaseAgent):
    def get_system_prompt(self) -> str:
        prompt_file = Path(__file__).resolve().parent / "system_prompt.txt"
        if prompt_file.exists():
            return prompt_file.read_text(encoding="utf-8").strip()
        prompt_file = prompt_file.with_suffix(".md")
        if prompt_file.exists():
            return prompt_file.read_text(encoding="utf-8").strip()
        return "You are a helpful assistant."

    def get_tools(self) -> list:
        return tools

    def get_tool_map(self) -> dict:
        return TOOL_MAP
