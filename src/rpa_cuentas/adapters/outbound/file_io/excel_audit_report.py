"""Adaptador secundario de generación y publicación atómica del reporte Excel consolidado."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable, Sequence
from pathlib import Path

from openpyxl import Workbook

from rpa_cuentas.application.ports.outbound_ports import (
    BatchSummary,
    InputIncident,
    ReportPublicationError,
    ReportRow,
    ReportWriterPort,
)

RESULTADOS_HEADERS: tuple[str, ...] = (
    "id_cuenta",
    "estado",
    "causa",
    "apariciones",
    "user_id",
    "titulo",
    "cuerpo",
    "intentos",
    "fecha_consulta",
)
INCIDENCIAS_HEADERS: tuple[str, ...] = ("linea", "valor_original", "motivo")
RESUMEN_HEADERS: tuple[str, ...] = ("metrica", "valor")


class ExcelReportWriter(ReportWriterPort):
    """Genera el libro Excel con tres hojas y lo publica de forma atómica con os.replace."""

    def __init__(
        self,
        output_path: Path | str,
        replace_fn: Callable[[Path | str, Path | str], None] = os.replace,
    ) -> None:
        self._output_path = Path(output_path)
        self._replace_fn = replace_fn

    def publish_report(
        self,
        rows: Sequence[ReportRow],
        incidents: Sequence[InputIncident],
        summary: BatchSummary,
    ) -> None:
        """Escribe el reporte en un archivo temporal del mismo directorio y reemplaza el destino."""
        try:
            self._output_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ReportPublicationError(
                f"No fue posible crear el directorio de salida '{self._output_path.parent}': {exc}"
            ) from exc

        safe_run_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in summary.run_id)
        temp_path = (
            self._output_path.parent
            / f".{self._output_path.stem}.{safe_run_id}.{os.getpid()}.tmp.xlsx"
        )

        workbook = self._build_workbook(rows=rows, incidents=incidents, summary=summary)
        try:
            workbook.save(str(temp_path))
            workbook.close()
            self._replace_fn(temp_path, self._output_path)
        except Exception as exc:
            raise ReportPublicationError(
                f"No fue posible publicar el reporte Excel en '{self._output_path}': {exc}"
            ) from exc
        finally:
            workbook.close()
            if temp_path.exists():
                with contextlib.suppress(OSError):
                    temp_path.unlink()

    @staticmethod
    def _build_workbook(
        rows: Sequence[ReportRow],
        incidents: Sequence[InputIncident],
        summary: BatchSummary,
    ) -> Workbook:
        """Construye en memoria el Workbook con las tres hojas requeridas."""
        workbook = Workbook()
        resultados_rows = [
            (
                r.id_cuenta.value,
                r.estado.value,
                r.causa or "",
                r.apariciones,
                r.user_id if r.user_id is not None else "",
                r.titulo or "",
                r.cuerpo or "",
                r.intentos,
                r.fecha_consulta,
            )  # fmt: skip
            for r in rows
        ]
        incidencias_rows = [(i.line_number, i.raw_value, i.reason.value) for i in incidents]
        resumen_rows = (("run_id", summary.run_id), *summary.to_metric_rows())

        for idx, (title, headers, sheet_rows) in enumerate(
            (
                ("Resultados", RESULTADOS_HEADERS, resultados_rows),
                ("Incidencias", INCIDENCIAS_HEADERS, incidencias_rows),
                ("Resumen", RESUMEN_HEADERS, resumen_rows),
            )
        ):
            ws = (workbook.active if idx == 0 else None) or workbook.create_sheet(title=title)
            ws.title = title
            ws.append(list(headers))
            for row_values in sheet_rows:
                ws.append(list(row_values))

        return workbook
