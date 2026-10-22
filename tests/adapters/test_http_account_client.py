"""Pruebas unitarias para HttpAccountClient y traducción de contrato HTTP/JSON."""

from __future__ import annotations

from typing import Any

import pytest
import requests
import responses
from dirty_equals import HasAttributes

from rpa_cuentas.adapters.outbound.external_services.http_account_client import (
    ExternalContractError,
    HttpAccountClient,
    _parse_retry_after_seconds,
    classify_http_status,
    validate_external_payload,
)
from rpa_cuentas.domain import (
    AccountId,
    AccountStatus,
    ErrorCategory,
    ResolvedAccountData,
    RetryPolicy,
)

BASE_URL = "https://jsonplaceholder.typicode.com"


def test_classify_http_status_maps_codes_to_domain_error_categories() -> None:
    """Verifica la traducción de códigos HTTP a categorías semánticas del dominio."""
    assert classify_http_status(200) is None
    assert classify_http_status(404) == ErrorCategory.NEGOCIO
    for code in (429, 500, 502, 503, 504):
        assert classify_http_status(code) == ErrorCategory.TECNICO_TRANSITORIO
    for code in (400, 401, 403):
        assert classify_http_status(code) == ErrorCategory.TECNICO_DEFINITIVO


def test_parse_retry_after_seconds_handles_valid_and_invalid_headers() -> None:
    """Verifica el parseo de la cabecera Retry-After en el borde HTTP."""
    assert [_parse_retry_after_seconds(v) for v in ("7", " 12.5 ", "0")] == [7.0, 12.5, 0.0]
    for bad in (None, "-5", "Wed, 21 Oct 2026 07:28:00 GMT", "nan"):
        assert _parse_retry_after_seconds(bad) is None


def test_validate_external_payload_accepts_valid_matching_payload() -> None:
    """Verifica que un payload JSON válido con id coincidente retorne ResolvedAccountData."""
    payload = validate_external_payload(
        AccountId(42),
        {
            "userId": 5, "id": 42,
            "title": "Cuenta médica auditada",
            "body": "Detalle completo de la radicación",
        },
    )  # fmt: skip
    assert payload == ResolvedAccountData(
        5, "Cuenta médica auditada", "Detalle completo de la radicación"
    )


@pytest.mark.parametrize(
    ("raw_payload", "expected_cause_fragment"),
    [
        (["no", "es", "dict"], "PAYLOAD_NO_ES_OBJETO_JSON"),
        ({"userId": 1, "id": 7, "title": "Solo tres"}, "CAMPOS_FALTANTES"),
        ({"userId": True, "id": 7, "title": "T", "body": "B"}, "TIPO_INVALIDO: userId"),
        ({"userId": "1", "id": 7, "title": "T", "body": "B"}, "TIPO_INVALIDO: userId"),
        ({"userId": 1, "id": False, "title": "T", "body": "B"}, "TIPO_INVALIDO: id"),
        ({"userId": 1, "id": "7", "title": "T", "body": "B"}, "TIPO_INVALIDO: id"),
        ({"userId": 1, "id": 99, "title": "T", "body": "B"}, "ID_NO_COINCIDE"),
        ({"userId": 1, "id": 7, "title": "", "body": "B"}, "TIPO_O_VALOR_INVALIDO: title"),
        ({"userId": 1, "id": 7, "title": 123, "body": "B"}, "TIPO_O_VALOR_INVALIDO: title"),
        ({"userId": 1, "id": 7, "title": "T", "body": "   "}, "TIPO_O_VALOR_INVALIDO: body"),
        ({"userId": 1, "id": 7, "title": "T", "body": None}, "TIPO_O_VALOR_INVALIDO: body"),
    ],
)
def test_validate_external_payload_rejects_corrupt_or_mismatched_payloads(
    raw_payload: object, expected_cause_fragment: str
) -> None:
    """Verifica que payloads corruptos o con id distinto lancen ExternalContractError."""
    with pytest.raises(ExternalContractError) as exc_info:
        validate_external_payload(AccountId(7), raw_payload)

    assert expected_cause_fragment in exc_info.value.cause
    assert exc_info.value.category == ErrorCategory.TECNICO_DEFINITIVO


@responses.activate
def test_fetch_account_resolves_valid_http_200_on_first_attempt() -> None:
    """Verifica que un HTTP 200 con payload válido retorne RESUELTA en 1 intento sin dormir."""
    sleeps: list[float] = []
    responses.add(
        responses.GET,
        f"{BASE_URL}/posts/7",
        json={"userId": 1, "id": 7, "title": "Cuenta 7", "body": "Detalle 7"},
        status=200,
    )
    client = HttpAccountClient(base_url=BASE_URL, sleep_fn=sleeps.append, jitter_fn=lambda: 0.0)
    result = client.fetch_account(AccountId(7))

    assert result == HasAttributes(
        status=AccountStatus.RESUELTA, attempts=1, cause=None,
        payload=HasAttributes(user_id=1, title="Cuenta 7"),
    )  # fmt: skip
    assert sleeps == [] and len(responses.calls) == 1


@pytest.mark.parametrize(
    ("account_id", "mock_kwargs", "expected_status", "expected_cause", "expected_category"),
    [
        (
            101,
            {"json": {}, "status": 404},
            AccountStatus.NO_EXISTE,
            "RECURSO_NO_ENCONTRADO",
            ErrorCategory.NEGOCIO,
        ),
        (
            88,
            {"status": 403},
            AccountStatus.FALLO_TECNICO,
            "HTTP_4XX_DEFINITIVO: 403",
            ErrorCategory.TECNICO_DEFINITIVO,
        ),
        (
            12,
            {"body": "<html>bad gateway disguised as 200</html>", "status": 200},
            AccountStatus.FALLO_TECNICO,
            "JSON_INVALIDO",
            ErrorCategory.TECNICO_DEFINITIVO,
        ),
        (
            23,
            {
                "json": {"userId": 1, "id": 999, "title": "Id cruzado", "body": "Cuerpo"},
                "status": 200,
            },
            AccountStatus.FALLO_TECNICO,
            "ID_NO_COINCIDE: esperado=23, recibido=999",
            ErrorCategory.TECNICO_DEFINITIVO,
        ),
    ],
)
@responses.activate
def test_fetch_account_does_not_retry_non_transient_responses(
    account_id: int,
    mock_kwargs: dict[str, Any],
    expected_status: AccountStatus,
    expected_cause: str,
    expected_category: ErrorCategory,
) -> None:
    """Verifica que 404, 4xx definitivos, JSON inválido o ID cruzado fallen en 1 intento."""
    sleeps: list[float] = []
    responses.add(responses.GET, f"{BASE_URL}/posts/{account_id}", **mock_kwargs)
    client = HttpAccountClient(
        base_url=BASE_URL, retry_policy=RetryPolicy(max_attempts=4), sleep_fn=sleeps.append
    )

    assert client.fetch_account(AccountId(account_id)) == HasAttributes(
        status=expected_status,
        attempts=1,
        cause=expected_cause,
        error_category=expected_category,
    )
    assert sleeps == [] and len(responses.calls) == 1


@responses.activate
def test_fetch_account_retries_5xx_and_429_respecting_retry_after_then_succeeds() -> None:
    """Verifica reintentos ante 503 y 429 respetando Retry-After hasta resolver en 3 intentos."""
    sleeps: list[float] = []
    responses.add(responses.GET, f"{BASE_URL}/posts/45", status=503)
    responses.add(responses.GET, f"{BASE_URL}/posts/45", status=429, headers={"Retry-After": "7"})
    responses.add(
        responses.GET,
        f"{BASE_URL}/posts/45",
        json={"userId": 5, "id": 45, "title": "Recuperada", "body": "Cuerpo 45"},
        status=200,
    )
    client = HttpAccountClient(
        base_url=BASE_URL,
        retry_policy=RetryPolicy(
            max_attempts=4, base_delay_seconds=1.0, backoff_factor=2.0, max_delay_seconds=30.0
        ),
        sleep_fn=sleeps.append,
        jitter_fn=lambda: 0.0,
    )

    assert client.fetch_account(AccountId(45)) == HasAttributes(
        status=AccountStatus.RESUELTA, attempts=3
    )
    assert sleeps == [1.0, 7.0] and len(responses.calls) == 3


@responses.activate
def test_fetch_account_exhausts_retries_on_persistent_timeout() -> None:
    """Verifica que tras agotar los 4 intentos por Timeout retorne FALLO_TECNICO."""
    sleeps: list[float] = []
    for _ in range(4):
        responses.add(
            responses.GET, f"{BASE_URL}/posts/45", body=requests.Timeout("Read timed out")
        )
    client = HttpAccountClient(
        base_url=BASE_URL,
        retry_policy=RetryPolicy(
            max_attempts=4, base_delay_seconds=1.0, backoff_factor=2.0, max_delay_seconds=30.0
        ),
        sleep_fn=sleeps.append,
        jitter_fn=lambda: 0.0,
    )

    assert client.fetch_account(AccountId(45)) == HasAttributes(
        status=AccountStatus.FALLO_TECNICO, attempts=4,
        cause="TIMEOUT_AGOTADO", error_category=ErrorCategory.TECNICO_TRANSITORIO,
    )  # fmt: skip
    assert sleeps == [1.0, 2.0, 4.0] and len(responses.calls) == 4
