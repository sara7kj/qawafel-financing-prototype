"""Shared test helpers."""

from pathlib import Path

from openpyxl import Workbook

from financing.schema import SHEETS

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
SAMPLE = ROOT / "data" / "sample"


def write_raw_workbook(path: Path, sheets: dict[str, list[dict]], columns: dict[str, list[str]] | None = None,
                       omit_sheets: tuple[str, ...] = ()) -> Path:
    """Write cell values exactly as given (no amount conversion), one sheet per schema sheet."""
    wb = Workbook()
    wb.remove(wb.active)
    for name, spec in SHEETS.items():
        if name in omit_sheets:
            continue
        header = (columns or {}).get(name, list(spec.columns))
        ws = wb.create_sheet(name)
        ws.append(header)
        for row in sheets.get(name, []):
            ws.append([row.get(c) for c in header])
    wb.save(path)
    return path
