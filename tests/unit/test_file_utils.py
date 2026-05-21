import pytest
from utils.file_utils import load_tool_definition

def test_load_tool_definition_returns_dict():
    """
    Test that the function returns a dictionary.
    """
    result = load_tool_definition()
    assert isinstance(result, dict), "The function should return a dictionary."