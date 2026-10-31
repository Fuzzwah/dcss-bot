from pathlib import Path
import stat

import pytest

from dcss_bot.config import Config


def test_unrelated_agent_environment_does_not_override_strategy(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT", "1")
    monkeypatch.delenv("BOT_AGENT", raising=False)
    assert Config.from_env().agent == "rule"
    monkeypatch.setenv("BOT_AGENT", "example:LocalAgent")
    assert Config.from_env().agent == "example:LocalAgent"


def test_credentials_are_private_reusable_and_absent_from_public_metadata(tmp_path):
    first = Config(username="PrivateBot", control_socket=tmp_path / "control.sock")
    first.ensure_credentials()
    second = Config(username="PrivateBot", control_socket=tmp_path / "control.sock")
    second.ensure_credentials()
    assert first.password == second.password
    assert len(first.password) >= 24
    assert stat.S_IMODE((tmp_path / "credentials-PrivateBot.json").stat().st_mode) == 0o600
    assert "password" not in first.public_dict()


@pytest.mark.parametrize("delay", [float("nan"), float("inf"), -.1, 61])
def test_invalid_action_delays_are_rejected(delay):
    with pytest.raises(ValueError):
        Config(delay=delay).validate()


@pytest.mark.parametrize("seed", [0, -1, 2**64, True, 1.5, "123", "random"])
def test_invalid_seeds_are_rejected(seed):
    with pytest.raises(ValueError, match="BOT_SEED"):
        Config(seed=seed).validate()




def test_optional_seed_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BOT_SEED", raising=False)
    assert Config.from_env().seed is None
    monkeypatch.setenv("BOT_SEED", "")
    assert Config.from_env().seed is None
    monkeypatch.setenv("BOT_SEED", "18446744073709551615")
    config = Config.from_env()
    config.validate()
    assert config.seed == 2**64 - 1
    monkeypatch.setenv("BOT_SEED", "0")
    with pytest.raises(ValueError, match="BOT_SEED"):
        Config.from_env().validate()
    monkeypatch.setenv("BOT_SEED", "random")
    with pytest.raises(ValueError):
        Config.from_env()


async def test_unknown_character_rejected_before_credentials_or_connection(tmp_path):
    from dcss_bot.cli import run

    config = Config(character="unsupported-combo", control_socket=tmp_path / "private" / "control.sock")
    with pytest.raises(ValueError, match="Unknown character"):
        await run(config)
    assert not config.control_socket.parent.exists()
