"""Pruebas unitarias para AccountId, validación semántica de entradas y modelos de dominio."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from dirty_equals import HasAttributes

from rpa_cuentas.domain import (
    AccountId,
    AccountStateRecord,
    AccountStatus,
    BatchClassificationResult,
    DeduplicatedAccountItem,
    ErrorCategory,
    ExternalFetchResult,
    IncidentReason,
    InputIncident,
    InvalidAccountIdError,
    InvalidResolvedAccountDataError,
    RawAccountEntry,
    ResolvedAccountData,
    UtcTimestamp,
    parse_raw_entry,
)
from tests.conftest import make_state


@pytest.mark.parametrize(
    ("cleaned_value", "raw_value", "expected_id"),
    [
        ("7", "7", 7), ("12", "12", 12), ("23", " 23", 23),
        ("42", '"42"', 42), ("101", "101", 101), ("150", "150", 150),
    ],
)  # fmt: skip
def test_account_id_from_clean_accepts_valid_base10_positive_integers(
    cleaned_value: str, raw_value: str, expected_id: int
) -> None:
    """Verifica que cadenas limpiadas válidas produzcan AccountId(int > 0)."""
    account_id = AccountId.from_clean(cleaned_value, raw_value=raw_value)
    assert account_id == AccountId(expected_id) and account_id.value == expected_id


@pytest.mark.parametrize(
    ("cleaned_value", "raw_value", "expected_reason"),
    [
        ("", "", IncidentReason.LINEA_VACIA),
        ("", ' "  " ', IncidentReason.LINEA_VACIA),
        ("abc", "abc", IncidentReason.NO_ENTERO),
        ("12.5", "12.5", IncidentReason.NO_ENTERO),
        ("12a", "12a", IncidentReason.NO_ENTERO),
        ("1_000", "1_000", IncidentReason.NO_ENTERO),
        (" 23", " 23", IncidentReason.NO_ENTERO),
        ('"42"', '"42"', IncidentReason.NO_ENTERO),
        ("0", ' "0" ', IncidentReason.NO_POSITIVO),
        ("-42", "-42", IncidentReason.NO_POSITIVO),
    ],
)
def test_account_id_from_clean_rejects_invalid_values_preserving_raw_value(
    cleaned_value: str, raw_value: str, expected_reason: IncidentReason
) -> None:
    """Verifica que vacíos, no enteros y <= 0 lancen InvalidAccountIdError con raw_value intacto."""
    with pytest.raises(InvalidAccountIdError) as exc_info:
        AccountId.from_clean(cleaned_value, raw_value=raw_value)

    assert exc_info.value == HasAttributes(reason=expected_reason, raw_value=raw_value)


def test_account_id_direct_construction_enforces_positive_integer_and_immutability() -> None:
    """Verifica invariantes al instanciar AccountId directamente y su inmutabilidad."""
    valid = AccountId(45)
    assert valid.value == 45

    with pytest.raises(FrozenInstanceError):
        valid.value = 99  # type: ignore[misc]

    for bad_val, expected_reason in (
        (0, IncidentReason.NO_POSITIVO),
        (-7, IncidentReason.NO_POSITIVO),
        (True, IncidentReason.NO_ENTERO),
    ):
        with pytest.raises(InvalidAccountIdError) as exc_info:
            AccountId(bad_val)  # type: ignore[arg-type]
        assert exc_info.value.reason == expected_reason


def test_parse_raw_entry_returns_account_id_or_input_incident_preserving_raw_value() -> None:
    """Verifica que parse_raw_entry evalúe cleaned_value y conserve raw_value en incidencias."""
    assert parse_raw_entry(RawAccountEntry(4, " 23", "23")) == AccountId(23)
    assert parse_raw_entry(RawAccountEntry(19, '"42"', "42")) == AccountId(42)
    assert parse_raw_entry(RawAccountEntry(9, "abc", "abc")) == InputIncident(
        9, "abc", IncidentReason.NO_ENTERO
    )
    assert parse_raw_entry(RawAccountEntry(12, '""', "")) == InputIncident(
        12, '""', IncidentReason.LINEA_VACIA
    )
    assert parse_raw_entry(RawAccountEntry(21, ' "0" ', "0")) == InputIncident(
        21, ' "0" ', IncidentReason.NO_POSITIVO
    )


def test_domain_enums_expose_expected_values() -> None:
    """Verifica que los enums de estados y categorías de error coincidan con la especificación."""
    assert (AccountStatus.RESUELTA, AccountStatus.NO_EXISTE, AccountStatus.FALLO_TECNICO) == (
        "RESUELTA", "NO_EXISTE", "FALLO_TECNICO",
    )  # fmt: skip
    assert (
        ErrorCategory.NEGOCIO, ErrorCategory.TECNICO_TRANSITORIO, ErrorCategory.TECNICO_DEFINITIVO,
    ) == ("NEGOCIO", "TECNICO_TRANSITORIO", "TECNICO_DEFINITIVO")  # fmt: skip


def test_value_objects_and_account_state_record_self_validate_in_post_init() -> None:
    """Verifica que todos los Value Objects y AccountStateRecord validen sus invariantes."""
    ts = UtcTimestamp("2026-10-21T04:00:00Z")
    assert ts.value == "2026-10-21T04:00:00Z"

    data = ResolvedAccountData(1, "T", "B")
    assert (data.user_id, data.title, data.body) == (1, "T", "B")

    with pytest.raises(InvalidAccountIdError):
        DeduplicatedAccountItem(7, 1, 0)  # type: ignore[arg-type]

    with pytest.raises(InvalidAccountIdError):
        AccountStateRecord(7, AccountStatus.RESUELTA, 1, None, data, ts, ts)  # type: ignore[arg-type]

    for invalid_call in (
        lambda: UtcTimestamp(""),
        lambda: UtcTimestamp("no-es-fecha"),
        lambda: UtcTimestamp("2026-10-21T04:00:00"),
        lambda: RawAccountEntry(0, "7", "7"),
        lambda: InputIncident(0, "abc", IncidentReason.NO_ENTERO),
        lambda: DeduplicatedAccountItem(AccountId(7), 0, 0),
        lambda: DeduplicatedAccountItem(AccountId(7), 1, -1),
        lambda: BatchClassificationResult(
            items=(), incidents=(), lines_read=-1, valid_lines=0, duplicates_collapsed=0
        ),
        lambda: BatchClassificationResult(
            items=(), incidents=(), lines_read=5, valid_lines=2, duplicates_collapsed=0
        ),
        lambda: BatchClassificationResult(
            items=(), incidents=(), lines_read=2, valid_lines=2, duplicates_collapsed=0
        ),
        lambda: ExternalFetchResult(status=AccountStatus.RESUELTA, attempts=1, payload=None),
        lambda: ExternalFetchResult(
            status=AccountStatus.NO_EXISTE, attempts=1, payload=data, cause="CAUSA"
        ),
        lambda: ExternalFetchResult(status=AccountStatus.FALLO_TECNICO, attempts=1, cause=""),
        lambda: AccountStateRecord(AccountId(7), AccountStatus.RESUELTA, 1, None, None, ts, ts),
        lambda: AccountStateRecord(AccountId(7), AccountStatus.RESUELTA, 1, "CAUSA", data, ts, ts),
        lambda: AccountStateRecord(
            AccountId(7), AccountStatus.NO_EXISTE, 1, "RECURSO_NO_ENCONTRADO", data, ts, ts
        ),
        lambda: AccountStateRecord(
            AccountId(7), AccountStatus.NO_EXISTE, 1, "RECURSO_NO_ENCONTRADO", None, ts, None
        ),
        lambda: AccountStateRecord(
            AccountId(7), AccountStatus.FALLO_TECNICO, 1, "TIMEOUT_AGOTADO", None, ts, ts
        ),
        lambda: AccountStateRecord(
            AccountId(7), AccountStatus.FALLO_TECNICO, 1, "", None, ts, None
        ),
    ):
        with pytest.raises(ValueError):
            invalid_call()

    for args, expected_field in (
        ((0, "T", "B"), "user_id"),
        ((1, "   ", "B"), "title"),
        ((1, "T", ""), "body"),
    ):
        with pytest.raises(InvalidResolvedAccountDataError) as exc_info:
            ResolvedAccountData(*args)
        assert exc_info.value.field == expected_field


def test_account_state_record_identity_and_query_behavior() -> None:
    """Verifica has_same_identity y should_be_queried (con y sin TTL) en AccountStateRecord."""
    state_resolved = make_state(
        7, status=AccountStatus.RESUELTA, resolved_at="2026-10-21T03:00:00Z"
    )
    state_failed_same_id = make_state(
        7, status=AccountStatus.FALLO_TECNICO, cause="TIMEOUT_AGOTADO"
    )
    state_other_id = make_state(
        12,
        status=AccountStatus.NO_EXISTE,
        cause="RECURSO_NO_ENCONTRADO",
        resolved_at="2026-10-21T03:00:00Z",
    )

    assert state_resolved.has_same_identity(state_failed_same_id) is True
    assert state_resolved.has_same_identity(state_other_id) is False
    assert state_resolved.should_be_queried() is False
    assert state_other_id.should_be_queried() is False
    assert state_failed_same_id.should_be_queried() is True

    # Dentro del TTL (3600s): no se reconsulta
    now_fresh = UtcTimestamp("2026-10-21T03:30:00Z")
    assert state_resolved.should_be_queried(now=now_fresh, ttl_seconds=3600.0) is False
    assert state_other_id.should_be_queried(now=now_fresh, ttl_seconds=3600.0) is False

    # Expirado el TTL (>= 3600s): tanto RESUELTA como NO_EXISTE se refrescan
    now_expired = UtcTimestamp("2026-10-21T04:00:00Z")
    assert state_resolved.should_be_queried(now=now_expired, ttl_seconds=3600.0) is True
    assert state_other_id.should_be_queried(now=now_expired, ttl_seconds=3600.0) is True

    for bad_call in (
        lambda: state_resolved.should_be_queried(now="2026-10-21T04:00:00Z", ttl_seconds=60.0),  # type: ignore[arg-type]
        lambda: state_resolved.should_be_queried(now=now_expired, ttl_seconds=-1.0),
        lambda: state_resolved.should_be_queried(now=now_expired, ttl_seconds=True),  # type: ignore[arg-type]
    ):
        with pytest.raises(ValueError):
            bad_call()


@pytest.mark.parametrize(
    ("fetch_result", "expected_overrides"),
    [
        (
            ExternalFetchResult(
                status=AccountStatus.RESUELTA,
                attempts=2,
                payload=ResolvedAccountData(3, "Título 42", "Cuerpo 42"),
                cause="CAUSA_DESCARTADA",
            ),
            {"attempts": 2, "user_id": 3, "title": "Título 42", "body": "Cuerpo 42"},
        ),
        (
            ExternalFetchResult.not_found(1),
            {"status": AccountStatus.NO_EXISTE, "cause": "RECURSO_NO_ENCONTRADO"},
        ),
        (
            ExternalFetchResult.technical_failure(
                4, "TIMEOUT_AGOTADO", ErrorCategory.TECNICO_TRANSITORIO
            ),
            {"status": AccountStatus.FALLO_TECNICO, "attempts": 4, "cause": "TIMEOUT_AGOTADO"},
        ),
    ],
)
def test_account_state_record_from_fetch_result_builds_expected_domain_states(
    fetch_result: ExternalFetchResult, expected_overrides: dict[str, object]
) -> None:
    """Verifica que AccountStateRecord.from_fetch_result construya los estados de dominio."""
    acc_id, ts = AccountId(42), UtcTimestamp("2026-10-21T04:00:00Z")
    assert AccountStateRecord.from_fetch_result(acc_id, fetch_result, ts) == make_state(
        acc_id, last_attempt_at=ts, **expected_overrides
    )
