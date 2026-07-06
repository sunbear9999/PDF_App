from core.services.data.csv_loader_service import CsvLoaderService


def test_is_available_always_true():
    assert CsvLoaderService.is_available() is True


def test_loads_csv_with_header(tmp_path):
    path = tmp_path / "sample.csv"
    path.write_text("Name,Age,City\nAlice,30,Boston\nBob,25,Denver\n", encoding="utf-8")

    state = CsvLoaderService().load(str(path))

    assert state is not None
    assert state.headers == ["Name", "Age", "City"]
    assert state.rows == [["Alice", "30", "Boston"], ["Bob", "25", "Denver"]]
    assert state.provenance.source_type == "csv"
    assert state.provenance.source_path == str(path)


def test_loads_tsv_with_tab_delimiter(tmp_path):
    path = tmp_path / "sample.tsv"
    path.write_text("Name\tAge\nAlice\t30\nBob\t25\n", encoding="utf-8")

    state = CsvLoaderService().load(str(path))

    assert state is not None
    assert state.headers == ["Name", "Age"]
    assert state.rows == [["Alice", "30"], ["Bob", "25"]]
    assert state.provenance.source_type == "tsv"


def test_missing_file_returns_none(tmp_path):
    assert CsvLoaderService().load(str(tmp_path / "missing.csv")) is None


def test_empty_file_returns_none(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")
    assert CsvLoaderService().load(str(path)) is None
