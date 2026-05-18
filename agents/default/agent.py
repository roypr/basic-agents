from core.base_agent import BaseAgent
from .tools import tools, TOOL_MAP
from .prompt import get_system_prompt


class DefaultAgent(BaseAgent):
    def get_system_prompt(self) -> str:
        return get_system_prompt()

    def get_tools(self) -> list:
        return tools

    def get_tool_map(self) -> dict:
        return TOOL_MAP
