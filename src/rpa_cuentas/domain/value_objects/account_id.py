"""Objeto de valor AccountId y validación de entradas de cuentas pre-normalizadas."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class IncidentReason(StrEnum):
    """Motivos de rechazo en el borde para filas del archivo de entrada."""

    LINEA_VACIA = "LINEA_VACIA"
    NO_ENTERO = "NO_ENTERO"
    NO_POSITIVO = "NO_POSITIVO"


@dataclass(frozen=True)
class RawAccountEntry:
    """Entrada cruda de cuenta leída desde la fuente de entrada con su valor limpio."""

    line_number: int
    raw_value: str
    cleaned_value: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.line_number, bool)
            or not isinstance(self.line_number, int)
            or self.line_number < 1
        ):
            raise ValueError(
                f"line_number debe ser un entero >= 1, se recibió: {self.line_number!r}"
            )
        if not isinstance(self.raw_value, str):
            raise ValueError("raw_value debe ser una cadena (str).")
        if not isinstance(self.cleaned_value, str):
            raise ValueError("cleaned_value debe ser una cadena (str).")


@dataclass(frozen=True)
class InputIncident:
    """Incidencia de entrada registrada para una fila vacía o inválida."""

    line_number: int
    raw_value: str
    reason: IncidentReason

    def __post_init__(self) -> None:
        if (
            isinstance(self.line_number, bool)
            or not isinstance(self.line_number, int)
            or self.line_number < 1
        ):
            raise ValueError(
                f"line_number debe ser un entero >= 1, se recibió: {self.line_number!r}"
            )
        if not isinstance(self.raw_value, str):
            raise ValueError("raw_value debe ser una cadena (str).")
        if not isinstance(self.reason, IncidentReason):
            raise ValueError(
                f"reason debe ser una instancia de IncidentReason, se recibió: {self.reason!r}"
            )


class InvalidAccountIdError(ValueError):
    """Excepción de dominio lanzada cuando un identificador de cuenta no es válido."""

    def __init__(self, reason: IncidentReason, raw_value: str, message: str | None = None) -> None:
        self.reason = reason
        self.raw_value = raw_value
        super().__init__(
            message or f"Identificador de cuenta inválido ({reason.value}): {raw_value!r}"
        )


def _is_base10_integer_literal(text: str) -> bool:
    """Determina si una cadena no vacía representa estrictamente un literal entero en base 10."""
    digits = text[1:] if text.startswith(("+", "-")) else text
    return bool(digits) and digits.isdecimal() and digits.isascii()


@dataclass(frozen=True)
class AccountId:
    """Objeto de valor inmutable que garantiza un identificador de cuenta entero positivo (> 0)."""

    value: int

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int):
            raise InvalidAccountIdError(
                IncidentReason.NO_ENTERO,
                str(self.value),
                f"AccountId requiere un entero, se recibió: {self.value!r}.",
            )
        if self.value <= 0:
            raise InvalidAccountIdError(
                IncidentReason.NO_POSITIVO,
                str(self.value),
                f"AccountId requiere un entero positivo (> 0), se recibió: {self.value}.",
            )

    @classmethod
    def from_clean(cls, cleaned_value: str, raw_value: str | None = None) -> AccountId:
        """Valida una cadena pre-normalizada y construye un AccountId válido o lanza excepción."""
        effective_raw = raw_value if raw_value is not None else cleaned_value
        if not cleaned_value:
            raise InvalidAccountIdError(IncidentReason.LINEA_VACIA, effective_raw)
        if not _is_base10_integer_literal(cleaned_value):
            raise InvalidAccountIdError(IncidentReason.NO_ENTERO, effective_raw)
        parsed = int(cleaned_value, 10)
        if parsed <= 0:
            raise InvalidAccountIdError(IncidentReason.NO_POSITIVO, effective_raw)
        return cls(value=parsed)


def require_account_id(account_id: object, owner: str) -> None:
    """Garantiza en tiempo de ejecución que el identificador sea una instancia de AccountId."""
    if not isinstance(account_id, AccountId):
        raise InvalidAccountIdError(
            IncidentReason.NO_ENTERO,
            str(account_id),
            f"{owner} requiere AccountId, se recibió: {account_id!r}.",
        )


def parse_raw_entry(entry: RawAccountEntry) -> AccountId | InputIncident:
    """Clasifica una entrada de fuente como AccountId válido o como InputIncident con su motivo."""
    try:
        return AccountId.from_clean(entry.cleaned_value, raw_value=entry.raw_value)
    except InvalidAccountIdError as exc:
        return InputIncident(entry.line_number, entry.raw_value, exc.reason)
