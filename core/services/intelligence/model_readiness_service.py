"""Read-only model/catalog snapshots shared by AI Setup and classifier cards."""


class ModelReadinessService:
    allow_without_project = True
    manages_project_locks = True

    def __init__(self, project_manager, dependencies, classifier, policy, *, backends=None, setup=None, roles=None, user_data_dir=''):
        self.project_manager, self.dependencies = project_manager, dependencies
        self.classifier, self.policy = classifier, policy
        self.backends, self.setup, self.roles = backends, setup, roles
        self.user_data_dir, self.hardware = user_data_dir, None

    @property
    def db(self):
        return getattr(self.project_manager, 'db_intelligence', None)

    def read(self, catalog=True):
        settings = self.policy.get_project_settings() if self.policy else {}
        enabled = bool(settings.get('use_sentence_role_enhancer', True))
        return {'dependencies': self.dependencies.list_dependencies() if catalog and self.dependencies else [],
                'sentence_status': self.classifier.status(enabled) if self.classifier else {},
                'enhancer_enabled': enabled}

    def read_setup(self, backend_id=''):
        from core.services.intelligence.model_setup_snapshot import read
        return read(self, backend_id)

    def set_enhancer(self, enabled):
        from contextlib import nullcontext
        with self.db._write_lock if self.db is not None else nullcontext():
            with getattr(self.project_manager, 'db_lock', nullcontext()):
                self.policy.set_project_settings({'use_sentence_role_enhancer': bool(enabled)})
        return self.read(catalog=False)

    def resource_enabled(self, setting_key):
        settings = self.policy.get_project_settings() if self.policy else {}
        return bool(settings.get(setting_key))

    def set_resource_enabled(self, setting_key, enabled):
        """Switch an optional AI Setup resource on or off for this project (catalog keys only)."""
        allowed = {d.setting_key for d in getattr(self.dependencies, '_specs', []) if d.setting_key}
        if setting_key not in allowed:
            raise ValueError(f"'{setting_key}' is not a model setting.")
        from contextlib import nullcontext
        with self.db._write_lock if self.db is not None else nullcontext():
            with getattr(self.project_manager, 'db_lock', nullcontext()):
                self.policy.set_project_settings({setting_key: bool(enabled)})
        return self.resource_enabled(setting_key)
