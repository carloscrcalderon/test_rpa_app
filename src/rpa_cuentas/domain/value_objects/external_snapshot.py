"""Objetos de valor y enumeraciones del resultado de consulta externa."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from rpa_cuentas.domain.value_objects.account_id import AccountId, require_account_id


class AccountStatus(StrEnum):
    """Estados posibles de una cuenta médica tras su procesamiento."""

    RESUELTA = "RESUELTA"
    NO_EXISTE = "NO_EXISTE"
    FALLO_TECNICO = "FALLO_TECNICO"


class ErrorCategory(StrEnum):
    """Categorías semánticas de fallo con consecuencias operativas distintas."""

    NEGOCIO = "NEGOCIO"
    TECNICO_TRANSITORIO = "TECNICO_TRANSITORIO"
    TECNICO_DEFINITIVO = "TECNICO_DEFINITIVO"


class InvalidResolvedAccountDataError(ValueError):
    """Error cuando los atributos de ResolvedAccountData incumplen invariantes del dominio."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class UtcTimestamp:
    """Value Object sin ID que garantiza una marca de tiempo ISO-8601 UTC válida."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("UtcTimestamp requiere una cadena ISO-8601 no vacía.")
        try:
            parsed = datetime.fromisoformat(self.value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Formato ISO-8601 inválido en UtcTimestamp: {self.value!r}") from exc
        if parsed.tzinfo is None:
            raise ValueError(
                f"UtcTimestamp requiere zona horaria explícita (UTC), se recibió: {self.value!r}"
            )


@dataclass(frozen=True)
class DeduplicatedAccountItem:
    """Cuenta médica única dentro de un lote con su conteo de apariciones y orden inicial."""

    account_id: AccountId
    occurrences: int
    first_seen_order: int

    def __post_init__(self) -> None:
        require_account_id(self.account_id, "DeduplicatedAccountItem")
        if (
            isinstance(self.occurrences, bool)
            or not isinstance(self.occurrences, int)
            or self.occurrences < 1
        ):
            raise ValueError(
                f"occurrences debe ser un entero >= 1, se recibió: {self.occurrences!r}"
            )
        if (
            isinstance(self.first_seen_order, bool)
            or not isinstance(self.first_seen_order, int)
            or self.first_seen_order < 0
        ):
            raise ValueError(
                f"first_seen_order debe ser un entero >= 0, se recibió: {self.first_seen_order!r}"
            )


@dataclass(frozen=True)
class ResolvedAccountData:
    """Value Object puro (sin ID de entidad) auto-validado en __post_init__."""

    user_id: int
    title: str
    body: str

    def __post_init__(self) -> None:
        if isinstance(self.user_id, bool) or not isinstance(self.user_id, int) or self.user_id <= 0:
            raise InvalidResolvedAccountDataError(
                "user_id",
                f"user_id debe ser un entero positivo (> 0), se recibió: {self.user_id!r}",
            )
        if not isinstance(self.title, str) or not self.title.strip():
            raise InvalidResolvedAccountDataError("title", "title no puede ser vacío.")
        if not isinstance(self.body, str) or not self.body.strip():
            raise InvalidResolvedAccountDataError("body", "body no puede ser vacío.")


@dataclass(frozen=True)
class ExternalFetchResult:
    """Value Object auto-validado para el resultado de consulta externa."""

    status: AccountStatus
    attempts: int
    payload: ResolvedAccountData | None = None
    cause: str | None = None
    error_category: ErrorCategory | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, AccountStatus):
            raise ValueError(f"status inválido: {self.status!r}")
        if (
            isinstance(self.attempts, bool)
            or not isinstance(self.attempts, int)
            or self.attempts < 1
        ):
            raise ValueError(f"attempts debe ser un entero >= 1, se recibió: {self.attempts!r}")
        if self.status == AccountStatus.RESUELTA and not isinstance(
            self.payload, ResolvedAccountData
        ):
            raise ValueError("Un resultado RESUELTA requiere un ResolvedAccountData válido.")
        if self.status != AccountStatus.RESUELTA and self.payload is not None:
            raise ValueError("Solo un resultado RESUELTA puede incluir payload.")
        if self.status != AccountStatus.RESUELTA and (not self.cause or not self.cause.strip()):
            raise ValueError("Los resultados no resueltos requieren una causa semántica no vacía.")

    @classmethod
    def resolved(cls, attempts: int, payload: ResolvedAccountData) -> ExternalFetchResult:
        """Crea un resultado exitoso con datos validados."""
        return cls(status=AccountStatus.RESUELTA, attempts=attempts, payload=payload)

    @classmethod
    def not_found(cls, attempts: int, cause: str = "RECURSO_NO_ENCONTRADO") -> ExternalFetchResult:
        """Crea un resultado de negocio para cuenta inexistente."""
        return cls(
            status=AccountStatus.NO_EXISTE,
            attempts=attempts,
            cause=cause,
            error_category=ErrorCategory.NEGOCIO,
        )

    @classmethod
    def technical_failure(
        cls,
        attempts: int,
        cause: str,
        category: ErrorCategory = ErrorCategory.TECNICO_DEFINITIVO,
    ) -> ExternalFetchResult:
        """Crea un resultado de fallo técnico transitorio o definitivo."""
        return cls(
            status=AccountStatus.FALLO_TECNICO,
            attempts=attempts,
            cause=cause,
            error_category=category,
        )
