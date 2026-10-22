"""Configuración operativa externa validada al arranque."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

VALID_LOG_LEVELS: frozenset[str] = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class ConfigurationError(ValueError):
    """Error fatal de validación o lectura de la configuración operativa."""


def _parse_float(env: Mapping[str, str], var_name: str, default: str) -> float:
    """Obtiene y convierte una variable en flotante finito o lanza ConfigurationError."""
    raw_value = env.get(var_name, default)
    try:
        parsed = float(raw_value.strip())
    except ValueError as exc:
        raise ConfigurationError(
            f"La variable {var_name} debe ser un número decimal válido, se recibió: {raw_value!r}."
        ) from exc
    if not math.isfinite(parsed):
        raise ConfigurationError(
            f"La variable {var_name} debe ser un número finito, se recibió: {raw_value!r}."
        )
    return parsed


def _parse_int(env: Mapping[str, str], var_name: str, default: str) -> int:
    """Obtiene y convierte una variable en entero en base 10 o lanza ConfigurationError."""
    raw_value = env.get(var_name, default)
    try:
        return int(raw_value.strip(), 10)
    except ValueError as exc:
        raise ConfigurationError(
            f"La variable {var_name} debe ser un número entero válido, se recibió: {raw_value!r}."
        ) from exc


def _parse_non_empty_path(raw_value: str | Path, var_name: str) -> Path:
    """Valida que una ruta no sea vacía y devuelve un objeto Path."""
    text = str(raw_value).strip()
    if not text or text == ".":
        raise ConfigurationError(f"La ruta configurada en {var_name} no puede estar vacía.")
    return Path(text)


@dataclass(frozen=True)
class Settings:
    """Parámetros operativos inmutables del procesador batch."""

    external_api_base_url: str = "https://jsonplaceholder.typicode.com"
    http_timeout_seconds: float = 10.0
    retry_max_attempts: int = 4
    retry_base_delay_seconds: float = 1.0
    retry_backoff_factor: float = 2.0
    retry_max_delay_seconds: float = 30.0
    request_delay_seconds: float = 0.0
    input_csv_path: Path = Path("data/cuentas.csv")
    output_excel_path: Path = Path("output/consolidado_cuentas.xlsx")
    sqlite_db_path: Path = Path("state/rpa_cuentas.db")
    log_file_path: Path = Path("logs/rpa_cuentas.log")
    lock_file_path: Path = Path("state/rpa_cuentas.lock")
    log_level: str = "INFO"
    state_ttl_seconds: float | None = None

    def __post_init__(self) -> None:
        url = self.external_api_base_url.strip().rstrip("/")
        if not url.startswith(("http://", "https://")) or url in {"http://", "https://"}:
            raise ConfigurationError(
                "EXTERNAL_API_BASE_URL debe ser una URL no vacía que comience con "
                f"'http://' o 'https://', se recibió: {self.external_api_base_url!r}."
            )
        object.__setattr__(self, "external_api_base_url", url)

        base = self.retry_base_delay_seconds
        for val, var_name, min_val in (
            (self.http_timeout_seconds, "HTTP_TIMEOUT_SECONDS", 0.0),
            (self.retry_max_attempts, "RETRY_MAX_ATTEMPTS", 1),
            (base, "RETRY_BASE_DELAY_SECONDS", 0.0),
            (self.retry_backoff_factor, "RETRY_BACKOFF_FACTOR", 1.0),
            (self.retry_max_delay_seconds, "RETRY_MAX_DELAY_SECONDS", base),
            (self.request_delay_seconds, "REQUEST_DELAY_SECONDS", 0.0),
        ):
            invalid = val <= 0 if var_name == "HTTP_TIMEOUT_SECONDS" else val < min_val
            if isinstance(val, bool) or invalid:
                raise ConfigurationError(f"Valor fuera de rango en {var_name}: {val!r}.")

        for attr, var_name in (
            ("input_csv_path", "INPUT_CSV_PATH"),
            ("output_excel_path", "OUTPUT_EXCEL_PATH"),
            ("sqlite_db_path", "SQLITE_DB_PATH"),
            ("log_file_path", "LOG_FILE_PATH"),
            ("lock_file_path", "LOCK_FILE_PATH"),
        ):
            object.__setattr__(self, attr, _parse_non_empty_path(getattr(self, attr), var_name))

        if self.output_excel_path.suffix.lower() != ".xlsx":
            raise ConfigurationError(
                f"OUTPUT_EXCEL_PATH debe tener extensión '.xlsx', "
                f"se recibió: {self.output_excel_path}."
            )

        level = self.log_level.strip().upper()
        if level not in VALID_LOG_LEVELS:
            raise ConfigurationError(
                f"LOG_LEVEL debe ser uno de {sorted(VALID_LOG_LEVELS)}, "
                f"se recibió: {self.log_level!r}."
            )
        object.__setattr__(self, "log_level", level)

        if self.state_ttl_seconds is not None and (
            isinstance(self.state_ttl_seconds, bool)
            or not isinstance(self.state_ttl_seconds, (int, float))
            or not math.isfinite(self.state_ttl_seconds)
            or self.state_ttl_seconds < 0.0
        ):
            raise ConfigurationError(
                f"Valor fuera de rango en STATE_TTL_SECONDS: {self.state_ttl_seconds!r}."
            )


def load_settings(
    env_file: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
    input_csv_override: Path | str | None = None,
) -> Settings:
    """Carga Settings desde un archivo .env y variables de entorno, validando todos los campos."""
    env: dict[str, str] = {}
    dotenv_path = (
        Path(env_file)
        if env_file is not None
        else (Path(".env") if environ is None and Path(".env").is_file() else None)
    )
    if dotenv_path is not None:
        env.update(
            {k: v for k, v in dotenv_values(dotenv_path=dotenv_path).items() if v is not None}
        )

    env.update(os.environ if environ is None else environ)
    input_raw: str | Path = (
        input_csv_override
        if input_csv_override is not None
        else env.get("INPUT_CSV_PATH", "data/cuentas.csv")
    )

    raw_ttl = env.get("STATE_TTL_SECONDS", "").strip()
    state_ttl = _parse_float(env, "STATE_TTL_SECONDS", raw_ttl) if raw_ttl else None

    return Settings(
        external_api_base_url=env.get(
            "EXTERNAL_API_BASE_URL", "https://jsonplaceholder.typicode.com"
        ),
        http_timeout_seconds=_parse_float(env, "HTTP_TIMEOUT_SECONDS", "10.0"),
        retry_max_attempts=_parse_int(env, "RETRY_MAX_ATTEMPTS", "4"),
        retry_base_delay_seconds=_parse_float(env, "RETRY_BASE_DELAY_SECONDS", "1.0"),
        retry_backoff_factor=_parse_float(env, "RETRY_BACKOFF_FACTOR", "2.0"),
        retry_max_delay_seconds=_parse_float(env, "RETRY_MAX_DELAY_SECONDS", "30.0"),
        request_delay_seconds=_parse_float(env, "REQUEST_DELAY_SECONDS", "0.0"),
        input_csv_path=input_raw,  # type: ignore[arg-type]
        output_excel_path=env.get("OUTPUT_EXCEL_PATH", "output/consolidado_cuentas.xlsx"),  # type: ignore[arg-type]
        sqlite_db_path=env.get("SQLITE_DB_PATH", "state/rpa_cuentas.db"),  # type: ignore[arg-type]
        log_file_path=env.get("LOG_FILE_PATH", "logs/rpa_cuentas.log"),  # type: ignore[arg-type]
        lock_file_path=env.get("LOCK_FILE_PATH", "state/rpa_cuentas.lock"),  # type: ignore[arg-type]
        log_level=env.get("LOG_LEVEL", "INFO"),
        state_ttl_seconds=state_ttl,
    )
