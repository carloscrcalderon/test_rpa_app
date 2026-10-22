"""Pruebas de contrato para BatchStateRepositoryPort (InMemory vs SQLite) y durabilidad SQLite."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from dirty_equals import HasAttributes

from rpa_cuentas.adapters.outbound.persistence.sqlite_batch_repository import (
    AccountPersistenceModel,
    SqliteBatchRepository,
)
from rpa_cuentas.application.ports.outbound_ports import BatchStateRepositoryPort, PersistenceError
from rpa_cuentas.domain import (
    AccountId,
    AccountStatus,
    DeduplicatedAccountItem,
    IncidentReason,
    InputIncident,
    UtcTimestamp,
)
from tests.application.test_consolidate_accounts import InMemoryBatchStateRepository
from tests.conftest import make_row, make_state


@pytest.fixture(params=["in_memory", "sqlite"])
def repository_impl(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[BatchStateRepositoryPort]:
    """Provee ambas implementaciones de BatchStateRepositoryPort para verificar paridad."""
    if request.param == "in_memory":
        yield InMemoryBatchStateRepository()
    else:
        repo = SqliteBatchRepository(tmp_path / "contract_state.db")
        try:
            yield repo
        finally:
            repo.close()


def test_repository_contract_plan_persistence_upserts_and_report_projection(
    repository_impl: BatchStateRepositoryPort,
) -> None:
    """Verifica que InMemory y SQLite cumplan idénticamente los invariantes del puerto."""
    run_id = "run-contract-01"
    repository_impl.start_run(run_id, UtcTimestamp("2026-10-21T03:00:00Z"), "data/cuentas.csv")
    repository_impl.save_run_plan(
        run_id=run_id,
        items=[
            DeduplicatedAccountItem(AccountId(45), occurrences=1, first_seen_order=0),
            DeduplicatedAccountItem(AccountId(7), occurrences=3, first_seen_order=1),
            DeduplicatedAccountItem(AccountId(101), occurrences=1, first_seen_order=2),
        ],
        incidents=[
            InputIncident(12, "", IncidentReason.LINEA_VACIA),
            InputIncident(9, "abc", IncidentReason.NO_ENTERO),
        ],
    )

    assert repository_impl.get_account_state(AccountId(7)) is None
    assert repository_impl.get_account_state(AccountId(45)) is None

    repository_impl.save_account_state(
        make_state(
            7, user_id=1, title="Cuenta 7", body="Cuerpo 7", last_attempt_at="2026-10-21T03:00:01Z"
        )  # fmt: skip
    )
    repository_impl.save_account_state(
        make_state(
            45,
            AccountStatus.FALLO_TECNICO,
            attempts=4,
            cause="TIMEOUT_AGOTADO",
            last_attempt_at="2026-10-21T03:00:05Z",
        )  # fmt: skip
    )
    repository_impl.save_account_state(
        make_state(
            101,
            AccountStatus.NO_EXISTE,
            cause="RECURSO_NO_ENCONTRADO",
            last_attempt_at="2026-10-21T03:00:06Z",
        )  # fmt: skip
    )

    assert repository_impl.get_account_state(AccountId(45)) == HasAttributes(
        account_id=AccountId(45), status=AccountStatus.FALLO_TECNICO
    )

    repository_impl.save_account_state(
        make_state(
            45,
            attempts=2,
            user_id=5,
            title="Cuenta 45 Recuperada",
            body="Cuerpo 45",
            last_attempt_at="2026-10-21T03:10:00Z",
        )  # fmt: skip
    )
    repository_impl.finish_run(
        run_id=run_id, finished_at=UtcTimestamp("2026-10-21T03:10:01Z"), exit_code=0
    )

    assert repository_impl.get_report_rows(run_id) == [
        make_row(
            45,
            user_id=5,
            titulo="Cuenta 45 Recuperada",
            cuerpo="Cuerpo 45",
            intentos=2,
            fecha_consulta="2026-10-21T03:10:00Z",
        ),  # fmt: skip
        make_row(
            7,
            apariciones=3,
            user_id=1,
            titulo="Cuenta 7",
            cuerpo="Cuerpo 7",
            fecha_consulta="2026-10-21T03:00:01Z",
        ),  # fmt: skip
        make_row(
            101,
            AccountStatus.NO_EXISTE,
            "RECURSO_NO_ENCONTRADO",
            fecha_consulta="2026-10-21T03:00:06Z",
        ),  # fmt: skip
    ]

    assert repository_impl.get_run_incidents(run_id) == [
        InputIncident(9, "abc", IncidentReason.NO_ENTERO),
        InputIncident(12, "", IncidentReason.LINEA_VACIA),
    ]


def test_sqlite_repository_upserts_account_state_across_reopened_connections(
    tmp_path: Path,
) -> None:
    """Verifica que el commit por cuenta persista en disco y permita actualizar FALLO_TECNICO."""
    db_path = tmp_path / "rpa_cuentas.db"
    repo_1 = SqliteBatchRepository(db_path)
    repo_1.save_account_state(
        make_state(
            45,
            AccountStatus.FALLO_TECNICO,
            attempts=4,
            cause="TIMEOUT_AGOTADO",
            last_attempt_at="2026-10-21T03:00:05Z",
        )  # fmt: skip
    )
    repo_1.close()

    repo_2 = SqliteBatchRepository(db_path)
    assert repo_2.get_account_state(AccountId(45)) == HasAttributes(
        account_id=AccountId(45), status=AccountStatus.FALLO_TECNICO
    )

    repo_2.save_account_state(
        make_state(
            45,
            user_id=5,
            title="Recuperada",
            body="Detalle 45",
            last_attempt_at="2026-10-21T03:10:00Z",
        )  # fmt: skip
    )
    updated = repo_2.get_account_state(AccountId(45))
    repo_2.close()

    assert updated == HasAttributes(
        account_id=AccountId(45), status=AccountStatus.RESUELTA,
        cause=None, user_id=5, resolved_at=UtcTimestamp("2026-10-21T03:10:00Z"),
    )  # fmt: skip


def test_account_persistence_model_maps_to_and_from_domain_cleanly() -> None:
    """Verifica que AccountPersistenceModel traduzca bidireccionalmente sin pérdida."""
    domain_record = make_state(
        45,
        attempts=2,
        user_id=5,
        title="Cuenta 45",
        body="Detalle 45",
        last_attempt_at="2026-10-21T03:10:00Z",
    )
    persistence_model = AccountPersistenceModel.from_domain(domain_record)
    assert persistence_model == HasAttributes(
        id=45, status="RESUELTA", attempts=2, user_id=5,
        title="Cuenta 45", body="Detalle 45",
        last_attempt_at="2026-10-21T03:10:00Z",
        resolved_at="2026-10-21T03:10:00Z",
    )  # fmt: skip
    assert persistence_model.to_domain() == domain_record


def test_sqlite_repository_raises_persistence_error_on_invalid_db_path(tmp_path: Path) -> None:
    """Verifica que una ruta de base de datos inválida (directorio) lance PersistenceError."""
    with pytest.raises(PersistenceError):
        SqliteBatchRepository(tmp_path)


def test_sqlite_repository_prunes_old_runs_preserving_retained_runs_and_account_states(
    tmp_path: Path,
) -> None:
    """Verifica que finish_run pode corridas antiguas excedentes sin borrar la tabla accounts."""
    db_path = tmp_path / "retention.db"
    repo = SqliteBatchRepository(db_path, max_retained_runs=2)
    try:
        repo.save_account_state(make_state(7, user_id=1, title="Cuenta 7", body="Cuerpo 7"))
        for idx in (1, 2, 3):
            run_id = f"run-0{idx}"
            repo.start_run(run_id, UtcTimestamp(f"2026-10-21T03:0{idx}:00Z"), "data/cuentas.csv")
            repo.save_run_plan(
                run_id=run_id,
                items=[DeduplicatedAccountItem(AccountId(7), occurrences=idx, first_seen_order=0)],
                incidents=[InputIncident(idx + 1, "abc", IncidentReason.NO_ENTERO)],
            )
            repo.finish_run(run_id, UtcTimestamp(f"2026-10-21T03:0{idx}:30Z"), exit_code=0)

        assert repo.get_report_rows("run-01") == []
        assert repo.get_run_incidents("run-01") == []
        assert len(repo.get_report_rows("run-02")) == 1
        assert len(repo.get_run_incidents("run-02")) == 1
        assert len(repo.get_report_rows("run-03")) == 1
        assert len(repo.get_run_incidents("run-03")) == 1
        assert repo.get_account_state(AccountId(7)) is not None
    finally:
        repo.close()
