# core/papyrus_core.py
from core.events.event_bus import EventBus
from core.engine.process_manager import ProcessRegistry

class PapyrusCore:
    """The headless engine of the application. Plugins interact with this, not the GUI."""
    def __init__(self):
        self.bus = EventBus.get_instance()
        self.services = {}
        self.registries = {}
        
        # 1. Initialize core infrastructure
        self.process_registry = ProcessRegistry()
        
        # 2. Boot registries
        self._boot_registries()
        
        # 3. Boot services
        self._boot_services()

    def get_service(self, service_class):
        return self.services.get(service_class.__name__)

    def register_plugin(self, plugin_module):
        """Called during boot to let plugins inject their own services/blueprints."""
        plugin_module.initialize(self)