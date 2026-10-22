"""Puertos primarios (inbound/driving) de la capa de aplicación."""

from __future__ import annotations

from typing import Protocol

from rpa_cuentas.application.dtos import (
    EXIT_FATAL_ERROR,
    EXIT_PARTIAL_FAILURE,
    EXIT_SUCCESS,
    BatchSummary,
    ProcessBatchCommand,
)
from rpa_cuentas.application.errors import FatalBatchError
from rpa_cuentas.application.observability import EventLogger


class ProcessBatchUseCasePort(Protocol):
    """Puerto conductor primario para ejecutar el procesamiento batch de cuentas médicas."""

    def execute(self, command: ProcessBatchCommand) -> BatchSummary:
        """Ejecuta el caso de uso de procesamiento batch bajo las garantías de aplicación."""


__all__ = [
    "EXIT_FATAL_ERROR",
    "EXIT_PARTIAL_FAILURE",
    "EXIT_SUCCESS",
    "BatchSummary",
    "EventLogger",
    "FatalBatchError",
    "ProcessBatchCommand",
    "ProcessBatchUseCasePort",
]
