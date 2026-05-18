import pytest
from unittest.mock import Mock, patch
from config import Config

@pytest.mark.unit
class TestConfig:
    def test_config_initialization(self):
        """Test that Config initializes correctly"""
        config = Config()
        assert config is not None

    def test_config_get_value(self):
        """Test getting configuration values"""
        config = Config()
        with patch.dict(os.environ, {'TEST_VAR': 'test_value'}):
            assert config.get_value('TEST_VAR') == 'test_value'

    def test_config_get_value_default(self):
        """Test getting configuration values with default"""
        config = Config()
        assert config.get_value('NONEXISTENT_VAR', 'default_value') == 'default_value'