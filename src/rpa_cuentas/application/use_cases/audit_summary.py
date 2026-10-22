"""Cálculo del resumen verificable de ejecución y determinación de códigos de salida."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from rpa_cuentas.application.dtos import (
    EXIT_FATAL_ERROR,
    EXIT_PARTIAL_FAILURE,
    EXIT_SUCCESS,
    BatchSummary,
    ReportRow,
)
from rpa_cuentas.domain import AccountStatus, BatchClassificationResult, IncidentReason


def determine_exit_code(technical_failure_count: int) -> int:
    """Determina el código de salida del lote (0 sin FALLO_TECNICO, 1 con >=1 FALLO_TECNICO)."""
    return EXIT_PARTIAL_FAILURE if technical_failure_count > 0 else EXIT_SUCCESS


def build_batch_summary(
    run_id: str,
    classification: BatchClassificationResult,
    report_rows: Sequence[ReportRow],
    queried_in_run: int,
    reused_from_state: int,
) -> BatchSummary:
    """Construye el BatchSummary verificable a partir de la clasificación y el estado persistido."""
    reason_counts = Counter(inc.reason.value for inc in classification.incidents)
    invalid_by_reason = {r.value: reason_counts.get(r.value, 0) for r in IncidentReason}
    status_counts = Counter(row.estado for row in report_rows)
    failure_causes = Counter(
        (row.causa or "").strip() or "CAUSA_NO_ESPECIFICADA"
        for row in report_rows
        if row.estado == AccountStatus.FALLO_TECNICO
    )
    tech_failures = status_counts[AccountStatus.FALLO_TECNICO]

    return BatchSummary(
        run_id=run_id,
        lines_read=classification.lines_read,
        empty_lines=invalid_by_reason[IncidentReason.LINEA_VACIA.value],
        invalid_lines_by_reason=invalid_by_reason,
        total_incidents=len(classification.incidents),
        valid_lines=classification.valid_lines,
        duplicates_collapsed=classification.duplicates_collapsed,
        unique_accounts=len(classification.items),
        queried_in_run=queried_in_run,
        reused_from_state=reused_from_state,
        resolved_count=status_counts[AccountStatus.RESUELTA],
        not_found_count=status_counts[AccountStatus.NO_EXISTE],
        technical_failure_count=tech_failures,
        failures_by_cause=dict(sorted(failure_causes.items())),
        exit_code=determine_exit_code(tech_failures),
    )


__all__ = [
    "EXIT_FATAL_ERROR",
    "EXIT_PARTIAL_FAILURE",
    "EXIT_SUCCESS",
    "BatchSummary",
    "build_batch_summary",
    "determine_exit_code",
]
