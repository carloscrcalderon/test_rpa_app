"""Constructores compartidos de objetos de dominio, aplicación y entorno para pruebas."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rpa_cuentas.application.ports.outbound_ports import ReportRow
from rpa_cuentas.application.use_cases.audit_summary import BatchSummary
from rpa_cuentas.domain import (
    AccountId,
    AccountStateRecord,
    AccountStatus,
    RawAccountEntry,
    ResolvedAccountData,
    UtcTimestamp,
)

REFERENCE_CSV_VALUES: tuple[str, ...] = (
    "7", "12", " 23", "45", "7", "99", "101", "abc", "3", "58",
    "", "88", "12", "150", "64", "31", "7", '"42"', "76", "0", "19",
)  # fmt: skip


def _normalize_test_cell(raw_value: str) -> str:
    """Normaliza una celda de prueba recortando espacios y comillas envolventes."""
    cleaned = raw_value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        cleaned = cleaned[1:-1].strip()
    return cleaned


def make_reference_entries(values: Sequence[str] = REFERENCE_CSV_VALUES) -> list[RawAccountEntry]:
    """Construye entradas RawAccountEntry pre-normalizadas numerando desde la línea física 2."""
    return [
        RawAccountEntry(idx, raw_value, _normalize_test_cell(raw_value))
        for idx, raw_value in enumerate(values, start=2)
    ]


def make_test_env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    """Construye un entorno aislado en tmp_path para pruebas de CLI y aceptación."""
    base_env = {
        "EXTERNAL_API_BASE_URL": "https://jsonplaceholder.typicode.com",
        "OUTPUT_EXCEL_PATH": str(tmp_path / "output" / "consolidado_cuentas.xlsx"),
        "SQLITE_DB_PATH": str(tmp_path / "state" / "rpa_cuentas.db"),
        "LOG_FILE_PATH": str(tmp_path / "logs" / "rpa_cuentas.log"),
        "LOCK_FILE_PATH": str(tmp_path / "state" / "rpa_cuentas.lock"),
    }
    return base_env | overrides


def make_state(
    account_id: int | AccountId = 1,
    status: AccountStatus = AccountStatus.RESUELTA,
    **overrides: Any,
) -> AccountStateRecord:
    """Construye un AccountStateRecord con Value Objects por defecto razonables."""
    opts = dict(overrides)
    status = opts.pop("status", status)
    raw_last = opts.pop("last_attempt_at", "2026-03-19T12:00:00Z")
    last_ts = raw_last if isinstance(raw_last, UtcTimestamp) else UtcTimestamp(raw_last)
    is_terminal = status in {AccountStatus.RESUELTA, AccountStatus.NO_EXISTE}
    raw_resolved = opts.pop("resolved_at", last_ts if is_terminal else None)
    resolved_ts = (
        raw_resolved
        if raw_resolved is None or isinstance(raw_resolved, UtcTimestamp)
        else UtcTimestamp(raw_resolved)
    )
    user_id = opts.pop("user_id", None)
    title = opts.pop("title", None)
    body = opts.pop("body", None)
    if "resolved_data" in opts:
        resolved_data = opts.pop("resolved_data")
    elif user_id is not None or title is not None or body is not None:
        resolved_data = ResolvedAccountData(
            user_id=user_id or 1, title=title or "Título", body=body or "Cuerpo"
        )
    elif status == AccountStatus.RESUELTA:
        resolved_data = ResolvedAccountData(user_id=1, title="Título", body="Cuerpo")
    else:
        resolved_data = None

    defaults: dict[str, Any] = {
        "account_id": account_id if isinstance(account_id, AccountId) else AccountId(account_id),
        "status": status,
        "attempts": 1,
        "cause": None if status == AccountStatus.RESUELTA else "CAUSA_DEFECTO",
        "resolved_data": resolved_data,
        "last_attempt_at": last_ts,
        "resolved_at": resolved_ts,
    }
    return AccountStateRecord(**(defaults | opts))


def make_row(
    id_cuenta: int | AccountId = 7,
    estado: AccountStatus = AccountStatus.RESUELTA,
    causa: str | None = None,
    **overrides: Any,
) -> ReportRow:
    """Construye un ReportRow con valores por defecto razonables."""
    acc_id = id_cuenta if isinstance(id_cuenta, AccountId) else AccountId(id_cuenta)
    defaults: dict[str, Any] = {
        "id_cuenta": acc_id, "estado": estado, "causa": causa, "apariciones": 1,
        "user_id": None, "titulo": None, "cuerpo": None, "intentos": 1,
        "fecha_consulta": "2026-10-21T03:00:00Z",
    }  # fmt: skip
    return ReportRow(**(defaults | overrides))


def make_summary(**overrides: Any) -> BatchSummary:
    """Construye un BatchSummary con valores por defecto razonables y overrides opcionales."""
    defaults: dict[str, Any] = {
        "run_id": "run-001", "lines_read": 0, "empty_lines": 0, "invalid_lines_by_reason": {},
        "total_incidents": 0, "valid_lines": 0, "duplicates_collapsed": 0, "unique_accounts": 0,
        "queried_in_run": 0, "reused_from_state": 0, "resolved_count": 0, "not_found_count": 0,
        "technical_failure_count": 0, "failures_by_cause": {}, "exit_code": 0,
    }  # fmt: skip
    return BatchSummary(**(defaults | overrides))
