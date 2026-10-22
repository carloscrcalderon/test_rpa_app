"""Pruebas unitarias para el adaptador de lectura CSV CsvAccountSource."""

from __future__ import annotations

from pathlib import Path

import pytest

from rpa_cuentas.adapters.outbound.file_io.csv_account_source import (
    CsvAccountSource,
    clean_csv_cell,
)
from rpa_cuentas.application.ports.outbound_ports import InputSourceError
from rpa_cuentas.domain import RawAccountEntry

FIXTURE_CSV = Path(__file__).resolve().parents[1] / "fixtures" / "cuentas_reference.csv"


@pytest.mark.parametrize(
    ("raw_value", "expected_cleaned"),
    [
        ("7", "7"), (" 23", "23"), ("  23  ", "23"), ('"42"', "42"), (' "42" ', "42"),
        ('" 42 "', "42"), ("'42'", "42"), ('""', ""), (' "  " ', ""), ("abc", "abc"),
    ],
)  # fmt: skip
def test_clean_csv_cell_strips_outer_whitespace_and_enclosing_quotes(
    raw_value: str, expected_cleaned: str
) -> None:
    """Verifica que clean_csv_cell elimine espacios exteriores y un par de comillas envolventes."""
    assert clean_csv_cell(raw_value) == expected_cleaned


def test_csv_source_reads_reference_file_preserving_line_numbers_raw_and_cleaned_values() -> None:
    """Verifica que el CSV de referencia emita 21 entradas con raw_value y cleaned_value exactos."""
    rows = list(CsvAccountSource(FIXTURE_CSV).read_rows())

    assert len(rows) == 21
    assert [rows[i] for i in (0, 2, 7, 10, 17, 19, 20)] == [
        RawAccountEntry(2, "7", "7"),
        RawAccountEntry(4, " 23", "23"),
        RawAccountEntry(9, "abc", "abc"),
        RawAccountEntry(12, "", ""),
        RawAccountEntry(19, "42", "42"),
        RawAccountEntry(21, "0", "0"),
        RawAccountEntry(22, "19", "19"),
    ]


@pytest.mark.parametrize(
    ("file_name", "content", "expected_match"),
    [
        ("no_existe.csv", None, "no existe"),
        (".", None, "no es un archivo regular"),
        ("vacio.csv", "", "está vacío"),
        ("mal_encabezado.csv", "cuenta_id\n7\n", "Encabezado CSV inválido"),
    ],
)
def test_csv_source_raises_input_source_error_on_invalid_source(
    tmp_path: Path, file_name: str, content: str | None, expected_match: str
) -> None:
    """Verifica InputSourceError ante archivo inexistente, directorio, vacío o mal encabezado."""
    target = tmp_path if file_name == "." else tmp_path / file_name
    if content is not None:
        target.write_text(content, encoding="utf-8")

    with pytest.raises(InputSourceError, match=expected_match):
        list(CsvAccountSource(target).read_rows())


def test_csv_source_preserves_extra_columns_as_raw_value(tmp_path: Path) -> None:
    """Verifica que filas con comas múltiples se conserven íntegras para rechazo en el dominio."""
    extra_cols_csv = tmp_path / "extra_cols.csv"
    extra_cols_csv.write_text("id_cuenta\n12,34\n", encoding="utf-8")
    assert list(CsvAccountSource(extra_cols_csv).read_rows()) == [
        RawAccountEntry(2, "12,34", "12,34")
    ]
