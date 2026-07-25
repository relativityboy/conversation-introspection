from introspect import config


def test_terminal_app_default(monkeypatch) -> None:
    monkeypatch.delenv("INTROSPECT_TERMINAL_APP", raising=False)
    assert config.terminal_app() == "Terminal"


def test_terminal_app_env_overrides_default(monkeypatch) -> None:
    monkeypatch.setenv("INTROSPECT_TERMINAL_APP", "iTerm")
    assert config.terminal_app() == "iTerm"


def test_terminal_app_explicit_beats_env(monkeypatch) -> None:
    monkeypatch.setenv("INTROSPECT_TERMINAL_APP", "iTerm")
    assert config.terminal_app("Ghostty") == "Ghostty"
