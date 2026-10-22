"""Pruebas unitarias para la carga y validación de Settings (config.py)."""

from __future__ import annotations

import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from dirty_equals import HasAttributes
from dotenv import dotenv_values

from rpa_cuentas.config import ConfigurationError, Settings, load_settings

EXPECTED_ENV_KEYS: frozenset[str] = frozenset(
    {
        "EXTERNAL_API_BASE_URL",
        "HTTP_TIMEOUT_SECONDS",
        "RETRY_MAX_ATTEMPTS",
        "RETRY_BASE_DELAY_SECONDS",
        "RETRY_BACKOFF_FACTOR",
        "RETRY_MAX_DELAY_SECONDS",
        "REQUEST_DELAY_SECONDS",
        "INPUT_CSV_PATH",
        "OUTPUT_EXCEL_PATH",
        "SQLITE_DB_PATH",
        "LOG_FILE_PATH",
        "LOCK_FILE_PATH",
        "LOG_LEVEL",
    }
)


def test_default_settings_are_valid_and_immutable() -> None:
    """Verifica que Settings por defecto tenga los valores esperados y sea inmutable."""
    settings = Settings()

    assert settings == HasAttributes(
        external_api_base_url="https://jsonplaceholder.typicode.com",
        http_timeout_seconds=10.0, retry_max_attempts=4, retry_base_delay_seconds=1.0,
        retry_backoff_factor=2.0, retry_max_delay_seconds=30.0, request_delay_seconds=0.0,
        input_csv_path=Path("data/cuentas.csv"),
        output_excel_path=Path("output/consolidado_cuentas.xlsx"),
        sqlite_db_path=Path("state/rpa_cuentas.db"),
        log_file_path=Path("logs/rpa_cuentas.log"),
        lock_file_path=Path("state/rpa_cuentas.lock"), log_level="INFO",
    )  # fmt: skip

    with pytest.raises(FrozenInstanceError):
        settings.retry_max_attempts = 10  # type: ignore[misc]


def test_env_example_defines_all_13_expected_keys_and_is_valid() -> None:
    """Verifica que .env.example contenga exactamente las 13 variables soportadas y sea válido."""
    parsed_keys = set(dotenv_values(dotenv_path=Path(".env.example")).keys())
    assert parsed_keys == EXPECTED_ENV_KEYS
    assert load_settings(env_file=Path(".env.example"), environ={}) == Settings()


def test_load_settings_automatically_reads_all_13_variables_from_cwd_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifica que load_settings() lea las 13 variables del .env en CWD sin mutar os.environ."""
    for key in EXPECTED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)

    dotenv_content = "\n".join(
        [
            "EXTERNAL_API_BASE_URL=https://api.hospital-central.org/v2/",
            "HTTP_TIMEOUT_SECONDS=25.5",
            "RETRY_MAX_ATTEMPTS=6",
            "RETRY_BASE_DELAY_SECONDS=2.5",
            "RETRY_BACKOFF_FACTOR=3.0",
            "RETRY_MAX_DELAY_SECONDS=60.0",
            "REQUEST_DELAY_SECONDS=0.75",
            "INPUT_CSV_PATH=custom_in/lote.csv",
            "OUTPUT_EXCEL_PATH=custom_out/auditoria.xlsx",
            "SQLITE_DB_PATH=custom_state/cuentas.sqlite",
            "LOG_FILE_PATH=custom_logs/ejecucion.log",
            "LOCK_FILE_PATH=custom_state/ejecucion.lock",
            "LOG_LEVEL=warning",
        ]
    )
    (tmp_path / ".env").write_text(dotenv_content, encoding="utf-8")

    settings = load_settings()

    assert settings == Settings(
        external_api_base_url="https://api.hospital-central.org/v2",
        http_timeout_seconds=25.5,
        retry_max_attempts=6,
        retry_base_delay_seconds=2.5,
        retry_backoff_factor=3.0,
        retry_max_delay_seconds=60.0,
        request_delay_seconds=0.75,
        input_csv_path=Path("custom_in/lote.csv"),
        output_excel_path=Path("custom_out/auditoria.xlsx"),
        sqlite_db_path=Path("custom_state/cuentas.sqlite"),
        log_file_path=Path("custom_logs/ejecucion.log"),
        lock_file_path=Path("custom_state/ejecucion.lock"),
        log_level="WARNING",
    )
    assert "EXTERNAL_API_BASE_URL" not in os.environ


def test_load_settings_precedence_defaults_then_dotenv_then_os_environ_then_cli_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifica la jerarquía Defaults < .env < os.environ < input_csv_override."""
    for key in EXPECTED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)

    (tmp_path / ".env").write_text(
        "EXTERNAL_API_BASE_URL=https://dotenv.clinica.local/\n"
        "RETRY_MAX_ATTEMPTS=5\n"
        "LOG_LEVEL=ERROR\n"
        "INPUT_CSV_PATH=dotenv_dir/cuentas.csv\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("INPUT_CSV_PATH", "environ_dir/cuentas.csv")
    cli_input = tmp_path / "cli_override.csv"

    settings = load_settings(input_csv_override=cli_input)

    assert settings == HasAttributes(
        external_api_base_url="https://dotenv.clinica.local",
        retry_max_attempts=5,
        http_timeout_seconds=10.0,
        log_level="DEBUG",
        input_csv_path=cli_input,
    )


@pytest.mark.parametrize(
    ("env_overrides", "expected_fragment"),
    [
        ({"EXTERNAL_API_BASE_URL": "ftp://invalid.local"}, "EXTERNAL_API_BASE_URL"),
        ({"EXTERNAL_API_BASE_URL": "https://"}, "EXTERNAL_API_BASE_URL"),
        ({"HTTP_TIMEOUT_SECONDS": "0"}, "HTTP_TIMEOUT_SECONDS"),
        ({"HTTP_TIMEOUT_SECONDS": "-5"}, "HTTP_TIMEOUT_SECONDS"),
        ({"HTTP_TIMEOUT_SECONDS": "abc"}, "HTTP_TIMEOUT_SECONDS"),
        ({"HTTP_TIMEOUT_SECONDS": "nan"}, "HTTP_TIMEOUT_SECONDS"),
        ({"RETRY_MAX_ATTEMPTS": "0"}, "RETRY_MAX_ATTEMPTS"),
        ({"RETRY_MAX_ATTEMPTS": "dos"}, "RETRY_MAX_ATTEMPTS"),
        ({"RETRY_BASE_DELAY_SECONDS": "-0.1"}, "RETRY_BASE_DELAY_SECONDS"),
        ({"RETRY_BACKOFF_FACTOR": "0.5"}, "RETRY_BACKOFF_FACTOR"),
        (
            {"RETRY_BASE_DELAY_SECONDS": "10.0", "RETRY_MAX_DELAY_SECONDS": "5.0"},
            "RETRY_MAX_DELAY_SECONDS",
        ),
        ({"REQUEST_DELAY_SECONDS": "-1.0"}, "REQUEST_DELAY_SECONDS"),
        ({"INPUT_CSV_PATH": "   "}, "INPUT_CSV_PATH"),
        ({"OUTPUT_EXCEL_PATH": "output/reporte.csv"}, "OUTPUT_EXCEL_PATH"),
        ({"SQLITE_DB_PATH": ""}, "SQLITE_DB_PATH"),
        ({"LOG_FILE_PATH": "."}, "LOG_FILE_PATH"),
        ({"LOCK_FILE_PATH": ""}, "LOCK_FILE_PATH"),
        ({"LOG_LEVEL": "TRACE"}, "LOG_LEVEL"),
        ({"STATE_TTL_SECONDS": "-1.0"}, "STATE_TTL_SECONDS"),
        ({"STATE_TTL_SECONDS": "abc"}, "STATE_TTL_SECONDS"),
        ({"STATE_TTL_SECONDS": "nan"}, "STATE_TTL_SECONDS"),
    ],
)
def test_load_settings_rejects_invalid_values(
    env_overrides: dict[str, str], expected_fragment: str
) -> None:
    """Verifica que cualquier parámetro inválido lance ConfigurationError al arrancar."""
    with pytest.raises(ConfigurationError, match=expected_fragment):
        load_settings(environ=env_overrides)


def test_load_settings_parses_optional_state_ttl_seconds() -> None:
    """Verifica que STATE_TTL_SECONDS sea None por defecto y acepte flotantes >= 0."""
    assert load_settings(environ={}).state_ttl_seconds is None
    assert load_settings(environ={"STATE_TTL_SECONDS": "3600"}).state_ttl_seconds == 3600.0
    assert load_settings(environ={"STATE_TTL_SECONDS": "0"}).state_ttl_seconds == 0.0
