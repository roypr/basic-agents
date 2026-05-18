import pytest
from pathlib import Path

import main


def test_get_agent_class():
    assert main.get_agent_class('default')
    with pytest.raises(ValueError, match="ocr"):
        main.get_agent_class("ocr")
