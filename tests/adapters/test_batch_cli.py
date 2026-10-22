"""Pruebas unitarias para el adaptador primario CLI, punto de composición y simetría hexagonal."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from dirty_equals import HasAttributes

from rpa_cuentas.adapters.inbound.cli.batch_cli import (
    CliUsageError,
    close_logger_handlers,
    configure_logging,
    format_summary_text,
    parse_cli_args,
    run_cli,
)
from rpa_cuentas.adapters.outbound.external_services.http_account_client import HttpAccountClient
from rpa_cuentas.adapters.outbound.file_io.csv_account_source import CsvAccountSource
from rpa_cuentas.adapters.outbound.file_io.excel_audit_report import ExcelReportWriter
from rpa_cuentas.adapters.outbound.persistence.file_execution_lock import FileExecutionLock
from rpa_cuentas.adapters.outbound.persistence.sqlite_batch_repository import SqliteBatchRepository
from rpa_cuentas.application.ports import inbound_ports, outbound_ports
from rpa_cuentas.application.ports.inbound_ports import (
    ProcessBatchCommand,
    ProcessBatchUseCasePort,
)
from rpa_cuentas.application.ports.outbound_ports import FatalBatchError
from rpa_cuentas.application.use_cases.audit_summary import (
    EXIT_FATAL_ERROR,
    EXIT_PARTIAL_FAILURE,
    EXIT_SUCCESS,
    BatchSummary,
)
from rpa_cuentas.main import main
from tests.adapters.test_config import EXPECTED_ENV_KEYS
from tests.conftest import make_summary, make_test_env


class _StubUseCase:
    """Doble de prueba para ProcessBatchUseCasePort sin efectos secundarios de infraestructura."""

    def __init__(self, summary: BatchSummary | None = None, error: Exception | None = None) -> None:
        self._summary, self._error = summary, error
        self.received_commands: list[ProcessBatchCommand] = []

    def execute(self, command: ProcessBatchCommand) -> BatchSummary:
        """Registra el comando recibido y retorna el resumen o lanza la excepción configurada."""
        self.received_commands.append(command)
        if self._error is not None:
            raise self._error
        assert self._summary is not None
        return self._summary

    @contextlib.contextmanager
    def factory(self, _log: logging.Logger) -> Iterator[ProcessBatchUseCasePort]:
        """Provee este doble como factory compatible con UseCaseFactory."""
        yield self


def test_parse_cli_args_parses_run_with_and_without_input() -> None:
    """Verifica el parseo de `run` con y sin `--input`."""
    assert parse_cli_args(["run", "--input", "data/cuentas.csv"]) == HasAttributes(
        command="run", input_path="data/cuentas.csv"
    )
    assert parse_cli_args(["run"]) == HasAttributes(command="run", input_path=None)


def test_parse_cli_args_raises_cli_usage_error_on_invalid_arguments() -> None:
    """Verifica que argumentos inválidos o ausencia de subcomando lancen CliUsageError."""
    for bad_argv in ([], ["invalid-command"]):
        with pytest.raises(CliUsageError):
            parse_cli_args(bad_argv)


def test_format_summary_text_renders_all_expected_key_value_lines() -> None:
    """Verifica el formato de texto del resumen emitido por el adaptador CLI."""
    summary = make_summary(
        run_id="run-cli-01", lines_read=21, empty_lines=1, total_incidents=3,
        valid_lines=18, duplicates_collapsed=3, unique_accounts=15, queried_in_run=15,
        resolved_count=12, not_found_count=2, technical_failure_count=1, exit_code=1,
        invalid_lines_by_reason={"LINEA_VACIA": 1, "NO_ENTERO": 1, "NO_POSITIVO": 1},
        failures_by_cause={"TIMEOUT_AGOTADO": 1},
    )  # fmt: skip
    rendered = format_summary_text(summary)
    for expected in (
        "=== Resumen de Ejecución (run-cli-01) ===",
        "lineas_leidas=21",
        "detalle_fallos_por_causa=TIMEOUT_AGOTADO: 1",
        "codigo_salida=1",
    ):
        assert expected in rendered


def test_configure_logging_writes_run_id_in_every_log_line(tmp_path: Path) -> None:
    """Verifica que RotatingFileHandler escriba el run_id en cada línea del archivo de log."""
    log_path = tmp_path / "logs" / "rpa_cuentas.log"
    logger = configure_logging(log_file_path=log_path, log_level="INFO", run_id="run-xyz-123")
    logger.info("event=test_message detail=ok")
    close_logger_handlers(logger)

    content = log_path.read_text(encoding="utf-8")
    assert "[run_id=run-xyz-123]" in content and "event=test_message detail=ok" in content


def test_run_cli_drives_injected_use_case_port_without_secondary_infrastructure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verifica que run_cli conduzca ProcessBatchUseCasePort sin tocar SQLite/HTTP/Excel."""
    expected_summary = make_summary(
        run_id="run-stub-01", lines_read=10, valid_lines=10, duplicates_collapsed=2,
        unique_accounts=8, queried_in_run=8, resolved_count=7, technical_failure_count=1,
        invalid_lines_by_reason={"LINEA_VACIA": 0, "NO_ENTERO": 0, "NO_POSITIVO": 0},
        failures_by_cause={"TIMEOUT_AGOTADO": 1}, exit_code=EXIT_PARTIAL_FAILURE,
    )  # fmt: skip
    stub_use_case = _StubUseCase(summary=expected_summary)

    exit_code = run_cli(
        use_case_factory=stub_use_case.factory,
        argv=["run", "--input", "data/custom_input.csv"],
        log_file_path=tmp_path / "logs" / "rpa_cuentas.log",
    )

    assert exit_code == EXIT_PARTIAL_FAILURE
    assert [cmd.input_path for cmd in stub_use_case.received_commands] == ["data/custom_input.csv"]
    captured = capsys.readouterr()
    assert "=== Resumen de Ejecución (run-stub-01) ===" in captured.out
    assert "codigo_salida=1" in captured.out


def test_run_cli_translates_fatal_batch_error_to_exit_code_2(tmp_path: Path) -> None:
    """Verifica que run_cli capture FatalBatchError del puerto primario y retorne código 2."""
    stub_use_case = _StubUseCase(error=FatalBatchError("Fallo fatal simulado en puerto primario"))
    assert (
        run_cli(
            stub_use_case.factory,
            argv=["run"],
            log_file_path=tmp_path / "logs" / "rpa_cuentas.log",
        )
        == EXIT_FATAL_ERROR
    )


@pytest.mark.parametrize(
    ("argv", "env_overrides"),
    [
        (["run", "--input", "inexistente.csv"], {}),
        (["run"], {"EXTERNAL_API_BASE_URL": "ftp://url-invalida"}),
    ],
)
def test_main_returns_exit_code_2_on_missing_input_or_invalid_config(
    tmp_path: Path, argv: list[str], env_overrides: dict[str, str]
) -> None:
    """Verifica que main() retorne código 2 ante archivo inexistente o configuración inválida."""
    resolved_argv = [str(tmp_path / arg) if arg.endswith(".csv") else arg for arg in argv]
    assert main(resolved_argv, environ=make_test_env(tmp_path, **env_overrides)) == EXIT_FATAL_ERROR


def test_main_returns_exit_code_2_when_lock_is_already_held(tmp_path: Path) -> None:
    """Verifica que main() retorne código 2 cuando otra ejecución mantiene el archivo de bloqueo."""
    env = make_test_env(tmp_path)
    csv_path = tmp_path / "cuentas.csv"
    csv_path.write_text("id_cuenta\n7\n", encoding="utf-8")

    with FileExecutionLock(Path(env["LOCK_FILE_PATH"])):
        exit_code = main(argv=["run", "--input", str(csv_path)], environ=env)

    assert exit_code == EXIT_FATAL_ERROR


def test_main_reads_dotenv_from_working_directory_and_wires_all_adapters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifica que main(['run']) cargue .env del CWD y cablee todos los adaptadores."""
    for key in EXPECTED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)

    csv_dir = tmp_path / "dotenv_in"
    csv_dir.mkdir()
    (csv_dir / "lote_env.csv").write_text("id_cuenta\nabc\n", encoding="utf-8")

    (tmp_path / ".env").write_text(
        "INPUT_CSV_PATH=dotenv_in/lote_env.csv\n"
        "OUTPUT_EXCEL_PATH=dotenv_out/reporte_env.xlsx\n"
        "SQLITE_DB_PATH=dotenv_state/estado_env.db\n"
        "LOG_FILE_PATH=dotenv_logs/corrida_env.log\n"
        "LOCK_FILE_PATH=dotenv_state/corrida_env.lock\n"
        "LOG_LEVEL=INFO\n",
        encoding="utf-8",
    )

    exit_code = main(["run"])

    assert exit_code == EXIT_SUCCESS
    assert (tmp_path / "dotenv_out" / "reporte_env.xlsx").is_file()
    assert (tmp_path / "dotenv_state" / "estado_env.db").is_file()
    assert (tmp_path / "dotenv_logs" / "corrida_env.log").is_file()


def test_hexagonal_inbound_outbound_symmetry_and_dependency_direction() -> None:
    """Verifica Screaming Architecture, simetría inbound/outbound y dirección de dependencias."""
    assert all((
        inbound_ports.ProcessBatchCommand, inbound_ports.ProcessBatchUseCasePort,
        outbound_ports.BatchStateRepositoryPort, run_cli, FileExecutionLock,
        CsvAccountSource, ExcelReportWriter, HttpAccountClient, SqliteBatchRepository,
    ))  # fmt: skip

    src_root = Path(__file__).resolve().parents[2] / "src" / "rpa_cuentas"
    forbidden_generic_or_leaked_files = (
        src_root / "domain" / "models.py",
        src_root / "domain" / "policies.py",
        src_root / "application" / "ports.py",
        src_root / "application" / "process_batch.py",
        src_root / "application" / "summary.py",
        src_root / "adapters" / "inbound" / "cli.py",
        src_root / "adapters" / "outbound" / "posts_client.py",
    )
    for forbidden_path in forbidden_generic_or_leaked_files:
        assert not forbidden_path.exists(), f"Archivo no permitido: {forbidden_path}"

    loose_adapter_files = [
        p.name for p in (src_root / "adapters").glob("*.py") if p.name != "__init__.py"
    ]
    assert loose_adapter_files == []

    for layer in ("domain", "application"):
        for py_file in sorted((src_root / layer).rglob("*.py")):
            source_text = py_file.read_text(encoding="utf-8")
            assert "rpa_cuentas.adapters" not in source_text, (
                f"Violación de dirección de dependencias en {py_file}: importa adapters"
            )
            assert "rpa_cuentas.main" not in source_text, (
                f"Violación de dirección de dependencias en {py_file}: importa main"
            )
