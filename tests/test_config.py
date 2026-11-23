from __future__ import annotations

import pytest
from pydantic import ValidationError

from chefscout.config import Settings


def test_defaults_are_the_cheap_setup(settings):
    assert settings.model == "claude-haiku-5-5"
    assert settings.effort == "low"
    assert settings.max_web_searches == 3
    assert settings.host == "127.0.0.1"
    assert settings.api_key_configured is False


def test_reads_prefixed_environment_variables(monkeypatch):
    monkeypatch.setenv("CHEFSCOUT_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("CHEFSCOUT_ANTHROPIC_API_KEY", "sk-test")
    s = Settings(_env_file=None)
    assert s.model == "claude-opus-5-5"
    assert s.api_key_configured is True


def test_key_is_never_printed(monkeypatch):
    monkeypatch.setenv("CHEFSCOUT_ANTHROPIC_API_KEY", "sk-secret-value")
    s = Settings(_env_file=None)
    assert "sk-secret-value" not in repr(s)
    assert "sk-secret-value" not in str(s.model_dump())


@pytest.mark.parametrize("field,value", [("effort", "turbo"), ("max_web_searches", 99), ("port", 0)])
def test_rejects_invalid_values(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})
