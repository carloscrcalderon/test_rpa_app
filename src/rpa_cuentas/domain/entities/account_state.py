"""Entidad de estado persistido de una cuenta médica."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from rpa_cuentas.domain.value_objects.account_id import AccountId, require_account_id
from rpa_cuentas.domain.value_objects.external_snapshot import (
    AccountStatus,
    ExternalFetchResult,
    ResolvedAccountData,
    UtcTimestamp,
)


@dataclass(frozen=True)
class AccountStateRecord:
    """Entidad de dominio cuya identidad es AccountId y cuyos atributos son Value Objects."""

    account_id: AccountId
    status: AccountStatus
    attempts: int
    cause: str | None
    resolved_data: ResolvedAccountData | None
    last_attempt_at: UtcTimestamp
    resolved_at: UtcTimestamp | None

    def __post_init__(self) -> None:
        require_account_id(self.account_id, "AccountStateRecord")
        if not isinstance(self.status, AccountStatus):
            raise ValueError("status debe ser una instancia de AccountStatus.")
        if (
            isinstance(self.attempts, bool)
            or not isinstance(self.attempts, int)
            or self.attempts < 1
        ):
            raise ValueError("attempts debe ser un entero >= 1.")
        if not isinstance(self.last_attempt_at, UtcTimestamp):
            raise ValueError("last_attempt_at debe ser una instancia de UtcTimestamp.")
        if self.resolved_at is not None and not isinstance(self.resolved_at, UtcTimestamp):
            raise ValueError("resolved_at debe ser UtcTimestamp o None.")
        if self.resolved_data is not None and not isinstance(
            self.resolved_data, ResolvedAccountData
        ):
            raise ValueError("resolved_data debe ser ResolvedAccountData o None.")
        self._validate_status_invariants()

    def _validate_status_invariants(self) -> None:
        if self.status == AccountStatus.RESUELTA:
            if self.resolved_data is None or self.resolved_at is None:
                raise ValueError(
                    "Una cuenta RESUELTA requiere resolved_data y resolved_at válidos."
                )
            if self.cause is not None:
                raise ValueError("Una cuenta RESUELTA no debe incluir causa de error.")
            return

        if self.resolved_data is not None:
            raise ValueError("Solo una cuenta RESUELTA puede incluir resolved_data.")
        if not self.cause or not self.cause.strip():
            raise ValueError("Una cuenta no resuelta requiere una causa semántica no vacía.")
        if self.status == AccountStatus.FALLO_TECNICO and self.resolved_at is not None:
            raise ValueError("Una cuenta en FALLO_TECNICO no debe tener resolved_at.")
        if self.status == AccountStatus.NO_EXISTE and self.resolved_at is None:
            raise ValueError("Una cuenta en NO_EXISTE requiere resolved_at.")

    def has_same_identity(self, other: AccountStateRecord) -> bool:
        """Compara igualdad de entidad por identidad (AccountId)."""
        return isinstance(other, AccountStateRecord) and self.account_id == other.account_id

    def should_be_queried(
        self,
        now: UtcTimestamp | None = None,
        ttl_seconds: float | None = None,
    ) -> bool:
        """Indica si el agregado requiere nueva consulta externa según su estado actual y TTL."""
        if now is not None and not isinstance(now, UtcTimestamp):
            raise ValueError("now debe ser UtcTimestamp o None.")
        if ttl_seconds is not None and (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, (int, float))
            or not math.isfinite(ttl_seconds)
            or ttl_seconds < 0
        ):
            raise ValueError(f"ttl_seconds debe ser finito >= 0, se recibió: {ttl_seconds!r}")
        if self.status == AccountStatus.FALLO_TECNICO:
            return True
        if now is None or ttl_seconds is None:
            return False
        ref_ts = self.resolved_at or self.last_attempt_at
        now_dt = datetime.fromisoformat(now.value.replace("Z", "+00:00"))
        ref_dt = datetime.fromisoformat(ref_ts.value.replace("Z", "+00:00"))
        return (now_dt - ref_dt).total_seconds() >= ttl_seconds

    @property
    def user_id(self) -> int | None:
        """Retorna el user_id del ResolvedAccountData encapsulado o None."""
        return self.resolved_data.user_id if self.resolved_data is not None else None

    @property
    def title(self) -> str | None:
        """Retorna el title del ResolvedAccountData encapsulado o None."""
        return self.resolved_data.title if self.resolved_data is not None else None

    @property
    def body(self) -> str | None:
        """Retorna el body del ResolvedAccountData encapsulado o None."""
        return self.resolved_data.body if self.resolved_data is not None else None

    @classmethod
    def from_fetch_result(
        cls,
        account_id: AccountId,
        fetch_result: ExternalFetchResult,
        timestamp: UtcTimestamp,
    ) -> AccountStateRecord:
        """Construye un registro de estado de dominio a partir del resultado de consulta externa."""
        is_terminal = fetch_result.status in {AccountStatus.RESUELTA, AccountStatus.NO_EXISTE}
        return cls(
            account_id=account_id,
            status=fetch_result.status,
            attempts=fetch_result.attempts,
            cause=None if fetch_result.status == AccountStatus.RESUELTA else fetch_result.cause,
            resolved_data=fetch_result.payload,
            last_attempt_at=timestamp,
            resolved_at=timestamp if is_terminal else None,
        )
