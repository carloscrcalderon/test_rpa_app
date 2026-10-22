"""Pruebas unitarias para las políticas puras del dominio (batch_policies.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from dirty_equals import HasAttributes

from rpa_cuentas.domain import (
    AccountId,
    AccountStatus,
    DeduplicatedAccountItem,
    ErrorCategory,
    IncidentReason,
    InputIncident,
    RetryPolicy,
    UtcTimestamp,
    calculate_retry_delay,
    classify_and_deduplicate_rows,
    should_query_account,
    should_retry_error,
)
from tests.conftest import make_reference_entries, make_state


def test_classify_and_deduplicate_rows_matches_reference_entries() -> None:
    """Verifica que las 21 entradas produzcan 15 únicas, 3 duplicados y 3 incidencias."""
    result = classify_and_deduplicate_rows(make_reference_entries())

    assert result == HasAttributes(lines_read=21, valid_lines=18, duplicates_collapsed=3)
    assert len(result.items) == 15

    expected_order_and_counts = [
        (7, 3, 0), (12, 2, 1), (23, 1, 2), (45, 1, 3), (99, 1, 4),
        (101, 1, 5), (3, 1, 6), (58, 1, 7), (88, 1, 8), (150, 1, 9),
        (64, 1, 10), (31, 1, 11), (42, 1, 12), (76, 1, 13), (19, 1, 14),
    ]  # fmt: skip
    assert list(result.items) == [
        DeduplicatedAccountItem(AccountId(acc_id), occurrences=occurrences, first_seen_order=order)
        for acc_id, occurrences, order in expected_order_and_counts
    ]

    assert list(result.incidents) == [
        InputIncident(9, "abc", IncidentReason.NO_ENTERO),
        InputIncident(12, "", IncidentReason.LINEA_VACIA),
        InputIncident(21, "0", IncidentReason.NO_POSITIVO),
    ]


def test_classify_and_deduplicate_rows_handles_empty_input() -> None:
    """Verifica que una secuencia vacía produzca contadores en cero."""
    assert classify_and_deduplicate_rows([]) == HasAttributes(
        lines_read=0, valid_lines=0, duplicates_collapsed=0, items=(), incidents=()
    )


def test_should_retry_error_only_retries_transient_category() -> None:
    """Verifica que solo la categoría TECNICO_TRANSITORIO sea elegible para reintento."""
    assert (
        should_retry_error(ErrorCategory.TECNICO_TRANSITORIO),
        should_retry_error(ErrorCategory.NEGOCIO),
        should_retry_error(ErrorCategory.TECNICO_DEFINITIVO),
    ) == (True, False, False)


def test_should_query_account_respects_idempotency_rules() -> None:
    """Verifica que solo se consulten cuentas sin estado previo o en FALLO_TECNICO."""
    assert (
        should_query_account(None),
        should_query_account(AccountStatus.RESUELTA),
        should_query_account(AccountStatus.NO_EXISTE),
        should_query_account(AccountStatus.FALLO_TECNICO),
    ) == (True, False, False, True)

    assert (
        should_query_account(make_state(7, status=AccountStatus.RESUELTA)),
        should_query_account(make_state(101, status=AccountStatus.NO_EXISTE)),
        should_query_account(make_state(45, status=AccountStatus.FALLO_TECNICO)),
    ) == (False, False, True)

    resolved = make_state(7, status=AccountStatus.RESUELTA, resolved_at="2026-10-21T03:00:00Z")
    not_found = make_state(101, status=AccountStatus.NO_EXISTE, resolved_at="2026-10-21T03:00:00Z")
    assert (
        should_query_account(resolved, now=UtcTimestamp("2026-10-21T03:05:00Z"), ttl_seconds=600.0)
        is False
    )
    assert (
        should_query_account(resolved, now=UtcTimestamp("2026-10-21T03:10:00Z"), ttl_seconds=600.0)
        is True
    )
    assert (
        should_query_account(not_found, now=UtcTimestamp("2026-10-21T03:15:00Z"), ttl_seconds=600.0)
        is True
    )


def test_calculate_retry_delay_exponential_backoff_cap_jitter_and_server_wait() -> None:
    """Verifica backoff exponencial, jitter acotado, tope máximo y server_wait_seconds puro."""
    assert [
        calculate_retry_delay(n, base_delay=1.0, backoff_factor=2.0, max_delay=5.0)
        for n in (1, 2, 3, 4)
    ] == [1.0, 2.0, 4.0, 5.0]

    assert calculate_retry_delay(1, 1.0, 2.0, 30.0, jitter_ratio=0.20) == pytest.approx(1.2)
    assert calculate_retry_delay(1, 1.0, 2.0, 30.0, server_wait_seconds=12.0) == 12.0
    assert calculate_retry_delay(1, 1.0, 2.0, 30.0, server_wait_seconds=120.0) == 30.0
    assert calculate_retry_delay(2, 1.0, 2.0, 30.0, server_wait_seconds=-5.0) == 2.0


def test_retry_policy_value_object_validates_invariants_and_encapsulates_backoff() -> None:
    """Verifica que RetryPolicy valide sus invariantes y encapsule las decisiones de reintento."""
    policy = RetryPolicy(
        max_attempts=4, base_delay_seconds=1.0, backoff_factor=2.0, max_delay_seconds=10.0
    )
    assert policy.is_retryable_category(ErrorCategory.TECNICO_TRANSITORIO) is True
    assert policy.is_retryable_category(ErrorCategory.TECNICO_DEFINITIVO) is False
    assert policy.should_retry(ErrorCategory.TECNICO_TRANSITORIO, attempt=3) is True
    assert policy.should_retry(ErrorCategory.TECNICO_TRANSITORIO, attempt=4) is False
    assert policy.next_delay(attempt=2, jitter_ratio=0.0) == 2.0
    assert policy.next_delay(attempt=1, server_wait_seconds=25.0) == 10.0

    for bad_kwargs in (
        {"max_attempts": 0},
        {"base_delay_seconds": -1.0},
        {"backoff_factor": 0.5},
        {"max_delay_seconds": 0.0},
    ):
        with pytest.raises(ValueError):
            RetryPolicy(**bad_kwargs)  # type: ignore[arg-type]


def test_domain_layer_has_no_infrastructure_or_presentation_leakage() -> None:
    """Verifica estáticamente que domain/ no contenga términos de transporte o presentación."""
    domain_dir = Path(__file__).resolve().parents[2] / "src" / "rpa_cuentas" / "domain"
    forbidden_tokens = (
        "Retry-After", "userId", "404", "429", "status_code",
        "RawCsvRow", "ReportRow", "requests", "sqlite3", "openpyxl", "argparse",
    )  # fmt: skip
    for py_file in sorted(domain_dir.rglob("*.py")):
        source_text = py_file.read_text(encoding="utf-8")
        for token in forbidden_tokens:
            assert token not in source_text, (
                f"Fuga de frontera detectada en {py_file.name}: contiene {token!r}"
            )
