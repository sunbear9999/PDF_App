from core.registries.data_provider_registry import DataProviderRegistry, DataSourceLoaderSpec


def test_register_and_get_data_source_loader():
    registry = DataProviderRegistry()
    spec = DataSourceLoaderSpec(loader_id="csv_stdlib", label="CSV", extensions=[".csv"], callback=lambda p: None)

    registry.register_data_source_loader(spec)

    assert registry.get_data_source_loader("csv_stdlib") is spec
    assert "csv_stdlib" in registry.data_source_loaders()


def test_get_unregistered_loader_returns_none():
    registry = DataProviderRegistry()
    assert registry.get_data_source_loader("nonexistent") is None


def test_remove_plugin_only_removes_matching_plugin_loaders():
    registry = DataProviderRegistry()
    registry.register_data_source_loader(
        DataSourceLoaderSpec(loader_id="a", label="A", extensions=[".a"], plugin_id="plugin1")
    )
    registry.register_data_source_loader(
        DataSourceLoaderSpec(loader_id="b", label="B", extensions=[".b"], plugin_id="plugin2")
    )

    registry.remove_plugin("plugin1")

    assert registry.get_data_source_loader("a") is None
    assert registry.get_data_source_loader("b") is not None
