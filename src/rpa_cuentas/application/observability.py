"""Primitiva de observabilidad estructurada de la capa de aplicación."""

from __future__ import annotations

import logging


class EventLogger:
    """Helper de observabilidad para emitir eventos estructurados key=value."""

    def __init__(self, logger: logging.Logger | None = None, name: str = "rpa_cuentas") -> None:
        self.raw = logger or logging.getLogger(name)

    def __call__(self, event: str, *extra: object, level: str = "info", **kw: object) -> None:
        parts = [f"event={event}", *(f"{k}={v}" for k, v in kw.items()), *(str(x) for x in extra)]
        getattr(self.raw, level)(" ".join(parts))


__all__ = ["EventLogger"]
