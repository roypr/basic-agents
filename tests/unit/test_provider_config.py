import pytest

from utils import provider_config
from utils.provider_config import ProviderError, resolve_provider


_FAKE = {
    "default_provider": "alpha",
    "default_model": "alpha-default",
    "providers": [
        {
            "name": "alpha",
            "api_base_url": "http://alpha",
            "api_key": "key-alpha",
            "models": ["alpha-default", "alpha-other"],
        },
        {
            "name": "beta",
            "api_base_url": "http://beta",
            "api_key": "key-beta",
            "models": ["beta-first", "beta-second"],
        },
    ],
}


@pytest.mark.unit
class TestResolveProvider:
    def _patch(self, monkeypatch):
        monkeypatch.setattr(provider_config, "load_providers", lambda: _FAKE)

    def test_default_provider_and_model(self, monkeypatch):
        self._patch(monkeypatch)

        cfg = resolve_provider()

        assert cfg.name == "alpha"
        assert cfg.model == "alpha-default"
        assert cfg.api_key == "key-alpha"

    def test_switch_provider_loads_its_credentials_and_first_model(self, monkeypatch):
        self._patch(monkeypatch)

        # The global default_model is not valid for beta, so its first model
        # (and its own credentials) must be used.
        cfg = resolve_provider("beta")

        assert cfg.api_base_url == "http://beta"
        assert cfg.api_key == "key-beta"
        assert cfg.model == "beta-first"

    def test_explicit_model_is_validated_strictly(self, monkeypatch):
        self._patch(monkeypatch)

        cfg = resolve_provider("beta", "beta-second")
        assert cfg.model == "beta-second"

        with pytest.raises(ProviderError):
            resolve_provider("beta", "alpha-default")
