"""Adaptador secundario de lectura de cuentas médicas desde archivo CSV con csv.reader."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

from rpa_cuentas.application.ports.outbound_ports import (
    AccountSourcePort,
    InputSourceError,
    RawAccountEntry,
)

EXPECTED_HEADER = "id_cuenta"


def clean_csv_cell(raw_value: str) -> str:
    """Limpia espacios exteriores y un par de comillas envolventes de una celda CSV."""
    cleaned = raw_value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        return cleaned[1:-1].strip()
    return cleaned


class CsvAccountSource(AccountSourcePort):
    """Fuente de cuentas que lee un archivo CSV en flujo conservando líneas vacías y numeración."""

    def __init__(self, csv_path: Path | str) -> None:
        self._csv_path = Path(csv_path)

    def read_rows(self) -> Iterator[RawAccountEntry]:
        """Lee el archivo CSV validando el encabezado en la línea 1 y emitiendo RawAccountEntry."""
        if not self._csv_path.exists():
            raise InputSourceError(f"El archivo de entrada no existe: {self._csv_path}")
        if not self._csv_path.is_file():
            raise InputSourceError(f"La ruta de entrada no es un archivo regular: {self._csv_path}")

        try:
            with self._csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
                reader = csv.reader(csv_file)
                try:
                    header_row = next(reader)
                except StopIteration as exc:
                    raise InputSourceError(
                        f"El archivo CSV está vacío y no contiene el encabezado "
                        f"'{EXPECTED_HEADER}': {self._csv_path}"
                    ) from exc

                if len(header_row) != 1 or header_row[0].strip() != EXPECTED_HEADER:
                    raise InputSourceError(
                        f"Encabezado CSV inválido en línea 1: se esperaba "
                        f"'{EXPECTED_HEADER}', se recibió '{','.join(header_row)}'."
                    )

                for row in reader:
                    raw_value = "" if not row else ",".join(row)
                    yield RawAccountEntry(
                        line_number=reader.line_num,
                        raw_value=raw_value,
                        cleaned_value=clean_csv_cell(raw_value),
                    )
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            raise InputSourceError(
                f"No fue posible leer el archivo CSV '{self._csv_path}': {exc}"
            ) from exc
