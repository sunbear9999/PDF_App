"""Read-only AI Setup discovery; no installation or model generation."""
from core.models.intelligence_models import ModelRole


def read(service, backend_id):
    registry, setup, roles = service.backends, service.setup, service.roles
    states = {}
    for spec in registry.get_all() if registry else []:
        try:
            installed = bool(spec.is_installed())
            states[spec.id] = {'installed': installed, 'version': spec.get_version() if installed else None}
        except Exception as exc:
            states[spec.id] = {'installed': False, 'error': str(exc)}
    spec = registry.get(backend_id) if registry and backend_id else None
    diagnostics = []
    try:
        installed = list(spec.list_installed_models()) if spec else []
    except Exception as exc:
        installed = []
        diagnostics.append(f'Model discovery failed: {exc}')
    installed_ids = [model.id for model in installed]
    descriptions = {role.value: roles.describe_role(role.value, installed_models=installed_ids)
                    for role in ModelRole} if roles else {}
    current_roles = {role: description.get('model') or '' for role, description in descriptions.items()}
    if service.hardware is None:
        service.hardware = setup.get_hardware_info() if setup else {}
    from core.backends.tts_backend import is_piper_installed, list_installed_voices
    return {'backend_id': backend_id, 'backends': states, 'installed_models': installed,
            'role_descriptions': descriptions, 'current_roles': current_roles,
            'settings': service.policy.get_project_settings() if service.policy else {},
            'hardware': service.hardware,
            'diagnostics': diagnostics,
            'piper_installed': is_piper_installed(service.user_data_dir),
            'voices': list_installed_voices(service.user_data_dir)}
