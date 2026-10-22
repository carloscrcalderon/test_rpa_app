"""Pruebas unitarias del caso de uso ProcessBatchUseCase con dobles en memoria."""

from __future__ import annotations

import ast
import contextlib
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import TracebackType

import pytest
from dirty_equals import HasAttributes

from rpa_cuentas.application.ports.inbound_ports import ProcessBatchCommand
from rpa_cuentas.application.ports.outbound_ports import (
    AccountStateRepositoryPort,
    BatchReportQueryPort,
    BatchRunAuditPort,
    BatchStateRepositoryPort,
    ConcurrentExecutionError,
    ReportRow,
)
from rpa_cuentas.application.use_cases.audit_summary import (
    EXIT_PARTIAL_FAILURE,
    EXIT_SUCCESS,
    BatchSummary,
)
from rpa_cuentas.application.use_cases.consolidate_accounts import ProcessBatchUseCase
from rpa_cuentas.domain import (
    AccountId,
    AccountStateRecord,
    AccountStatus,
    DeduplicatedAccountItem,
    ErrorCategory,
    ExternalFetchResult,
    IncidentReason,
    InputIncident,
    RawAccountEntry,
    ResolvedAccountData,
    UtcTimestamp,
)
from tests.conftest import REFERENCE_CSV_VALUES, make_reference_entries


class InMemoryAccountSource:
    """Doble en memoria para AccountSourcePort."""

    def __init__(self, values: Sequence[str] = REFERENCE_CSV_VALUES) -> None:
        self._values = tuple(values)
        self.read_calls = 0

    def read_rows(self) -> Iterator[RawAccountEntry]:
        """Emite entradas pre-normalizadas numerando desde la línea física 2."""
        self.read_calls += 1
        yield from make_reference_entries(self._values)


class FakeExternalAccountClient:
    """Doble en memoria para ExternalAccountClientPort con respuestas configurables."""

    def __init__(
        self,
        not_found_ids: set[int] | None = None,
        failing_causes: dict[int, tuple[str, int]] | None = None,
        exploding_ids: set[int] | None = None,
    ) -> None:
        self.not_found_ids = not_found_ids if not_found_ids is not None else {101, 150}
        self.failing_causes = failing_causes or {}
        self.exploding_ids = exploding_ids or set()
        self.queried_ids: list[int] = []

    def fetch_account(self, account_id: AccountId) -> ExternalFetchResult:
        """Simula la consulta externa registrando cada identificador invocado."""
        acc_val = account_id.value
        self.queried_ids.append(acc_val)

        if acc_val in self.exploding_ids:
            raise RuntimeError(f"Error inesperado simulado en cliente para cuenta {acc_val}")
        if acc_val in self.failing_causes:
            cause, attempts = self.failing_causes[acc_val]
            return ExternalFetchResult.technical_failure(
                attempts, cause, ErrorCategory.TECNICO_TRANSITORIO
            )
        if acc_val in self.not_found_ids:
            return ExternalFetchResult.not_found(1)
        return ExternalFetchResult.resolved(
            1,
            ResolvedAccountData(
                (acc_val % 10) + 1,
                f"Título cuenta {acc_val}",
                f"Detalle cuerpo cuenta {acc_val}",
            ),
        )


class InMemoryBatchStateRepository:
    """Doble en memoria para BatchStateRepositoryPort."""

    def __init__(
        self,
        stop_after_saves: int | None = None,
        max_retained_runs: int = 100,
    ) -> None:
        self.accounts: dict[AccountId, AccountStateRecord] = {}
        self.runs: dict[str, dict[str, object]] = {}
        self.run_accounts: dict[str, list[DeduplicatedAccountItem]] = {}
        self.run_incidents: dict[str, list[InputIncident]] = {}
        self.stop_after_saves = stop_after_saves
        self.max_retained_runs = max_retained_runs
        self.saves_in_current_run = 0

    def start_run(self, run_id: str, started_at: UtcTimestamp, input_path: str) -> None:
        """Registra el inicio de la ejecución en memoria."""
        self.runs[run_id] = {
            "started_at": started_at.value, "finished_at": None,
            "input_path": input_path, "exit_code": None,
        }  # fmt: skip
        self.saves_in_current_run = 0

    def save_run_plan(
        self,
        run_id: str,
        items: Sequence[DeduplicatedAccountItem],
        incidents: Sequence[InputIncident],
    ) -> None:
        """Almacena el plan deduplicado y las incidencias de la ejecución."""
        self.run_accounts[run_id] = list(items)
        self.run_incidents[run_id] = list(incidents)

    def get_account_state(self, account_id: AccountId) -> AccountStateRecord | None:
        """Retorna el estado persistido de la cuenta si existe."""
        return self.accounts.get(account_id)

    def save_account_state(self, record: AccountStateRecord) -> None:
        """Guarda el estado de la cuenta y permite simular caída a mitad de lote."""
        self.accounts[record.account_id] = record
        self.saves_in_current_run += 1
        if self.stop_after_saves is not None and self.saves_in_current_run >= self.stop_after_saves:
            raise KeyboardInterrupt("Interrupción abrupta simulada a mitad del lote")

    def get_report_rows(self, run_id: str) -> list[ReportRow]:
        """Construye las filas semánticas del reporte en orden de primera aparición."""
        items = sorted(self.run_accounts.get(run_id, []), key=lambda item: item.first_seen_order)
        return [
            ReportRow(
                item.account_id,
                state.status,
                None if state.status == AccountStatus.RESUELTA else state.cause,
                item.occurrences,
                state.user_id,
                state.title,
                state.body,
                state.attempts,
                (state.resolved_at or state.last_attempt_at).value,
            )  # fmt: skip
            for item in items
            if (state := self.accounts.get(item.account_id)) is not None
        ]

    def get_run_incidents(self, run_id: str) -> list[InputIncident]:
        """Devuelve las incidencias de la ejecución ordenadas por línea física."""
        return sorted(self.run_incidents.get(run_id, []), key=lambda inc: inc.line_number)

    def finish_run(self, run_id: str, finished_at: UtcTimestamp, exit_code: int) -> None:
        """Marca la ejecución como finalizada y poda corridas antiguas excedentes."""
        self.runs[run_id]["finished_at"] = finished_at.value
        self.runs[run_id]["exit_code"] = exit_code
        while len(self.runs) > self.max_retained_runs:
            oldest_run_id = next(iter(self.runs))
            del self.runs[oldest_run_id]
            self.run_accounts.pop(oldest_run_id, None)
            self.run_incidents.pop(oldest_run_id, None)


class InMemoryReportWriter:
    """Doble en memoria para ReportWriterPort."""

    def __init__(self) -> None:
        self.published_rows: list[ReportRow] = []
        self.published_incidents: list[InputIncident] = []
        self.published_summary: BatchSummary | None = None
        self.publish_count: int = 0

    def publish_report(
        self,
        rows: Sequence[ReportRow],
        incidents: Sequence[InputIncident],
        summary: BatchSummary,
    ) -> None:
        """Captura los datos publicados para su inspección en las pruebas."""
        self.published_rows = list(rows)
        self.published_incidents = list(incidents)
        self.published_summary = summary
        self.publish_count += 1


class RejectingExecutionLock:
    """Doble de bloqueo que simula otra corrida en curso lanzando ConcurrentExecutionError."""

    def __init__(self) -> None:
        self.enter_attempts = 0
        self.exit_calls = 0

    def __enter__(self) -> RejectingExecutionLock:
        self.enter_attempts += 1
        raise ConcurrentExecutionError("Bloqueo activo en otra instancia simulada")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.exit_calls += 1


def _run_batch(
    source: InMemoryAccountSource,
    client: FakeExternalAccountClient,
    repository: InMemoryBatchStateRepository,
    writer: InMemoryReportWriter,
    run_id: str = "run-001",
    ts: str = "2026-10-21T03:00:00Z",
) -> BatchSummary:
    """Ejecuta ProcessBatchUseCase con los dobles en memoria suministrados."""
    return ProcessBatchUseCase(source, client, repository, writer, clock=lambda: ts).execute(
        ProcessBatchCommand(run_id=run_id, input_path="data/cuentas.csv")
    )


def test_process_batch_reference_run_and_idempotent_rerun() -> None:
    """Verifica primera corrida limpia (15 consultas) y segunda idempotente (0 consultas)."""
    source, repository = InMemoryAccountSource(), InMemoryBatchStateRepository()
    client_run_1, writer_run_1 = FakeExternalAccountClient(), InMemoryReportWriter()

    summary_1 = _run_batch(source, client_run_1, repository, writer_run_1, "run-001")

    assert summary_1 == HasAttributes(
        lines_read=21, empty_lines=1, total_incidents=3, valid_lines=18,
        duplicates_collapsed=3, unique_accounts=15, queried_in_run=15, reused_from_state=0,
        resolved_count=13, not_found_count=2, technical_failure_count=0, exit_code=EXIT_SUCCESS,
        invalid_lines_by_reason={
            IncidentReason.LINEA_VACIA.value: 1,
            IncidentReason.NO_ENTERO.value: 1,
            IncidentReason.NO_POSITIVO.value: 1,
        },
    )  # fmt: skip
    assert len(client_run_1.queried_ids) == 15
    assert len(writer_run_1.published_rows) == 15 and len(writer_run_1.published_incidents) == 3

    assert writer_run_1.published_rows[0] == HasAttributes(
        id_cuenta=AccountId(7), estado=AccountStatus.RESUELTA, causa=None, apariciones=3
    )
    assert writer_run_1.published_rows[1] == HasAttributes(id_cuenta=AccountId(12), apariciones=2)

    # Segunda ejecución inmediata: 0 consultas externas y mismo contenido en el reporte
    client_run_2, writer_run_2 = FakeExternalAccountClient(), InMemoryReportWriter()
    summary_2 = _run_batch(source, client_run_2, repository, writer_run_2, "run-002")

    assert client_run_2.queried_ids == []
    assert summary_2 == HasAttributes(
        queried_in_run=0, reused_from_state=15, resolved_count=13,
        not_found_count=2, technical_failure_count=0, exit_code=EXIT_SUCCESS,
    )  # fmt: skip
    assert writer_run_2.published_rows == writer_run_1.published_rows


def test_process_batch_isolates_failures_and_retries_only_failed_on_next_run() -> None:
    """Verifica aislamiento de fallos individuales (código 1) y recuperación selectiva posterior."""
    source, repository = InMemoryAccountSource(), InMemoryBatchStateRepository()
    failing_client = FakeExternalAccountClient(
        failing_causes={45: ("TIMEOUT_AGOTADO", 4)}, exploding_ids={99}
    )
    writer_1 = InMemoryReportWriter()

    summary_1 = _run_batch(source, failing_client, repository, writer_1, "run-fail")

    assert summary_1 == HasAttributes(
        queried_in_run=15, resolved_count=11, not_found_count=2,
        technical_failure_count=2, exit_code=EXIT_PARTIAL_FAILURE,
    )  # fmt: skip
    assert summary_1.failures_by_cause["TIMEOUT_AGOTADO"] == 1
    assert "ERROR_INESPERADO_CLIENTE" in summary_1.format_failures_by_cause()

    # Relanzamiento con servicio recuperado: solo debe consultar las cuentas 45 y 99
    recovered_client, writer_2 = FakeExternalAccountClient(), InMemoryReportWriter()
    summary_2 = _run_batch(source, recovered_client, repository, writer_2, "run-recovered")

    assert recovered_client.queried_ids == [45, 99]
    assert summary_2 == HasAttributes(
        queried_in_run=2, reused_from_state=13, resolved_count=13,
        not_found_count=2, technical_failure_count=0, exit_code=EXIT_SUCCESS,
    )  # fmt: skip


def test_process_batch_refreshes_terminal_accounts_when_state_ttl_expires() -> None:
    """Verifica que cuentas RESUELTA y NO_EXISTE se refresquen al superar state_ttl_seconds."""
    source = InMemoryAccountSource(values=("7", "101"))
    repository = InMemoryBatchStateRepository()
    client_1, writer_1 = FakeExternalAccountClient(not_found_ids={101}), InMemoryReportWriter()

    ProcessBatchUseCase(
        source, client_1, repository, writer_1,
        clock=lambda: "2026-10-21T03:00:00Z", state_ttl_seconds=3600.0,
    ).execute(ProcessBatchCommand(run_id="run-ttl-1", input_path="data/cuentas.csv"))  # fmt: skip
    assert client_1.queried_ids == [7, 101]

    # Corrida dentro del TTL (30 min después): reutiliza ambas cuentas
    client_2, writer_2 = FakeExternalAccountClient(not_found_ids=set()), InMemoryReportWriter()
    summary_fresh = ProcessBatchUseCase(
        source, client_2, repository, writer_2,
        clock=lambda: "2026-10-21T03:30:00Z", state_ttl_seconds=3600.0,
    ).execute(ProcessBatchCommand(run_id="run-ttl-2", input_path="data/cuentas.csv"))  # fmt: skip
    assert client_2.queried_ids == []
    assert summary_fresh == HasAttributes(queried_in_run=0, reused_from_state=2, not_found_count=1)

    # Corrida tras expirar el TTL (2 horas después): refresca 7 y 101 (101 pasa a RESUELTA)
    client_3, writer_3 = FakeExternalAccountClient(not_found_ids=set()), InMemoryReportWriter()
    summary_expired = ProcessBatchUseCase(
        source, client_3, repository, writer_3,
        clock=lambda: "2026-10-21T05:00:00Z", state_ttl_seconds=3600.0,
    ).execute(ProcessBatchCommand(run_id="run-ttl-3", input_path="data/cuentas.csv"))  # fmt: skip
    assert client_3.queried_ids == [7, 101]
    assert summary_expired == HasAttributes(
        queried_in_run=2, reused_from_state=0, resolved_count=2, not_found_count=0
    )


def test_process_batch_resumes_from_pending_accounts_after_interruption() -> None:
    """Verifica que tras una interrupción solo se consulten las cuentas pendientes."""
    source, repository = InMemoryAccountSource(), InMemoryBatchStateRepository(stop_after_saves=5)
    client_1, writer_1 = FakeExternalAccountClient(), InMemoryReportWriter()

    with contextlib.suppress(KeyboardInterrupt):
        _run_batch(source, client_1, repository, writer_1, "run-interrupted")

    assert len(client_1.queried_ids) == 5 and writer_1.publish_count == 0

    # Al relanzar sin interrupción, reutiliza las 5 guardadas y consulta las 10 restantes
    repository.stop_after_saves = None
    client_2, writer_2 = FakeExternalAccountClient(), InMemoryReportWriter()
    summary_resumed = _run_batch(source, client_2, repository, writer_2, "run-resumed")

    assert len(client_2.queried_ids) == 10
    assert summary_resumed == HasAttributes(
        queried_in_run=10, reused_from_state=5, resolved_count=13,
        not_found_count=2, technical_failure_count=0, exit_code=EXIT_SUCCESS,
    )  # fmt: skip


def test_process_batch_use_case_enforces_execution_lock_before_touching_ports() -> None:
    """Verifica que ProcessBatchUseCase adquiera el bloqueo antes de leer o escribir estado."""
    source, repository = InMemoryAccountSource(), InMemoryBatchStateRepository()
    client, writer, rejecting_lock = (
        FakeExternalAccountClient(), InMemoryReportWriter(), RejectingExecutionLock(),
    )  # fmt: skip

    use_case = ProcessBatchUseCase(source, client, repository, writer, rejecting_lock)

    with pytest.raises(ConcurrentExecutionError, match="Bloqueo activo"):
        use_case.execute(ProcessBatchCommand(run_id="run-locked", input_path="data/cuentas.csv"))

    assert (rejecting_lock.enter_attempts, source.read_calls, writer.publish_count) == (1, 0, 0)
    assert repository.runs == {} and client.queried_ids == []


def test_application_dtos_validate_invariants() -> None:
    """Verifica que ReportRow y BatchSummary auto-validen sus invariantes en __post_init__."""
    with pytest.raises(ValueError):
        ReportRow(
            id_cuenta=AccountId(7),
            estado=AccountStatus.RESUELTA,
            causa=None,
            apariciones=1,
            user_id=1,
            titulo="T",
            cuerpo="B",
            intentos=0,
            fecha_consulta="2026-10-21T04:00:00Z",
        )

    with pytest.raises(ValueError):
        BatchSummary(
            run_id="",
            lines_read=0,
            empty_lines=0,
            invalid_lines_by_reason={},
            total_incidents=0,
            valid_lines=0,
            duplicates_collapsed=0,
            unique_accounts=0,
            queried_in_run=0,
            reused_from_state=0,
            resolved_count=0,
            not_found_count=0,
            technical_failure_count=0,
            failures_by_cause={},
            exit_code=EXIT_SUCCESS,
        )


def test_hexagonal_dependency_inversion_and_import_boundaries() -> None:
    """Verifica por AST que las capas respeten DIP y las fronteras de importación."""
    src_root = Path(__file__).resolve().parents[2] / "src" / "rpa_cuentas"

    def _imported_modules(py_file: Path) -> list[str]:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
        return modules

    for py_file in (src_root / "domain").rglob("*.py"):
        for mod in _imported_modules(py_file):
            assert not mod.startswith(
                ("rpa_cuentas.application", "rpa_cuentas.adapters", "rpa_cuentas.config")
            ), f"Violación en domain ({py_file.name}): importa {mod}"

    for py_file in (src_root / "application").rglob("*.py"):
        for mod in _imported_modules(py_file):
            assert not mod.startswith(
                ("rpa_cuentas.adapters", "rpa_cuentas.config", "rpa_cuentas.main")
            ), f"Violación en application ({py_file.name}): importa {mod}"

    for py_file in (src_root / "application" / "ports").rglob("*.py"):
        for mod in _imported_modules(py_file):
            assert not mod.startswith(
                ("rpa_cuentas.application.use_cases", "rpa_cuentas.application.ports")
            ), f"Violación en ports ({py_file.name}): importa {mod}"

    for py_file in (src_root / "adapters").rglob("*.py"):
        for mod in _imported_modules(py_file):
            assert not mod.startswith(
                (
                    "rpa_cuentas.domain",
                    "rpa_cuentas.application.use_cases",
                    "rpa_cuentas.config",
                    "rpa_cuentas.main",
                )
            ), f"Violación en adapter ({py_file.name}): importa {mod}"
            if "inbound" in py_file.parts:
                assert not mod.startswith("rpa_cuentas.application.ports.outbound_ports"), (
                    f"Violación en inbound adapter ({py_file.name}): importa {mod}"
                )
            if "outbound" in py_file.parts:
                assert not mod.startswith("rpa_cuentas.application.ports.inbound_ports"), (
                    f"Violación en outbound adapter ({py_file.name}): importa {mod}"
                )

    root_init_tree = ast.parse((src_root / "__init__.py").read_text(encoding="utf-8"))
    non_docstring_nodes = [
        node
        for node in root_init_tree.body
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
    ]
    assert non_docstring_nodes == [], "src/rpa_cuentas/__init__.py debe permanecer sin código"

    assert {
        AccountStateRepositoryPort,
        BatchRunAuditPort,
        BatchReportQueryPort,
    }.issubset(BatchStateRepositoryPort.__mro__)

    tests_root = Path(__file__).resolve().parents[1]
    for test_target in (
        tests_root / "conftest.py",
        *(tests_root / "domain").rglob("*.py"),
        *(tests_root / "application").rglob("*.py"),
    ):
        for mod in _imported_modules(test_target):
            assert not mod.startswith("rpa_cuentas.adapters"), (
                f"Violación de aislamiento en prueba ({test_target.name}): importa {mod}"
            )
