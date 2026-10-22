"""Adaptador primario CLI con argparse, formateo de salida y configuración de logging rotativo."""

from __future__ import annotations

import argparse
import contextlib
import logging
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import NoReturn

from rpa_cuentas.application.ports.inbound_ports import (
    EXIT_FATAL_ERROR,
    BatchSummary,
    EventLogger,
    FatalBatchError,
    ProcessBatchCommand,
    ProcessBatchUseCasePort,
)

LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

UseCaseFactory = Callable[
    [logging.Logger], contextlib.AbstractContextManager[ProcessBatchUseCasePort]
]


class CliUsageError(FatalBatchError):
    """Error fatal por argumentos inválidos o faltantes en la línea de comandos."""


@dataclass(frozen=True)
class CliArgs:
    """Argumentos validados desde la línea de comandos."""

    command: str
    input_path: str | None


class _RaisingArgumentParser(argparse.ArgumentParser):
    """ArgumentParser que lanza CliUsageError en lugar de invocar sys.exit directamente."""

    def error(self, message: str) -> NoReturn:
        raise CliUsageError(f"Argumentos CLI inválidos: {message}")


def generate_run_id() -> str:
    """Genera un identificador único de ejecución con marca de tiempo UTC."""
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"run-{timestamp}-{uuid.uuid4().hex[:8]}"


def format_summary_text(summary: BatchSummary) -> str:
    """Formatea el resumen final de ejecución para salida estándar y archivo de log."""
    return "\n".join(
        [
            f"=== Resumen de Ejecución ({summary.run_id}) ===",
            *(f"{k}={v}" for k, v in summary.to_metric_rows(empty_causes_label="ninguno")),
        ]
    )


def build_arg_parser() -> argparse.ArgumentParser:
    """Construye el parser de argumentos CLI para `python -m rpa_cuentas run`."""
    parser = _RaisingArgumentParser(
        prog="rpa_cuentas", description="Procesador batch desatendido de cuentas médicas."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser(
        "run", help="Ejecuta el procesamiento batch de cuentas médicas."
    )
    run_parser.add_argument(
        "--input",
        dest="input_path",
        type=str,
        default=None,
        help="Ruta del archivo CSV de entrada (sobreescribe INPUT_CSV_PATH).",
    )
    return parser


def parse_cli_args(argv: Sequence[str] | None = None) -> CliArgs:
    """Parsea los argumentos de línea de comandos y retorna CliArgs o lanza CliUsageError."""
    namespace = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    command = getattr(namespace, "command", None)
    if command != "run":
        raise CliUsageError(f"Subcomando no soportado: {command!r}. Use 'run'.")
    return CliArgs(command="run", input_path=getattr(namespace, "input_path", None))


def configure_logging(log_file_path: Path | str, log_level: str, run_id: str) -> logging.Logger:
    """Configura un logger con RotatingFileHandler y formato que incluye run_id."""
    path = Path(log_file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("rpa_cuentas")
    close_logger_handlers(logger)

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(numeric_level)
    logger.propagate = False

    handler = RotatingFileHandler(
        filename=str(path), maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    handler.setLevel(numeric_level)
    handler.addFilter(lambda record: setattr(record, "run_id", run_id) or True)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s [run_id=%(run_id)s] %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    logger.addHandler(handler)
    return logger


def close_logger_handlers(logger: logging.Logger) -> None:
    """Cierra y desvincula todos los handlers del logger indicado."""
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.flush()
        handler.close()


def run_cli(
    use_case_factory: UseCaseFactory,
    argv: Sequence[str] | None = None,
    default_input_path: Path | str = "data/cuentas.csv",
    log_file_path: Path | str | None = None,
    log_level: str = "INFO",
    cli_args: CliArgs | None = None,
) -> int:
    """Controlador primario CLI: parsea argumentos, ejecuta el caso de uso y formatea salida."""
    if cli_args is None:
        try:
            cli_args = parse_cli_args(argv)
        except CliUsageError as exc:
            print(f"ERROR FATAL DE CONFIGURACIÓN/CLI: {exc}", file=sys.stderr)
            return EXIT_FATAL_ERROR

    run_id = generate_run_id()
    input_path = cli_args.input_path or str(default_input_path)
    if log_file_path is not None:
        try:
            logger = configure_logging(log_file_path, log_level, run_id)
        except OSError as exc:
            print(
                f"ERROR FATAL DE LOGGING [{run_id}]: No fue posible abrir '{log_file_path}': {exc}",
                file=sys.stderr,
            )
            return EXIT_FATAL_ERROR
    else:
        logger = logging.getLogger("rpa_cuentas")

    log = EventLogger(logger)
    try:
        with use_case_factory(logger) as use_case:
            summary = use_case.execute(ProcessBatchCommand(run_id=run_id, input_path=input_path))
        summary_text = format_summary_text(summary)
        log("summary_emitted", summary_text.replace("\n", " | "))
        print(summary_text)
        return summary.exit_code
    except FatalBatchError as exc:
        log("fatal_batch_error", level="error", detail=exc)
        print(f"ERROR FATAL [{run_id}]: {exc}", file=sys.stderr)
        return EXIT_FATAL_ERROR
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log("unexpected_fatal_error", level="exception", detail=exc)
        print(f"ERROR FATAL INESPERADO [{run_id}]: {exc}", file=sys.stderr)
        return EXIT_FATAL_ERROR
    finally:
        if log_file_path is not None:
            close_logger_handlers(logger)
