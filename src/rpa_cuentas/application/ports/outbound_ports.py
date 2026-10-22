"""Puertos secundarios (outbound/driven) de la capa de aplicación."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from types import TracebackType
from typing import Protocol

from rpa_cuentas.application.dtos import BatchSummary, ReportRow
from rpa_cuentas.application.errors import (
    ConcurrentExecutionError,
    FatalBatchError,
    InputSourceError,
    PersistenceError,
    ReportPublicationError,
)
from rpa_cuentas.application.observability import EventLogger
from rpa_cuentas.domain import (
    AccountId,
    AccountStateRecord,
    AccountStatus,
    DeduplicatedAccountItem,
    ErrorCategory,
    ExternalFetchResult,
    IncidentReason,
    InputIncident,
    InvalidResolvedAccountDataError,
    RawAccountEntry,
    ResolvedAccountData,
    RetryPolicy,
    UtcTimestamp,
)


class AccountSourcePort(Protocol):
    """Puerto para la fuente de entradas crudas del lote."""

    def read_rows(self) -> Iterator[RawAccountEntry]:
        """Emite en flujo las entradas con su línea física, texto original y valor limpio."""


class ExternalAccountClientPort(Protocol):
    """Puerto para consultar una cuenta médica en el sistema externo."""

    def fetch_account(self, account_id: AccountId) -> ExternalFetchResult:
        """Consulta el identificador en el sistema externo y devuelve su resultado clasificado."""


class AccountStateRepositoryPort(Protocol):
    """Puerto de repositorio DDD para persistir y recuperar el agregado AccountStateRecord."""

    def get_account_state(self, account_id: AccountId) -> AccountStateRecord | None:
        """Obtiene el último estado persistido de una cuenta o None si nunca se ha procesado."""

    def save_account_state(self, record: AccountStateRecord) -> None:
        """Persiste de inmediato el estado de una cuenta médica (commit individual)."""


class BatchRunAuditPort(Protocol):
    """Puerto para registrar el ciclo de vida y plan de ejecución de una corrida batch."""

    def start_run(self, run_id: str, started_at: UtcTimestamp, input_path: str) -> None:
        """Registra el inicio de una ejecución batch."""

    def save_run_plan(
        self,
        run_id: str,
        items: Sequence[DeduplicatedAccountItem],
        incidents: Sequence[InputIncident],
    ) -> None:
        """Guarda las cuentas únicas y las incidencias detectadas en la ejecución actual."""

    def get_run_incidents(self, run_id: str) -> list[InputIncident]:
        """Obtiene las incidencias registradas para la ejecución en orden de línea física."""

    def finish_run(self, run_id: str, finished_at: UtcTimestamp, exit_code: int) -> None:
        """Registra la finalización y el código de salida de la ejecución batch."""


class BatchReportQueryPort(Protocol):
    """Puerto de consulta (CQRS Read Model) para proyectar las filas del reporte consolidado."""

    def get_report_rows(self, run_id: str) -> list[ReportRow]:
        """Obtiene las filas proyectadas para la hoja Resultados en orden de primera aparición."""


class BatchStateRepositoryPort(
    AccountStateRepositoryPort,
    BatchRunAuditPort,
    BatchReportQueryPort,
    Protocol,
):
    """Puerto para persistir y consultar el estado de las cuentas y de cada ejecución."""


class ReportWriterPort(Protocol):
    """Puerto para emitir de forma atómica el reporte consolidado."""

    def publish_report(
        self,
        rows: Sequence[ReportRow],
        incidents: Sequence[InputIncident],
        summary: BatchSummary,
    ) -> None:
        """Genera y publica atómicamente el reporte a partir del estado persistido."""


class ExecutionLockPort(Protocol):
    """Puerto para garantizar exclusión mutua durante la ejecución del lote."""

    def __enter__(self) -> object:
        """Adquiere el bloqueo exclusivo o lanza ConcurrentExecutionError."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None:
        """Libera el bloqueo exclusivo al terminar la ejecución."""


__all__ = [
    "AccountId",
    "AccountSourcePort",
    "AccountStateRecord",
    "AccountStateRepositoryPort",
    "AccountStatus",
    "BatchReportQueryPort",
    "BatchRunAuditPort",
    "BatchStateRepositoryPort",
    "BatchSummary",
    "ConcurrentExecutionError",
    "DeduplicatedAccountItem",
    "ErrorCategory",
    "EventLogger",
    "ExecutionLockPort",
    "ExternalAccountClientPort",
    "ExternalFetchResult",
    "FatalBatchError",
    "IncidentReason",
    "InputIncident",
    "InputSourceError",
    "InvalidResolvedAccountDataError",
    "PersistenceError",
    "RawAccountEntry",
    "ReportPublicationError",
    "ReportRow",
    "ReportWriterPort",
    "ResolvedAccountData",
    "RetryPolicy",
    "UtcTimestamp",
]
