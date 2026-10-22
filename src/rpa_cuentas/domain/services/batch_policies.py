"""Políticas puras del dominio: deduplicación, idempotencia y cálculo de espera entre reintentos."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import ClassVar

from rpa_cuentas.domain.entities.account_state import AccountStateRecord
from rpa_cuentas.domain.value_objects.account_id import (
    AccountId,
    InputIncident,
    RawAccountEntry,
    parse_raw_entry,
)
from rpa_cuentas.domain.value_objects.external_snapshot import (
    AccountStatus,
    DeduplicatedAccountItem,
    ErrorCategory,
    UtcTimestamp,
)

MAX_JITTER_RATIO: float = 0.25


@dataclass(frozen=True)
class BatchClassificationResult:
    """Resultado inmutable de clasificar y deduplicar las entradas del lote."""

    items: tuple[DeduplicatedAccountItem, ...]
    incidents: tuple[InputIncident, ...]
    lines_read: int
    valid_lines: int
    duplicates_collapsed: int

    def __post_init__(self) -> None:
        for attr_name in ("lines_read", "valid_lines", "duplicates_collapsed"):
            val = getattr(self, attr_name)
            if isinstance(val, bool) or not isinstance(val, int) or val < 0:
                raise ValueError(f"{attr_name} debe ser un entero >= 0, se recibió: {val!r}")
        if self.lines_read != len(self.incidents) + self.valid_lines:
            raise ValueError(
                "Invariante rota en BatchClassificationResult: "
                "lines_read debe coincidir con len(incidents) + valid_lines."
            )
        if self.valid_lines - len(self.items) != self.duplicates_collapsed:
            raise ValueError(
                "Invariante rota en BatchClassificationResult: "
                "valid_lines - len(items) debe coincidir con duplicates_collapsed."
            )


def classify_and_deduplicate_rows(rows: Iterable[RawAccountEntry]) -> BatchClassificationResult:
    """Clasifica cada entrada de fuente y colapsa duplicados preservando orden y apariciones."""
    incidents: list[InputIncident] = []
    counts: Counter[AccountId] = Counter()
    lines_read = 0
    valid_lines = 0

    for entry in rows:
        lines_read += 1
        parsed = parse_raw_entry(entry)
        if isinstance(parsed, InputIncident):
            incidents.append(parsed)
        else:
            valid_lines += 1
            counts[parsed] += 1

    items = tuple(
        DeduplicatedAccountItem(acc_id, occurrences, order)
        for order, (acc_id, occurrences) in enumerate(counts.items())
    )
    return BatchClassificationResult(
        items=items,
        incidents=tuple(incidents),
        lines_read=lines_read,
        valid_lines=valid_lines,
        duplicates_collapsed=valid_lines - len(items),
    )


def should_retry_error(category: ErrorCategory) -> bool:
    """Indica si una categoría de fallo debe reintentarse (solo TECNICO_TRANSITORIO)."""
    return category == ErrorCategory.TECNICO_TRANSITORIO


def should_query_account(
    previous_state: AccountStateRecord | AccountStatus | None,
    now: UtcTimestamp | None = None,
    ttl_seconds: float | None = None,
) -> bool:
    """Determina si una cuenta debe consultarse en el sistema externo según su estado previo."""
    if previous_state is None:
        return True
    if isinstance(previous_state, AccountStateRecord):
        return previous_state.should_be_queried(now=now, ttl_seconds=ttl_seconds)
    return previous_state == AccountStatus.FALLO_TECNICO


def calculate_retry_delay(
    attempt: int,
    base_delay: float = 1.0,
    backoff_factor: float = 2.0,
    max_delay: float = 30.0,
    server_wait_seconds: float | None = None,
    jitter_ratio: float = 0.0,
) -> float:
    """Calcula la espera antes del siguiente reintento respetando espera del servidor y tope."""
    if attempt < 1:
        raise ValueError("El número de intento debe ser mayor o igual a 1.")
    if (
        server_wait_seconds is not None
        and math.isfinite(server_wait_seconds)
        and server_wait_seconds >= 0
    ):
        return min(server_wait_seconds, max_delay)

    jitter_seconds = max(0.0, min(jitter_ratio, MAX_JITTER_RATIO)) * base_delay
    exponential_delay = base_delay * (backoff_factor ** (attempt - 1))
    return min(exponential_delay + jitter_seconds, max_delay)


@dataclass(frozen=True)
class RetryPolicy:
    """Política inmutable de dominio para reintentos acotados con backoff exponencial."""

    MAX_JITTER_RATIO: ClassVar[float] = MAX_JITTER_RATIO

    max_attempts: int = 4
    base_delay_seconds: float = 1.0
    backoff_factor: float = 2.0
    max_delay_seconds: float = 30.0

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or self.max_attempts < 1
        ):
            raise ValueError(
                f"max_attempts debe ser un entero >= 1, se recibió: {self.max_attempts!r}"
            )
        if not math.isfinite(self.base_delay_seconds) or self.base_delay_seconds < 0:
            raise ValueError(
                f"base_delay_seconds debe ser finito >= 0, se recibió: {self.base_delay_seconds!r}"
            )
        if not math.isfinite(self.backoff_factor) or self.backoff_factor < 1.0:
            raise ValueError(
                f"backoff_factor debe ser finito >= 1.0, se recibió: {self.backoff_factor!r}"
            )
        if not math.isfinite(self.max_delay_seconds) or self.max_delay_seconds <= 0:
            raise ValueError(
                f"max_delay_seconds debe ser finito > 0, se recibió: {self.max_delay_seconds!r}"
            )

    @staticmethod
    def is_retryable_category(category: ErrorCategory) -> bool:
        """Indica si una categoría semántica de error admite reintentos."""
        return should_retry_error(category)

    def should_retry(self, category: ErrorCategory, attempt: int) -> bool:
        """Determina si corresponde ejecutar otro intento según la categoría y el intento actual."""
        return attempt < self.max_attempts and should_retry_error(category)

    def next_delay(
        self,
        attempt: int,
        server_wait_seconds: float | None = None,
        jitter_ratio: float = 0.0,
    ) -> float:
        """Calcula la espera en segundos antes del siguiente reintento bajo esta política."""
        return calculate_retry_delay(
            attempt=attempt,
            base_delay=self.base_delay_seconds,
            backoff_factor=self.backoff_factor,
            max_delay=self.max_delay_seconds,
            server_wait_seconds=server_wait_seconds,
            jitter_ratio=jitter_ratio,
        )
