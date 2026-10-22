"""Pruebas unitarias para el adaptador de bloqueo de ejecución FileExecutionLock."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from rpa_cuentas.adapters.outbound.persistence.file_execution_lock import FileExecutionLock
from rpa_cuentas.application.ports.outbound_ports import ConcurrentExecutionError


def test_file_execution_lock_lifecycle_and_concurrent_rejection(tmp_path: Path) -> None:
    """Verifica creación con PID, rechazo de adquisición concurrente y limpieza al salir."""
    lock_path = tmp_path / "state" / "rpa_cuentas.lock"

    with FileExecutionLock(lock_path):
        assert lock_path.read_text(encoding="utf-8").strip() == str(os.getpid())
        with (
            pytest.raises(ConcurrentExecutionError, match="Ejecución simultánea detectada"),
            FileExecutionLock(lock_path),
        ):
            pass
        assert lock_path.exists()

    assert not lock_path.exists()


def test_file_execution_lock_cleans_up_when_exception_occurs_inside_context(
    tmp_path: Path,
) -> None:
    """Verifica que el archivo de bloqueo se libere aunque ocurra una excepción en el bloque."""
    lock_path = tmp_path / "rpa_cuentas.lock"

    with pytest.raises(RuntimeError, match="fallo simulado"), FileExecutionLock(lock_path):
        assert lock_path.exists()
        raise RuntimeError("fallo simulado")

    assert not lock_path.exists()


def test_file_execution_lock_reclaims_orphan_lock_from_dead_pid(tmp_path: Path) -> None:
    """Verifica que un lockfile huérfano cuyo PID ya no existe sea recuperado automáticamente."""
    lock_path = tmp_path / "state" / "orphan.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("99999999\n", encoding="utf-8")

    with FileExecutionLock(lock_path):
        assert lock_path.read_text(encoding="utf-8").strip() == str(os.getpid())

    assert not lock_path.exists()


def test_file_execution_lock_reclaims_orphan_lock_even_when_pid_is_reused_or_self(
    tmp_path: Path,
) -> None:
    """Verifica que un lockfile huérfano sin flock activo se recupere aunque su PID esté vivo."""
    lock_path = tmp_path / "state" / "reused_pid.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(f"{os.getpid()}\n", encoding="utf-8")

    with FileExecutionLock(lock_path):
        assert lock_path.read_text(encoding="utf-8").strip() == str(os.getpid())

    assert not lock_path.exists()
