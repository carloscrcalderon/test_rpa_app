"""Adaptador secundario HTTP para consulta de cuentas en el servicio externo."""

from __future__ import annotations

import logging
import math
import random
import time
from collections.abc import Callable, Mapping

import requests

from rpa_cuentas.application.ports.outbound_ports import (
    AccountId,
    ErrorCategory,
    EventLogger,
    ExternalAccountClientPort,
    ExternalFetchResult,
    InvalidResolvedAccountDataError,
    ResolvedAccountData,
    RetryPolicy,
)

REQUIRED_EXTERNAL_FIELDS: tuple[str, ...] = ("userId", "id", "title", "body")
_FIELD_ERROR_CAUSES: Mapping[str, str] = {
    "user_id": "TIPO_INVALIDO: userId",
    "title": "TIPO_O_VALOR_INVALIDO: title",
    "body": "TIPO_O_VALOR_INVALIDO: body",
}


class ExternalContractError(ValueError):
    """Excepción del adaptador HTTP para respuestas JSON que violan el contrato esperado."""

    def __init__(self, cause: str) -> None:
        self.cause = cause
        self.category = ErrorCategory.TECNICO_DEFINITIVO
        super().__init__(cause)


def validate_external_payload(account_id: AccountId, raw_payload: object) -> ResolvedAccountData:
    """Valida el sobre JSON de /posts/{id} y delega invariantes a ResolvedAccountData."""
    if not isinstance(raw_payload, Mapping):
        raise ExternalContractError("PAYLOAD_NO_ES_OBJETO_JSON")

    if missing := [f for f in REQUIRED_EXTERNAL_FIELDS if f not in raw_payload]:
        raise ExternalContractError(f"CAMPOS_FALTANTES: {', '.join(missing)}")

    raw_id = raw_payload["id"]
    if isinstance(raw_id, bool) or not isinstance(raw_id, int):
        raise ExternalContractError("TIPO_INVALIDO: id")

    if raw_id != account_id.value:
        raise ExternalContractError(
            f"ID_NO_COINCIDE: esperado={account_id.value}, recibido={raw_id}"
        )

    try:
        return ResolvedAccountData(
            user_id=raw_payload["userId"],
            title=raw_payload["title"],
            body=raw_payload["body"],
        )
    except InvalidResolvedAccountDataError as exc:
        cause = _FIELD_ERROR_CAUSES.get(exc.field, f"TIPO_O_VALOR_INVALIDO: {exc.field}")
        raise ExternalContractError(cause) from exc


def classify_http_status(status_code: int) -> ErrorCategory | None:
    """Clasifica un código de estado HTTP en su categoría de dominio (None si es 200 OK)."""
    if status_code == 200:
        return None
    if status_code == 404:
        return ErrorCategory.NEGOCIO
    if status_code == 429 or 500 <= status_code < 600:
        return ErrorCategory.TECNICO_TRANSITORIO
    return ErrorCategory.TECNICO_DEFINITIVO


def _parse_retry_after_seconds(retry_after_header: str | None) -> float | None:
    """Parsea un valor numérico no negativo de Retry-After o retorna None si no es válido."""
    if retry_after_header is None:
        return None
    try:
        parsed = float(retry_after_header.strip())
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _default_jitter_ratio() -> float:
    """Genera una proporción aleatoria de jitter entre 0.0 y RetryPolicy.MAX_JITTER_RATIO."""
    return random.uniform(0.0, RetryPolicy.MAX_JITTER_RATIO)


class HttpAccountClient(ExternalAccountClientPort):
    """Cliente HTTP que consulta cuentas médicas con timeout explícito y reintentos acotados."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 10.0,
        request_delay_seconds: float = 0.0,
        retry_policy: RetryPolicy | None = None,
        session: requests.Session | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        jitter_fn: Callable[[], float] = _default_jitter_ratio,
        logger: logging.Logger | None = None,
    ) -> None:
        self._base_url, self._timeout_seconds = base_url.rstrip("/"), timeout_seconds
        self._retry_policy = retry_policy or RetryPolicy()
        self._request_delay_seconds, self._session = (
            request_delay_seconds,
            session or requests.Session(),
        )
        self._sleep_fn, self._jitter_fn = sleep_fn, jitter_fn
        self._log = EventLogger(logger, "rpa_cuentas.adapters.http_account_client")

    def close(self) -> None:
        """Cierra la sesión HTTP subyacente."""
        self._session.close()

    def fetch_account(self, account_id: AccountId) -> ExternalFetchResult:
        """Consulta una cuenta médica aplicando reintentos únicamente a fallos transitorios."""
        if self._request_delay_seconds > 0:
            self._sleep_fn(self._request_delay_seconds)

        url = f"{self._base_url}/posts/{account_id.value}"
        last_cause = "FALLO_TRANSITORIO_AGOTADO"
        max_attempts = self._retry_policy.max_attempts

        for attempt in range(1, max_attempts + 1):
            result, server_wait, last_cause = self._execute_single_attempt(account_id, url, attempt)
            if result is not None:
                return result

            if self._retry_policy.should_retry(ErrorCategory.TECNICO_TRANSITORIO, attempt):
                delay = self._retry_policy.next_delay(
                    attempt=attempt,
                    server_wait_seconds=server_wait,
                    jitter_ratio=self._jitter_fn(),
                )
                self._log(
                    "retry_scheduled", level="warning", account_id=account_id.value,
                    attempt=f"{attempt}/{max_attempts}",
                    category=ErrorCategory.TECNICO_TRANSITORIO.value,
                    cause=last_cause, wait_seconds=f"{delay:.3f}",
                )  # fmt: skip
                self._sleep_fn(delay)

        return ExternalFetchResult.technical_failure(
            max_attempts, last_cause, ErrorCategory.TECNICO_TRANSITORIO
        )

    def _execute_single_attempt(
        self, account_id: AccountId, url: str, attempt: int
    ) -> tuple[ExternalFetchResult | None, float | None, str]:
        """Ejecuta un intento HTTP y retorna (resultado_final, server_wait_seconds, causa)."""
        try:
            response = self._session.get(
                url, headers={"Accept": "application/json"}, timeout=self._timeout_seconds
            )
        except requests.Timeout:
            return None, None, "TIMEOUT_AGOTADO"
        except requests.RequestException:
            return None, None, "ERROR_RED_AGOTADO"

        category = classify_http_status(response.status_code)
        if category is None:
            return self._parse_success_response(account_id, response, attempt), None, ""
        if category == ErrorCategory.NEGOCIO:
            return ExternalFetchResult.not_found(attempt), None, ""
        if not self._retry_policy.is_retryable_category(category):
            cause = f"HTTP_4XX_DEFINITIVO: {response.status_code}"
            return ExternalFetchResult.technical_failure(attempt, cause), None, ""

        server_wait = _parse_retry_after_seconds(response.headers.get("Retry-After"))
        code = response.status_code
        transient_cause = "HTTP_429_AGOTADO" if code == 429 else f"HTTP_5XX_AGOTADO: {code}"
        return None, server_wait, transient_cause

    @staticmethod
    def _parse_success_response(
        account_id: AccountId, response: requests.Response, attempt: int
    ) -> ExternalFetchResult:
        """Valida el cuerpo JSON de una respuesta HTTP 200 contra el contrato esperado."""
        try:
            raw_payload = response.json()
        except ValueError:
            return ExternalFetchResult.technical_failure(attempt, "JSON_INVALIDO")

        try:
            payload = validate_external_payload(account_id, raw_payload)
        except ExternalContractError as exc:
            return ExternalFetchResult.technical_failure(attempt, exc.cause)

        return ExternalFetchResult.resolved(attempt, payload)
