# core/db/base_db.py
class BaseDB:
    """Base class for all database modules, providing access to the manager's connection."""
    def __init__(self, manager):
        self.manager = manager

    @property
    def _conn(self):
        return self.manager._conn