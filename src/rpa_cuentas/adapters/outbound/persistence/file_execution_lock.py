"""Adaptador secundario de bloqueo de ejecución por archivo con flock de kernel (fcntl.flock)."""

from __future__ import annotations

import contextlib
import fcntl
import os
from pathlib import Path
from types import TracebackType

from rpa_cuentas.application.ports.outbound_ports import (
    ConcurrentExecutionError,
    ExecutionLockPort,
)


class FileExecutionLock(ExecutionLockPort):
    """Guardián de exclusión mutua en host basado en flock exclusivo sobre descriptor abierto."""

    def __init__(self, lock_path: Path | str) -> None:
        self._lock_path = Path(lock_path)
        self._fd: int | None = None

    def acquire(self) -> None:
        """Adquiere el bloqueo exclusivo de kernel o lanza ConcurrentExecutionError."""
        if self._fd is not None:
            raise ConcurrentExecutionError(
                f"Ejecución simultánea detectada: el bloqueo '{self._lock_path}' ya fue adquirido."
            )
        try:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        except OSError as exc:
            raise ConcurrentExecutionError(
                f"No fue posible abrir el archivo de bloqueo en '{self._lock_path}': {exc}"
            ) from exc

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            existing_pid = self._read_existing_pid()
            pid_detail = f" (PID registrado: {existing_pid})" if existing_pid else ""
            raise ConcurrentExecutionError(
                f"Ejecución simultánea detectada: el archivo de bloqueo "
                f"'{self._lock_path}' ya existe{pid_detail}."
            ) from exc
        except OSError as exc:
            os.close(fd)
            raise ConcurrentExecutionError(
                f"No fue posible adquirir el bloqueo en '{self._lock_path}': {exc}"
            ) from exc

        try:
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
            os.fsync(fd)
        except OSError as exc:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            raise ConcurrentExecutionError(
                f"No fue posible registrar el PID en '{self._lock_path}': {exc}"
            ) from exc

        self._fd = fd

    def release(self) -> None:
        """Libera el descriptor y elimina el archivo de bloqueo si fue adquirido."""
        if self._fd is None:
            return
        fd = self._fd
        self._fd = None
        with contextlib.suppress(OSError):
            self._lock_path.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(fd)

    def _read_existing_pid(self) -> str:
        """Intenta leer el PID contenido en un archivo de bloqueo activo."""
        try:
            return self._lock_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def __enter__(self) -> FileExecutionLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.release()
