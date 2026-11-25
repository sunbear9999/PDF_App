"""Owned plugin field schemas and searchable explanations through PapyrusAPI."""
from copy import deepcopy
from core.help.models import HelpTopic


def register_type(namespace, artifact_type, metadata):
    owner = namespace._plugin_id
    qualified = artifact_type if artifact_type.startswith(owner + ".") else f"{owner}.{artifact_type}"
    if (":" in artifact_type or "." in artifact_type) and not artifact_type.startswith(owner + "."):
        raise ValueError("Artifact types must belong to the registering plugin namespace.")
    service = getattr(namespace._core, "artifact_service", None)
    if service is None:
        raise RuntimeError("Artifact type registry is not available.")
    definition = deepcopy(metadata or {})
    definition["plugin_id"] = owner
    topics = []
    for field in definition.get("review_fields") or []:
        topic_id = field.get("help_topic")
        if field.get("description"):
            topic_id = f"plugin.{owner}.artifact.{qualified}.{field['path']}"
            field["help_topic"] = topic_id
            topics.append(HelpTopic(topic_id, field["label"], field["description"].splitlines()[0],
                field["description"], "Intelligence", keywords=[qualified, field["path"]],
                namespace="plugin", plugin_id=owner))
        elif topic_id and not topic_id.startswith(f"plugin.{owner}."):
            raise ValueError("Custom field help must use the registering plugin's help namespace.")
    registry = getattr(getattr(namespace._core, "help_service", None), "help_registry", None)
    if topics and registry is None:
        raise RuntimeError("Help registry is required for artifact field explanations.")
    for topic in topics:
        existing = registry.get(topic.id)
        if existing and existing.plugin_id != owner:
            raise ValueError("Artifact field help topic belongs to another plugin.")
    service.register_artifact_type(qualified, definition)
    if registry is not None:
        for old in namespace._registered_type_topics.get(qualified, set()) - {t.id for t in topics}:
            registry.unregister(old)
        registry.register_many(topics)
    namespace._registered_types.add(qualified)
    namespace._registered_type_topics[qualified] = {topic.id for topic in topics}
    return qualified


def cleanup(namespace):
    service = getattr(namespace._core, "artifact_service", None)
    registry = getattr(getattr(namespace._core, "help_service", None), "help_registry", None)
    for identity in namespace._registered_types:
        if service:
            service.unregister_artifact_type(identity, plugin_id=namespace._plugin_id)
        if registry is not None:
            for topic in namespace._registered_type_topics.get(identity, set()):
                registry.unregister(topic)
    namespace._registered_types.clear()
    namespace._registered_type_topics.clear()
