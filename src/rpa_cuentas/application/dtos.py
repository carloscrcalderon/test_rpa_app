"""Modelos de transferencia (Commands, Read Models y Resúmenes) de la capa de aplicación."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from rpa_cuentas.domain import (
    AccountId,
    AccountStatus,
    IncidentReason,
    UtcTimestamp,
    require_account_id,
)

EXIT_SUCCESS: int = 0
EXIT_PARTIAL_FAILURE: int = 1
EXIT_FATAL_ERROR: int = 2


@dataclass(frozen=True)
class ProcessBatchCommand:
    """Comando de entrada para ejecutar una corrida del procesador batch."""

    run_id: str
    input_path: str

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("ProcessBatchCommand requiere un run_id no vacío.")
        if not isinstance(self.input_path, str) or not self.input_path.strip():
            raise ValueError("ProcessBatchCommand requiere un input_path no vacío.")


@dataclass(frozen=True)
class ReportRow:
    """Fila proyectada semánticamente (Read Model) para el reporte consolidado."""

    id_cuenta: AccountId
    estado: AccountStatus
    causa: str | None
    apariciones: int
    user_id: int | None
    titulo: str | None
    cuerpo: str | None
    intentos: int
    fecha_consulta: str

    def __post_init__(self) -> None:
        require_account_id(self.id_cuenta, "ReportRow")
        if not isinstance(self.estado, AccountStatus):
            raise ValueError(f"estado inválido en ReportRow: {self.estado!r}")
        if (
            isinstance(self.apariciones, bool)
            or not isinstance(self.apariciones, int)
            or self.apariciones < 1
        ):
            raise ValueError(f"apariciones debe ser >= 1, se recibió: {self.apariciones!r}")
        if (
            isinstance(self.intentos, bool)
            or not isinstance(self.intentos, int)
            or self.intentos < 1
        ):
            raise ValueError(f"intentos debe ser >= 1, se recibió: {self.intentos!r}")
        UtcTimestamp(self.fecha_consulta)


@dataclass(frozen=True)
class BatchSummary:
    """Resumen consolidado y verificable de una ejecución del proceso batch."""

    run_id: str
    lines_read: int
    empty_lines: int
    invalid_lines_by_reason: Mapping[str, int]
    total_incidents: int
    valid_lines: int
    duplicates_collapsed: int
    unique_accounts: int
    queried_in_run: int
    reused_from_state: int
    resolved_count: int
    not_found_count: int
    technical_failure_count: int
    failures_by_cause: Mapping[str, int]
    exit_code: int

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("BatchSummary requiere un run_id no vacío.")
        for field_name in (
            "lines_read",
            "empty_lines",
            "total_incidents",
            "valid_lines",
            "duplicates_collapsed",
            "unique_accounts",
            "queried_in_run",
            "reused_from_state",
            "resolved_count",
            "not_found_count",
            "technical_failure_count",
        ):
            val = getattr(self, field_name)
            if isinstance(val, bool) or not isinstance(val, int) or val < 0:
                raise ValueError(f"{field_name} debe ser un entero >= 0, se recibió: {val!r}")

    def format_failures_by_cause(self) -> str:
        """Devuelve el desglose determinista de fallos técnicos por causa."""
        return "; ".join(f"{k}: {v}" for k, v in sorted(self.failures_by_cause.items()))

    def to_metric_rows(self, empty_causes_label: str = "") -> tuple[tuple[str, int | str], ...]:
        """Proyecta los pares ordenados (métrica, valor) del resumen verificable."""
        inv = self.invalid_lines_by_reason
        return (
            ("lineas_leidas", self.lines_read),
            ("lineas_vacias", self.empty_lines),
            ("invalidas_no_entero", inv.get(IncidentReason.NO_ENTERO.value, 0)),
            ("invalidas_no_positivo", inv.get(IncidentReason.NO_POSITIVO.value, 0)),
            ("total_incidencias", self.total_incidents),
            ("filas_validas", self.valid_lines),
            ("duplicados_colapsados", self.duplicates_collapsed),
            ("cuentas_unicas", self.unique_accounts),
            ("consultadas_en_ejecucion", self.queried_in_run),
            ("reutilizadas_de_estado", self.reused_from_state),
            ("resueltas", self.resolved_count),
            ("no_existe", self.not_found_count),
            ("fallos_tecnicos", self.technical_failure_count),
            ("detalle_fallos_por_causa", self.format_failures_by_cause() or empty_causes_label),
            ("codigo_salida", self.exit_code),
        )


__all__ = [
    "EXIT_FATAL_ERROR",
    "EXIT_PARTIAL_FAILURE",
    "EXIT_SUCCESS",
    "BatchSummary",
    "ProcessBatchCommand",
    "ReportRow",
]
