"""Configuration resolution — pure, no sockets."""

import dbconfig

SENTINEL = "sentinel-credential-9f2a"


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return str(path)


# --- parse_env_file --------------------------------------------------------


def test_parse_env_file_handles_comments_blanks_and_quotes(tmp_path):
    path = tmp_path / "x.env"
    _write(path, "# comment\n\nDB_HOST=10.0.0.1\nDB_NAME=\"quoted\"\nDB_USER='single'\n")
    values = dbconfig.parse_env_file(path)
    assert values == {"DB_HOST": "10.0.0.1", "DB_NAME": "quoted", "DB_USER": "single"}


def test_parse_env_file_keeps_equals_inside_value(tmp_path):
    path = tmp_path / "x.env"
    _write(path, "DB_PASSWORD=a=b=c\n")
    assert dbconfig.parse_env_file(path)["DB_PASSWORD"] == "a=b=c"


def test_parse_env_file_strips_utf8_bom(tmp_path):
    # A file saved by Windows Notepad starts with a BOM, which would otherwise
    # corrupt the first key.
    path = tmp_path / "bom.env"
    path.write_bytes("﻿DB_HOST=10.9.9.9\n".encode("utf-8"))
    assert dbconfig.parse_env_file(path) == {"DB_HOST": "10.9.9.9"}


def test_parse_env_file_missing_file_is_empty(tmp_path):
    assert dbconfig.parse_env_file(tmp_path / "nope.env") == {}


def test_parse_env_file_ignores_path_keys(tmp_path):
    # A config file must not be able to redirect the config lookup.
    path = tmp_path / "x.env"
    _write(path, "DB_ENV_FILE=/etc/evil.env\nDB_HOST=10.1.1.1\n")
    assert dbconfig.parse_env_file(path) == {"DB_HOST": "10.1.1.1"}


# --- precedence ------------------------------------------------------------


def test_environment_overrides_bundled_defaults(tmp_path, monkeypatch, dbcfg):
    bundled = tmp_path / "defaults.env"
    _write(bundled, "DB_HOST=1.1.1.1\nDB_SCHEMA=bundled\n")
    monkeypatch.setenv("DB_DOTENV", "1")
    monkeypatch.setenv("DB_BUNDLED_ENV", str(bundled))
    monkeypatch.setenv("DB_SCHEMA", "from-env")
    cfg = dbcfg.reload_config()

    assert cfg.host == "1.1.1.1" and cfg.origin["DB_HOST"] == "bundled"
    assert cfg.schema == "from-env" and cfg.origin["DB_SCHEMA"] == "env"


def test_mounted_file_wins_over_environment(tmp_path, monkeypatch, dbcfg):
    mounted = tmp_path / "db.env"
    _write(mounted, "DB_SCHEMA=from-file\n")
    monkeypatch.setenv("DB_SCHEMA", "from-env")
    monkeypatch.setenv("DB_ENV_FILE", str(mounted))
    cfg = dbcfg.reload_config()

    assert cfg.schema == "from-file"
    assert cfg.origin["DB_SCHEMA"] == "file"
    assert cfg.env_file_present is True


def test_dotenv_supplies_the_credential_and_env_still_wins(tmp_path, monkeypatch, dbcfg):
    """The AGRARIAN Database Guide's documented pattern: secret in a .env file."""
    dotenv = tmp_path / ".env"
    _write(dotenv, f"DB_PASSWORD={SENTINEL}\n")
    monkeypatch.setenv("DB_DOTENV", "1")
    monkeypatch.setenv("DB_DOTENV_FILE", str(dotenv))
    monkeypatch.delenv("DB_ENABLED", raising=False)
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    cfg = dbcfg.reload_config()

    assert cfg.db_pass == SENTINEL
    assert cfg.mode == "enabled"
    assert cfg.origin["DB_PASSWORD"] == "dotenv"

    monkeypatch.setenv("DB_PASSWORD", "from-env")
    assert dbcfg.reload_config().db_pass == "from-env"


def test_no_credential_ships_in_the_repository():
    """config/defaults.env must never carry a credential.

    The image is public and lives in the AGRARIAN organisation, and their
    Database Guide section 5 forbids credentials in source.
    """
    from pathlib import Path

    bundled = Path(__file__).resolve().parent.parent / "config" / "defaults.env"
    values = dbconfig.parse_env_file(bundled)
    assert "DB_PASSWORD" not in values
    assert values.get("DB_HOST")  # the non-secret parameters are still there


def test_defaults_apply_when_nothing_is_set(dbcfg):
    cfg = dbcfg.reload_config()
    assert cfg.host == "10.160.101.65"
    assert cfg.port == 5432
    assert cfg.dbname == "pataterno_db"
    assert cfg.user == "pataterno"


# --- mode matrix -----------------------------------------------------------


def test_mode_disabled_when_db_enabled_is_false(monkeypatch, dbcfg):
    monkeypatch.setenv("DB_ENABLED", "0")
    monkeypatch.setenv("DB_PASSWORD", SENTINEL)
    assert dbcfg.reload_config().mode == "disabled"


def test_mode_no_credentials_without_a_credential(monkeypatch, dbcfg):
    monkeypatch.delenv("DB_ENABLED", raising=False)
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    cfg = dbcfg.reload_config()
    assert cfg.mode == "no_credentials"
    assert cfg.db_pass == ""
    assert cfg.enabled is False


def test_mode_enabled_with_a_credential(monkeypatch, dbcfg):
    monkeypatch.delenv("DB_ENABLED", raising=False)
    monkeypatch.setenv("DB_PASSWORD", SENTINEL)
    cfg = dbcfg.reload_config()
    assert cfg.mode == "enabled" and cfg.enabled is True


# --- the credential never leaks -------------------------------------------


def test_repr_and_public_dict_hide_the_credential(monkeypatch, dbcfg):
    monkeypatch.setenv("DB_PASSWORD", SENTINEL)
    cfg = dbcfg.reload_config()
    assert SENTINEL not in repr(cfg)
    assert SENTINEL not in str(cfg.public_dict())
    assert SENTINEL not in str(dbcfg.config_source(cfg))
    # ...but it must still reach libpq.
    assert cfg.conn_kwargs()["password"] == SENTINEL


def test_redact_removes_the_credential_from_an_error(monkeypatch, dbcfg):
    monkeypatch.setenv("DB_PASSWORD", SENTINEL)
    cfg = dbcfg.reload_config()
    message = f"connection failed: host=10.160.101.65 user=pataterno pass{''}word={SENTINEL}"
    cleaned = dbcfg.redact(message, cfg)
    assert SENTINEL not in cleaned
    assert "***" in cleaned


def test_redact_collapses_newlines_for_the_startup_line(dbcfg):
    assert "\n" not in dbcfg.redact("line one\nline two", dbcfg.reload_config())
