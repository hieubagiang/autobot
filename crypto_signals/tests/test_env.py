from crypto_signals.env import load_env_file


def test_load_env_file_sets_environ(tmp_path, monkeypatch):
    monkeypatch.delenv("CRYPTO_SIGNALS_TEST_VAR", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\n\nCRYPTO_SIGNALS_TEST_VAR=hello\n", encoding="utf-8")

    load_env_file(str(env_file))

    import os
    assert os.environ["CRYPTO_SIGNALS_TEST_VAR"] == "hello"


def test_load_env_file_missing_file_is_a_noop(tmp_path):
    load_env_file(str(tmp_path / "does_not_exist.env"))  # must not raise
