"""API pública de la capa de dominio."""

from rpa_cuentas.domain.entities.account_state import AccountStateRecord
from rpa_cuentas.domain.services.batch_policies import (
    MAX_JITTER_RATIO,
    BatchClassificationResult,
    RetryPolicy,
    calculate_retry_delay,
    classify_and_deduplicate_rows,
    should_query_account,
    should_retry_error,
)
from rpa_cuentas.domain.value_objects.account_id import (
    AccountId,
    IncidentReason,
    InputIncident,
    InvalidAccountIdError,
    RawAccountEntry,
    parse_raw_entry,
    require_account_id,
)
from rpa_cuentas.domain.value_objects.external_snapshot import (
    AccountStatus,
    DeduplicatedAccountItem,
    ErrorCategory,
    ExternalFetchResult,
    InvalidResolvedAccountDataError,
    ResolvedAccountData,
    UtcTimestamp,
)

__all__ = [
    "MAX_JITTER_RATIO",
    "AccountId",
    "AccountStateRecord",
    "AccountStatus",
    "BatchClassificationResult",
    "DeduplicatedAccountItem",
    "ErrorCategory",
    "ExternalFetchResult",
    "IncidentReason",
    "InputIncident",
    "InvalidAccountIdError",
    "InvalidResolvedAccountDataError",
    "RawAccountEntry",
    "ResolvedAccountData",
    "RetryPolicy",
    "UtcTimestamp",
    "calculate_retry_delay",
    "classify_and_deduplicate_rows",
    "parse_raw_entry",
    "require_account_id",
    "should_query_account",
    "should_retry_error",
]
