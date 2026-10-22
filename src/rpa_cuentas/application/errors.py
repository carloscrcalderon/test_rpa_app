"""Excepciones de frontera de la capa de aplicación."""

from __future__ import annotations


class FatalBatchError(RuntimeError):
    """Error fatal que impide iniciar o completar la ejecución del lote (código de salida 2)."""


class InputSourceError(FatalBatchError):
    """Error fatal al abrir, leer o validar el encabezado de la fuente de entrada."""


class ConcurrentExecutionError(FatalBatchError):
    """Error fatal cuando otra instancia del proceso ya mantiene el bloqueo del lote."""


class PersistenceError(FatalBatchError):
    """Error fatal al leer o escribir en el repositorio de estado."""


class ReportPublicationError(FatalBatchError):
    """Error fatal al construir o publicar de forma atómica el reporte consolidado."""


__all__ = [
    "ConcurrentExecutionError",
    "FatalBatchError",
    "InputSourceError",
    "PersistenceError",
    "ReportPublicationError",
]
