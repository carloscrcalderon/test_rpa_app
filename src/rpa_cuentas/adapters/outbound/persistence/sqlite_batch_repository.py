"""Adaptador secundario de persistencia en SQLite para el estado de cuentas y ejecuciones."""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from rpa_cuentas.application.ports.outbound_ports import (
    AccountId,
    AccountStateRecord,
    AccountStatus,
    BatchStateRepositoryPort,
    DeduplicatedAccountItem,
    IncidentReason,
    InputIncident,
    PersistenceError,
    ReportRow,
    ResolvedAccountData,
    UtcTimestamp,
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY, status TEXT NOT NULL, attempts INTEGER NOT NULL,
    cause TEXT NULL, user_id INTEGER NULL, title TEXT NULL, body TEXT NULL,
    last_attempt_at TEXT NOT NULL, resolved_at TEXT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT NULL,
    input_path TEXT NOT NULL, exit_code INTEGER NULL
);
CREATE TABLE IF NOT EXISTS run_accounts (
    run_id TEXT NOT NULL, account_id INTEGER NOT NULL, occurrences INTEGER NOT NULL,
    position INTEGER NOT NULL, PRIMARY KEY (run_id, account_id)
);
CREATE TABLE IF NOT EXISTS run_incidents (
    run_id TEXT NOT NULL, line_number INTEGER NOT NULL, raw_value TEXT NOT NULL,
    reason TEXT NOT NULL, PRIMARY KEY (run_id, line_number)
);
"""


@dataclass(frozen=True)
class AccountPersistenceModel:
    """Modelo de persistencia que aísla los tipos primitivos de la tabla SQLite `accounts`."""

    id: int
    status: str
    attempts: int
    cause: str | None
    user_id: int | None
    title: str | None
    body: str | None
    last_attempt_at: str
    resolved_at: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> AccountPersistenceModel:
        """Construye el modelo de persistencia desde una fila SQLite."""
        return cls(
            id=row["id"],
            status=row["status"],
            attempts=row["attempts"],
            cause=row["cause"],
            user_id=row["user_id"],
            title=row["title"],
            body=row["body"],
            last_attempt_at=row["last_attempt_at"],
            resolved_at=row["resolved_at"],
        )

    @classmethod
    def from_domain(cls, record: AccountStateRecord) -> AccountPersistenceModel:
        """Traduce la entidad y sus Value Objects a primitivos para SQLite."""
        data = record.resolved_data
        return cls(
            id=record.account_id.value,
            status=record.status.value,
            attempts=record.attempts,
            cause=record.cause,
            user_id=data.user_id if data is not None else None,
            title=data.title if data is not None else None,
            body=data.body if data is not None else None,
            last_attempt_at=record.last_attempt_at.value,
            resolved_at=record.resolved_at.value if record.resolved_at is not None else None,
        )

    def to_domain(self) -> AccountStateRecord:
        """Reconstruye Value Objects y entidad de dominio desde los primitivos persistidos."""
        resolved_data = (
            ResolvedAccountData(user_id=self.user_id, title=self.title, body=self.body)
            if self.user_id is not None and self.title is not None and self.body is not None
            else None
        )
        return AccountStateRecord(
            account_id=AccountId(self.id),
            status=AccountStatus(self.status),
            attempts=self.attempts,
            cause=self.cause,
            resolved_data=resolved_data,
            last_attempt_at=UtcTimestamp(self.last_attempt_at),
            resolved_at=UtcTimestamp(self.resolved_at) if self.resolved_at is not None else None,
        )

    def to_sql_tuple(self) -> tuple[object, ...]:
        """Devuelve la tupla ordenada de parámetros SQL para inserción/actualización."""
        return (
            self.id,
            self.status,
            self.attempts,
            self.cause,
            self.user_id,
            self.title,
            self.body,
            self.last_attempt_at,
            self.resolved_at,
        )


@dataclass(frozen=True)
class RunAccountPersistenceModel:
    """Modelo de persistencia que aísla los tipos primitivos de la tabla SQLite `run_accounts`."""

    run_id: str
    account_id: int
    occurrences: int
    position: int

    @classmethod
    def from_row(cls, row: sqlite3.Row, run_id: str = "") -> RunAccountPersistenceModel:
        """Construye el modelo de persistencia desde una fila SQLite."""
        return cls(
            run_id=run_id,
            account_id=row["account_id"],
            occurrences=row["occurrences"],
            position=row["position"],
        )

    @classmethod
    def from_domain(cls, run_id: str, item: DeduplicatedAccountItem) -> RunAccountPersistenceModel:
        """Traduce el Value Object DeduplicatedAccountItem a primitivos para SQLite."""
        return cls(
            run_id=run_id,
            account_id=item.account_id.value,
            occurrences=item.occurrences,
            position=item.first_seen_order,
        )

    def to_domain(self) -> DeduplicatedAccountItem:
        """Reconstruye el Value Object DeduplicatedAccountItem desde los primitivos persistidos."""
        return DeduplicatedAccountItem(
            account_id=AccountId(self.account_id),
            occurrences=self.occurrences,
            first_seen_order=self.position,
        )

    def to_sql_tuple(self) -> tuple[object, ...]:
        """Devuelve la tupla ordenada de parámetros SQL para inserción."""
        return (self.run_id, self.account_id, self.occurrences, self.position)


@dataclass(frozen=True)
class IncidentPersistenceModel:
    """Modelo de persistencia que aísla los tipos primitivos de la tabla SQLite `run_incidents`."""

    run_id: str
    line_number: int
    raw_value: str
    reason: str

    @classmethod
    def from_row(cls, row: sqlite3.Row, run_id: str = "") -> IncidentPersistenceModel:
        """Construye el modelo de persistencia desde una fila SQLite."""
        return cls(
            run_id=run_id,
            line_number=row["line_number"],
            raw_value=row["raw_value"],
            reason=row["reason"],
        )

    @classmethod
    def from_domain(cls, run_id: str, incident: InputIncident) -> IncidentPersistenceModel:
        """Traduce el Value Object InputIncident a primitivos para SQLite."""
        return cls(
            run_id=run_id,
            line_number=incident.line_number,
            raw_value=incident.raw_value,
            reason=incident.reason.value,
        )

    def to_domain(self) -> InputIncident:
        """Reconstruye el Value Object InputIncident desde los primitivos persistidos."""
        return InputIncident(
            line_number=self.line_number,
            raw_value=self.raw_value,
            reason=IncidentReason(self.reason),
        )

    def to_sql_tuple(self) -> tuple[object, ...]:
        """Devuelve la tupla ordenada de parámetros SQL para inserción."""
        return (self.run_id, self.line_number, self.raw_value, self.reason)


@dataclass(frozen=True)
class ReportRowPersistenceModel:
    """Modelo de persistencia para traducir la proyección SQL de reporte a ReportRow."""

    id_cuenta: int
    estado: str
    causa: str | None
    apariciones: int
    user_id: int | None
    titulo: str | None
    cuerpo: str | None
    intentos: int
    fecha_consulta: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ReportRowPersistenceModel:
        """Construye el modelo de persistencia desde una fila proyectada en SQLite."""
        return cls(
            id_cuenta=row["id_cuenta"],
            estado=row["estado"],
            causa=row["causa"],
            apariciones=row["apariciones"],
            user_id=row["user_id"],
            titulo=row["titulo"],
            cuerpo=row["cuerpo"],
            intentos=row["intentos"],
            fecha_consulta=row["fecha_consulta"],
        )

    def to_domain(self) -> ReportRow:
        """Reconstruye ReportRow garantizando Value Objects validados (AccountId y UtcTimestamp)."""
        return ReportRow(
            id_cuenta=AccountId(self.id_cuenta),
            estado=AccountStatus(self.estado),
            causa=self.causa,
            apariciones=self.apariciones,
            user_id=self.user_id,
            titulo=self.titulo,
            cuerpo=self.cuerpo,
            intentos=self.intentos,
            fecha_consulta=UtcTimestamp(self.fecha_consulta).value,
        )


DEFAULT_MAX_RETAINED_RUNS = 100


class SqliteBatchRepository(BatchStateRepositoryPort):
    """Repositorio SQLite que persiste un commit por cuenta y proyecta el reporte con JOIN."""

    def __init__(
        self,
        db_path: Path | str,
        max_retained_runs: int = DEFAULT_MAX_RETAINED_RUNS,
    ) -> None:
        if max_retained_runs < 1:
            raise ValueError("max_retained_runs debe ser un entero mayor o igual a 1.")
        self._db_path = Path(db_path)
        self._max_retained_runs = max_retained_runs
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(SCHEMA_SQL)
            self._conn.commit()
        except (sqlite3.Error, OSError) as exc:
            raise PersistenceError(
                f"No fue posible inicializar la base de datos SQLite en '{self._db_path}': {exc}"
            ) from exc

    @contextlib.contextmanager
    def _db_op(self, error_prefix: str, *, commit: bool = False) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            if commit:
                self._conn.commit()
        except sqlite3.Error as exc:
            raise PersistenceError(f"{error_prefix}: {exc}") from exc

    def close(self) -> None:
        """Cierra la conexión SQLite."""
        with contextlib.suppress(sqlite3.Error):
            self._conn.close()

    def start_run(self, run_id: str, started_at: UtcTimestamp, input_path: str) -> None:
        """Registra el inicio de una ejecución batch."""
        with self._db_op(f"Error al registrar inicio de corrida '{run_id}'", commit=True) as conn:
            conn.execute(
                "INSERT INTO runs (run_id, started_at, finished_at, input_path, exit_code) "
                "VALUES (?, ?, NULL, ?, NULL)",
                (run_id, started_at.value, input_path),
            )

    def save_run_plan(
        self,
        run_id: str,
        items: Sequence[DeduplicatedAccountItem],
        incidents: Sequence[InputIncident],
    ) -> None:
        """Persiste el plan de cuentas únicas de la corrida y las incidencias detectadas."""
        with self._db_op(f"Error al persistir el plan del lote para '{run_id}'", commit=True) as db:
            db.executemany(
                "INSERT INTO run_accounts (run_id, account_id, occurrences, position) "
                "VALUES (?, ?, ?, ?)",
                [RunAccountPersistenceModel.from_domain(run_id, it).to_sql_tuple() for it in items],
            )
            db.executemany(
                "INSERT INTO run_incidents (run_id, line_number, raw_value, reason) "
                "VALUES (?, ?, ?, ?)",
                [
                    IncidentPersistenceModel.from_domain(run_id, inc).to_sql_tuple()
                    for inc in incidents
                ],
            )

    def get_account_state(self, account_id: AccountId) -> AccountStateRecord | None:
        """Obtiene el último estado persistido de una cuenta o None si nunca se ha procesado."""
        with self._db_op(f"Error al consultar el estado de la cuenta {account_id.value}") as conn:
            row = conn.execute(
                "SELECT id, status, attempts, cause, user_id, title, body, "
                "last_attempt_at, resolved_at FROM accounts WHERE id = ?",
                (account_id.value,),
            ).fetchone()
        if row is None:
            return None
        return AccountPersistenceModel.from_row(row).to_domain()

    def save_account_state(self, record: AccountStateRecord) -> None:
        """Guarda o actualiza el estado de una cuenta con commit inmediato."""
        model = AccountPersistenceModel.from_domain(record)
        err = f"Error al persistir el estado de la cuenta {model.id}"
        with self._db_op(err, commit=True) as conn:
            conn.execute(
                "INSERT INTO accounts (id, status, attempts, cause, user_id, title, body, "
                "last_attempt_at, resolved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status = excluded.status, "
                "attempts = excluded.attempts, cause = excluded.cause, user_id = excluded.user_id, "
                "title = excluded.title, body = excluded.body, "
                "last_attempt_at = excluded.last_attempt_at, resolved_at = excluded.resolved_at",
                model.to_sql_tuple(),
            )

    def get_report_rows(self, run_id: str) -> list[ReportRow]:
        """Construye las filas semánticas de Resultados uniendo run_accounts con accounts."""
        with self._db_op(f"Error al consultar filas de reporte para '{run_id}'") as conn:
            rows = conn.execute(
                "SELECT ra.account_id AS id_cuenta, a.status AS estado, a.cause AS causa, "
                "ra.occurrences AS apariciones, a.user_id AS user_id, a.title AS titulo, "
                "a.body AS cuerpo, a.attempts AS intentos, "
                "COALESCE(a.resolved_at, a.last_attempt_at) AS fecha_consulta "
                "FROM run_accounts AS ra JOIN accounts AS a ON a.id = ra.account_id "
                "WHERE ra.run_id = ? ORDER BY ra.position ASC",
                (run_id,),
            ).fetchall()
        return [ReportRowPersistenceModel.from_row(r).to_domain() for r in rows]

    def get_run_incidents(self, run_id: str) -> list[InputIncident]:
        """Recupera las incidencias de entrada de la ejecución ordenadas por número de línea."""
        with self._db_op(f"Error al consultar incidencias para '{run_id}'") as conn:
            rows = conn.execute(
                "SELECT line_number, raw_value, reason FROM run_incidents "
                "WHERE run_id = ? ORDER BY line_number ASC",
                (run_id,),
            ).fetchall()
        return [IncidentPersistenceModel.from_row(r, run_id).to_domain() for r in rows]

    def finish_run(self, run_id: str, finished_at: UtcTimestamp, exit_code: int) -> None:
        """Registra la finalización de la ejecución batch y poda el historial antiguo excedente."""
        with self._db_op(f"Error al registrar fin de corrida '{run_id}'", commit=True) as conn:
            conn.execute(
                "UPDATE runs SET finished_at = ?, exit_code = ? WHERE run_id = ?",
                (finished_at.value, exit_code, run_id),
            )
            conn.execute(
                "DELETE FROM runs WHERE rowid NOT IN ("
                "SELECT rowid FROM runs ORDER BY rowid DESC LIMIT ?"
                ")",
                (self._max_retained_runs,),
            )
            conn.execute("DELETE FROM run_accounts WHERE run_id NOT IN (SELECT run_id FROM runs)")
            conn.execute("DELETE FROM run_incidents WHERE run_id NOT IN (SELECT run_id FROM runs)")
