import pytest

openpyxl = pytest.importorskip("openpyxl")

from core.services.data.xlsx_loader_service import XlsxLoaderService


def _make_xlsx(tmp_path):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Name", "Age", "City"])
    sheet.append(["Alice", 30, "Boston"])
    sheet.append(["Bob", 25, "Denver"])
    path = str(tmp_path / "sample.xlsx")
    workbook.save(path)
    return path


def test_is_available():
    assert XlsxLoaderService.is_available() is True


def test_loads_active_sheet_with_header(tmp_path):
    path = _make_xlsx(tmp_path)

    state = XlsxLoaderService().load(path)

    assert state is not None
    assert state.headers == ["Name", "Age", "City"]
    assert state.rows == [["Alice", "30", "Boston"], ["Bob", "25", "Denver"]]
    assert state.provenance.source_type == "xlsx"
    assert state.provenance.source_path == path


def test_missing_file_returns_none(tmp_path):
    assert XlsxLoaderService().load(str(tmp_path / "missing.xlsx")) is None


def test_corrupt_file_returns_none(tmp_path):
    path = tmp_path / "not_really.xlsx"
    path.write_text("not an xlsx file")
    assert XlsxLoaderService().load(str(path)) is None
