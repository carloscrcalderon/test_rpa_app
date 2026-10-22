"""Caso de uso principal: orquestación del procesamiento batch de cuentas médicas."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from rpa_cuentas.application.observability import EventLogger
from rpa_cuentas.application.ports.inbound_ports import (
    ProcessBatchCommand,
    ProcessBatchUseCasePort,
)
from rpa_cuentas.application.ports.outbound_ports import (
    AccountSourcePort,
    BatchStateRepositoryPort,
    ExecutionLockPort,
    ExternalAccountClientPort,
    FatalBatchError,
    ReportWriterPort,
)
from rpa_cuentas.application.use_cases.audit_summary import BatchSummary, build_batch_summary
from rpa_cuentas.domain import (
    AccountId,
    AccountStateRecord,
    DeduplicatedAccountItem,
    ExternalFetchResult,
    UtcTimestamp,
    classify_and_deduplicate_rows,
    should_query_account,
)


def _utc_now_iso() -> str:
    """Genera una marca de tiempo ISO 8601 en UTC."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class ProcessBatchUseCase(ProcessBatchUseCasePort):
    """Caso de uso primario que orquesta la ejecución batch bajo bloqueo exclusivo."""

    def __init__(
        self, source: AccountSourcePort, external_client: ExternalAccountClientPort,
        repository: BatchStateRepositoryPort, report_writer: ReportWriterPort,
        execution_lock: ExecutionLockPort | None = None, clock: Callable[[], str] = _utc_now_iso,
        logger: logging.Logger | None = None, state_ttl_seconds: float | None = None,
    ) -> None:  # fmt: skip
        self._source, self._external_client = source, external_client
        self._repository, self._report_writer = repository, report_writer
        self._execution_lock: ExecutionLockPort = execution_lock or contextlib.nullcontext()
        self._clock, self._log = clock, EventLogger(logger, "rpa_cuentas.application")
        self._state_ttl_seconds = state_ttl_seconds

    def execute(self, command: ProcessBatchCommand) -> BatchSummary:
        """Ejecuta el comando del lote garantizando exclusión mutua antes de iniciar corrida."""
        with self._execution_lock:
            return self._run_locked(command)

    def _safe_fetch(self, account_id: AccountId, run_id: str) -> ExternalFetchResult:
        """Consulta una cuenta aislando cualquier fallo inesperado del cliente externo."""
        try:
            return self._external_client.fetch_account(account_id)
        except FatalBatchError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            cause = f"ERROR_INESPERADO_CLIENTE: {type(exc).__name__}"
            self._log(
                "unexpected_client_error", level="error", run_id=run_id,
                account_id=account_id.value, cause=cause, detail=exc,
            )  # fmt: skip
            return ExternalFetchResult.technical_failure(1, cause)

    def _resolve_accounts(
        self, run_id: str, items: tuple[DeduplicatedAccountItem, ...]
    ) -> tuple[int, int]:
        """Procesa cada cuenta única reutilizando estado terminal o consultando pendiente."""
        queried_in_run = reused_from_state = 0
        for item in items:
            existing = self._repository.get_account_state(item.account_id)
            now = UtcTimestamp(self._clock())
            if not should_query_account(existing, now=now, ttl_seconds=self._state_ttl_seconds):
                reused_from_state += 1
                self._log(
                    "state_reused", run_id=run_id, account_id=item.account_id.value,
                    status=existing.status.value if existing else "UNKNOWN",
                    occurrences=item.occurrences,
                )  # fmt: skip
                continue

            queried_in_run += 1
            res = self._safe_fetch(item.account_id, run_id)
            timestamp = UtcTimestamp(self._clock())
            record = AccountStateRecord.from_fetch_result(item.account_id, res, timestamp)
            self._repository.save_account_state(record)
            self._log(
                "account_processed", run_id=run_id, account_id=item.account_id.value,
                status=record.status.value, attempts=record.attempts, cause=record.cause or "NONE",
            )  # fmt: skip
        return queried_in_run, reused_from_state

    def _run_locked(self, command: ProcessBatchCommand) -> BatchSummary:
        """Ejecuta el flujo del lote una vez adquirido el bloqueo exclusivo."""
        run_id, input_path = command.run_id, command.input_path
        started_at = UtcTimestamp(self._clock())
        self._log(
            "batch_started",
            run_id=run_id,
            input_path=input_path,
            started_at=started_at.value,
        )
        self._repository.start_run(
            run_id=run_id,
            started_at=started_at,
            input_path=input_path,
        )

        classification = classify_and_deduplicate_rows(self._source.read_rows())
        self._repository.save_run_plan(run_id, classification.items, classification.incidents)
        self._log(
            "input_classified", run_id=run_id, lines_read=classification.lines_read,
            valid_lines=classification.valid_lines, unique_accounts=len(classification.items),
            duplicates_collapsed=classification.duplicates_collapsed,
            incidents=len(classification.incidents),
        )  # fmt: skip

        queried, reused = self._resolve_accounts(run_id, classification.items)
        report_rows = self._repository.get_report_rows(run_id)
        summary = build_batch_summary(run_id, classification, report_rows, queried, reused)
        self._report_writer.publish_report(
            report_rows, self._repository.get_run_incidents(run_id), summary
        )
        finished_at = UtcTimestamp(self._clock())
        self._repository.finish_run(run_id, finished_at, summary.exit_code)
        self._log(
            "batch_finished", run_id=run_id, exit_code=summary.exit_code,
            resolved=summary.resolved_count, not_found=summary.not_found_count,
            technical_failures=summary.technical_failure_count,
        )  # fmt: skip
        return summary
