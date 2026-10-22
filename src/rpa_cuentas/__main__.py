"""Permite invocar el paquete mediante `python -m rpa_cuentas run --input ...`."""

from __future__ import annotations

from rpa_cuentas.main import cli_entrypoint

if __name__ == "__main__":
    cli_entrypoint()
