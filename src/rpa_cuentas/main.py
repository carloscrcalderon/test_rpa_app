"""Punto único de composición (Composition Root) del procesador batch."""

from __future__ import annotations

import contextlib
import logging
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path

from rpa_cuentas.adapters.inbound.cli.batch_cli import CliUsageError, parse_cli_args, run_cli
from rpa_cuentas.adapters.outbound.external_services.http_account_client import HttpAccountClient
from rpa_cuentas.adapters.outbound.file_io.csv_account_source import CsvAccountSource
from rpa_cuentas.adapters.outbound.file_io.excel_audit_report import ExcelReportWriter
from rpa_cuentas.adapters.outbound.persistence.file_execution_lock import FileExecutionLock
from rpa_cuentas.adapters.outbound.persistence.sqlite_batch_repository import SqliteBatchRepository
from rpa_cuentas.application.ports.inbound_ports import EXIT_FATAL_ERROR, ProcessBatchUseCasePort
from rpa_cuentas.application.use_cases.consolidate_accounts import ProcessBatchUseCase
from rpa_cuentas.config import ConfigurationError, Settings, load_settings
from rpa_cuentas.domain import RetryPolicy


@contextlib.contextmanager
def build_use_case(
    settings: Settings,
    logger: logging.Logger,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Iterator[ProcessBatchUseCasePort]:
    """Construye el grafo de dependencias del caso de uso y libera recursos al salir."""
    repository = SqliteBatchRepository(settings.sqlite_db_path)
    retry_policy = RetryPolicy(
        max_attempts=settings.retry_max_attempts,
        base_delay_seconds=settings.retry_base_delay_seconds,
        backoff_factor=settings.retry_backoff_factor,
        max_delay_seconds=settings.retry_max_delay_seconds,
    )
    external_client = HttpAccountClient(
        base_url=settings.external_api_base_url,
        timeout_seconds=settings.http_timeout_seconds,
        retry_policy=retry_policy,
        request_delay_seconds=settings.request_delay_seconds,
        sleep_fn=sleep_fn,
        logger=logger.getChild("adapters.http_account_client"),
    )
    use_case = ProcessBatchUseCase(
        source=CsvAccountSource(settings.input_csv_path),
        external_client=external_client,
        repository=repository,
        report_writer=ExcelReportWriter(settings.output_excel_path),
        execution_lock=FileExecutionLock(settings.lock_file_path),
        logger=logger.getChild("application"),
        state_ttl_seconds=settings.state_ttl_seconds,
    )
    try:
        yield use_case
    finally:
        external_client.close()
        repository.close()


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    env_file: Path | str | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    """Punto de composición que carga configuración y delega la conducción al adaptador CLI."""
    try:
        cli_args = parse_cli_args(argv)
        settings = load_settings(
            env_file=env_file, environ=environ, input_csv_override=cli_args.input_path
        )
    except (CliUsageError, ConfigurationError) as exc:
        print(f"ERROR FATAL DE CONFIGURACIÓN/CLI: {exc}", file=sys.stderr)
        return EXIT_FATAL_ERROR

    return run_cli(
        use_case_factory=lambda log: build_use_case(settings, log, sleep_fn=sleep_fn),
        default_input_path=settings.input_csv_path,
        log_file_path=settings.log_file_path,
        log_level=settings.log_level,
        cli_args=cli_args,
    )


def cli_entrypoint() -> None:
    """Punto de entrada para el ejecutable de consola y `python -m rpa_cuentas`."""
    raise SystemExit(main())
