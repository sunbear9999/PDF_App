from pathlib import Path


def test_source_uses_pyside6_not_pyqt6():
    repo_root = Path(__file__).resolve().parents[1]
    search_roots = ["core", "gui", "plugins", "tests"]
    forbidden_binding = "Py" + "Qt6"
    offenders = []

    for root_name in search_roots:
        for path in (repo_root / root_name).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if forbidden_binding in text:
                offenders.append(str(path.relative_to(repo_root)))

    assert offenders == []
