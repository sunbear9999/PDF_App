from core.registries.source_format_registry import SourceFormatRegistry, SourceFormatSpec


def _registry():
    registry = SourceFormatRegistry()
    registry.register(SourceFormatSpec(
        source_type="pdf", label="PDF", category="document", extensions=[".pdf"], priority=10,
    ))
    registry.register(SourceFormatSpec(
        source_type="epub", label="EPUB", category="document", extensions=[".epub"],
    ))
    return registry


def test_detect_source_type_by_extension():
    registry = _registry()
    assert registry.detect_source_type("/tmp/paper.pdf") == "pdf"
    assert registry.detect_source_type("/tmp/book.epub") == "epub"


def test_detect_source_type_unknown_extension_returns_unknown_not_pdf():
    registry = _registry()
    assert registry.detect_source_type("/tmp/mystery.xyz") == "unknown"


def test_is_available_reflects_spec_callable():
    registry = SourceFormatRegistry()
    registry.register(SourceFormatSpec(
        source_type="doc", label="Legacy Doc", category="document", extensions=[".doc"],
        is_available=lambda: False, unavailable_reason="needs LibreOffice",
    ))
    assert registry.is_available("doc") is False
    assert registry.get("doc").unavailable_reason == "needs LibreOffice"


def test_is_available_false_for_unregistered_type():
    registry = SourceFormatRegistry()
    assert registry.is_available("nonexistent") is False


def test_all_sorted_by_priority():
    registry = SourceFormatRegistry()
    registry.register(SourceFormatSpec(source_type="a", label="A", category="document", priority=50))
    registry.register(SourceFormatSpec(source_type="b", label="B", category="document", priority=5))
    assert [s.source_type for s in registry.all()] == ["b", "a"]


def test_remove_by_plugin_only_removes_matching_plugin():
    registry = SourceFormatRegistry()
    registry.register(SourceFormatSpec(source_type="a", label="A", category="document", plugin_id="plugin1"))
    registry.register(SourceFormatSpec(source_type="b", label="B", category="document", plugin_id="plugin2"))
    registry.remove_by_plugin("plugin1")
    assert registry.get("a") is None
    assert registry.get("b") is not None
