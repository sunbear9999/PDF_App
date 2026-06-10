from __future__ import annotations

import uuid
from typing import Optional

from PySide6.QtCore import QObject

from core.events.event_bus import EventBus
from core.models.ontology_model import (
    EntityIntent,
    EntityModel,
    EntityPayload,
    RelationIntent,
    RelationModel,
    RelationPayload,
    ViewEntityMetaModel,
    ViewIntent,
    ViewModel,
    ViewPayload,
)
from core.ontology.registry import OntologyRegistry


class OntologyService(QObject):
    """Headless write service for the global knowledge graph."""

    def __init__(self, project_manager, event_bus: Optional[EventBus] = None, registry: Optional[OntologyRegistry] = None, parent=None):
        super().__init__(parent)
        self.pm = project_manager
        self.bus = event_bus or EventBus.get_instance()
        self.registry = registry or OntologyRegistry()
        self.bus.entity_action_requested.connect(self.handle_entity_intent)
        self.bus.relation_action_requested.connect(self.handle_relation_intent)
        self.bus.view_action_requested.connect(self.handle_view_intent)

    @property
    def graph(self):
        return getattr(self.pm, "db_graph", None)

    def handle_entity_intent(self, intent, payload):
        if isinstance(payload, dict):
            payload = EntityPayload(**payload)
        if intent == EntityIntent.ADD:
            self.add_entity(payload)
        elif intent == EntityIntent.UPDATE_PROPERTIES:
            self.update_entity_properties(payload.entity_id, payload.data)
        elif intent == EntityIntent.UPDATE_STATE:
            self.update_entity_state(payload.entity_id, payload.data)
        elif intent == EntityIntent.UPDATE_VIEW_META:
            self.update_view_meta(payload.entity_id, payload.view_id, payload.data)
        elif intent == EntityIntent.CHANGE_TYPE:
            self.change_entity_type(payload.entity_id, payload.entity_type)
        elif intent == EntityIntent.DELETE_FROM_VIEW:
            self.remove_entity_from_view(payload.entity_id, payload.view_id)
        elif intent == EntityIntent.PURGE_GLOBALLY:
            self.purge_entity(payload.entity_id)
        elif intent == EntityIntent.VERIFY:
            self.verify_entity(payload.entity_id)

    def handle_relation_intent(self, intent, payload):
        try:
            if isinstance(payload, dict):
                payload = RelationPayload(**payload)
            if intent == RelationIntent.ADD:
                self.add_relation(payload)
            elif intent == RelationIntent.UPDATE_PROPERTIES:
                self.update_relation_properties(payload.relation_id, payload.data)
            elif intent == RelationIntent.UPDATE_STATE:
                self.update_relation_state(payload.relation_id, payload.data)
            elif intent == RelationIntent.DELETE:
                self.delete_relation(payload.relation_id)
        except ValueError as e:
            print(f"Ontology relation intent rejected: {e}")

    def handle_view_intent(self, intent, payload):
        if isinstance(payload, dict):
            payload = ViewPayload(**payload)
        if intent == ViewIntent.ADD:
            self.add_view(payload)
        elif intent == ViewIntent.UPDATE:
            self.update_view(payload)
        elif intent == ViewIntent.DELETE:
            self.delete_view(payload.view_id)

    def add_entity(self, payload: EntityPayload) -> Optional[EntityModel]:
        if not self.graph:
            return None
        blueprint = self.registry.get_entity_blueprint(payload.entity_type or "entity.text")
        data = payload.data or {}
        entity = EntityModel(
            id=payload.entity_id or data.get("id") or str(uuid.uuid4()),
            entity_type=blueprint.type_key,
            origin_id=payload.origin_id or data.get("origin_id"),
            properties=blueprint.build_default_properties(data.get("properties", data)),
            state=blueprint.build_default_state(data.get("state")),
        )
        self.graph.upsert_entity(entity)
        if payload.view_id:
            self.update_view_meta(entity.id, payload.view_id, data.get("view_meta", {}))
        self._emit_entity_changed(EntityIntent.ADD, entity)
        return entity

    def update_entity_properties(self, entity_id: str, changes: dict) -> Optional[EntityModel]:
        entity = self._get_entity(entity_id)
        if not entity:
            return None
        entity.properties.update(changes or {})
        self.graph.upsert_entity(entity)
        self._emit_entity_changed(EntityIntent.UPDATE_PROPERTIES, entity)
        return entity

    def update_entity_state(self, entity_id: str, changes: dict) -> Optional[EntityModel]:
        entity = self._get_entity(entity_id)
        if not entity:
            return None
        entity.state.update(changes or {})
        self.graph.upsert_entity(entity)
        self._emit_entity_changed(EntityIntent.UPDATE_STATE, entity)
        return entity

    def verify_entity(self, entity_id: str) -> Optional[EntityModel]:
        return self.update_entity_state(entity_id, {"is_verified": True})

    def change_entity_type(self, entity_id: str, entity_type: str) -> Optional[EntityModel]:
        entity = self._get_entity(entity_id)
        if not entity or not entity_type:
            return None
        blueprint = self.registry.get_entity_blueprint(entity_type)
        entity.entity_type = blueprint.type_key
        entity.properties = blueprint.build_default_properties(entity.properties)
        entity.state = blueprint.build_default_state(entity.state)
        self.graph.upsert_entity(entity)
        self._emit_entity_changed(EntityIntent.CHANGE_TYPE, entity)
        return entity

    def update_view_meta(self, entity_id: str, view_id: str, changes: dict) -> Optional[ViewEntityMetaModel]:
        if not self.graph or not entity_id or not view_id:
            return None
        existing = self.graph.get_view_entity_meta(view_id, entity_id)
        data = changes or {}
        props = dict(existing.properties) if existing else {}
        props.update(data.get("properties", {}))
        meta = ViewEntityMetaModel(
            view_id=str(view_id),
            entity_id=entity_id,
            x=data.get("x", existing.x if existing else 0),
            y=data.get("y", existing.y if existing else 0),
            color=data.get("color", existing.color if existing else None),
            is_collapsed=data.get("is_collapsed", existing.is_collapsed if existing else False),
            properties=props,
        )
        self.graph.upsert_view_entity_meta(meta)
        self.bus.view_changed.emit(EntityIntent.UPDATE_VIEW_META, EntityPayload(entity_id=entity_id, view_id=str(view_id), data=meta.to_dict()))
        return meta

    def remove_entity_from_view(self, entity_id: str, view_id: str):
        if self.graph and entity_id and view_id:
            self.graph.remove_entity_from_view(view_id, entity_id)
            self._emit_entity_changed(EntityIntent.DELETE_FROM_VIEW, self.graph.get_entity(entity_id), view_id=view_id)

    def purge_entity(self, entity_id: str):
        if self.graph and entity_id:
            entity = self.graph.get_entity(entity_id)
            self.graph.delete_entity(entity_id)
            self._emit_entity_changed(EntityIntent.PURGE_GLOBALLY, entity)

    def add_relation(self, payload: RelationPayload) -> Optional[RelationModel]:
        if not self.graph:
            return None
        source = self.graph.get_entity(payload.source_id)
        target = self.graph.get_entity(payload.target_id)
        if not source or not target:
            raise ValueError("Relation endpoints must exist before a relation can be added.")
        relation_type = payload.relation_type or "relation.basic"
        if not self.registry.validate_relation(relation_type, source.entity_type, target.entity_type):
            raise ValueError(f"{relation_type} cannot connect {source.entity_type} to {target.entity_type}.")
        blueprint = self.registry.get_relation_blueprint(relation_type)
        data = payload.data or {}
        properties = blueprint.build_default_properties(data.get("properties", data))
        if payload.view_id:
            properties["view_ids"] = sorted({str(payload.view_id), *[str(v) for v in properties.get("view_ids", [])]})
        relation = RelationModel(
            id=payload.relation_id or data.get("id") or str(uuid.uuid4()),
            source_id=payload.source_id,
            target_id=payload.target_id,
            relation_type=blueprint.type_key,
            evidence_ids=data.get("evidence_ids", []),
            properties=properties,
            state=blueprint.build_default_state(data.get("state")),
        )
        self.graph.upsert_relation(relation)
        self._emit_relation_changed(RelationIntent.ADD, relation)
        return relation

    def update_relation_properties(self, relation_id: str, changes: dict) -> Optional[RelationModel]:
        relation = self._get_relation(relation_id)
        if not relation:
            return None
        relation.properties.update(changes or {})
        self.graph.upsert_relation(relation)
        self._emit_relation_changed(RelationIntent.UPDATE_PROPERTIES, relation)
        return relation

    def update_relation_state(self, relation_id: str, changes: dict) -> Optional[RelationModel]:
        relation = self._get_relation(relation_id)
        if not relation:
            return None
        relation.state.update(changes or {})
        self.graph.upsert_relation(relation)
        self._emit_relation_changed(RelationIntent.UPDATE_STATE, relation)
        return relation

    def delete_relation(self, relation_id: str):
        if self.graph and relation_id:
            relation = self.graph.get_relation(relation_id)
            self.graph.delete_relation(relation_id)
            self._emit_relation_changed(RelationIntent.DELETE, relation)

    def add_view(self, payload: ViewPayload) -> Optional[ViewModel]:
        if not self.graph:
            return None
        blueprint = self.registry.get_view_blueprint(payload.view_type or "view.graph")
        view = ViewModel(
            id=payload.view_id or str(uuid.uuid4()),
            name=payload.name or blueprint.display_name,
            view_type=blueprint.type_key,
            properties=payload.data or {},
        )
        self.graph.upsert_view(view)
        self.bus.view_changed.emit(ViewIntent.ADD, payload)
        return view

    def update_view(self, payload: ViewPayload) -> Optional[ViewModel]:
        if not self.graph or not payload.view_id:
            return None
        existing = self.graph.get_view(payload.view_id)
        if not existing:
            return None
        if payload.view_type:
            existing.view_type = self.registry.get_view_blueprint(payload.view_type).type_key
        if payload.name:
            existing.name = payload.name
        existing.properties.update(payload.data or {})
        self.graph.upsert_view(existing)
        self.bus.view_changed.emit(ViewIntent.UPDATE, payload)
        return existing

    def delete_view(self, view_id: str):
        if self.graph and view_id:
            self.graph.delete_view(view_id)
            self.bus.view_changed.emit(ViewIntent.DELETE, ViewPayload(view_id=view_id))

    def compute_entity_metrics(self, entity_id: str) -> dict:
        entity = self._get_entity(entity_id)
        if not entity:
            return {}
        relations = self.graph.get_relations_for_entity(entity_id)
        return self.registry.compute_metrics(entity, relations, self.graph.get_entity)

    def _get_entity(self, entity_id: str) -> Optional[EntityModel]:
        return self.graph.get_entity(entity_id) if self.graph and entity_id else None

    def _get_relation(self, relation_id: str) -> Optional[RelationModel]:
        return self.graph.get_relation(relation_id) if self.graph and relation_id else None

    def _emit_entity_changed(self, intent, entity: Optional[EntityModel], view_id: Optional[str] = None):
        if not entity:
            return
        payload = EntityPayload(entity_id=entity.id, entity_type=entity.entity_type, origin_id=entity.origin_id, view_id=view_id, data=entity.to_dict())
        self.bus.entity_changed.emit(intent, payload)
        if entity.state.get("is_verified") is False:
            self.bus.discovery_items_changed.emit(intent, payload)

    def _emit_relation_changed(self, intent, relation: Optional[RelationModel]):
        if not relation:
            return
        payload = RelationPayload(
            relation_id=relation.id,
            relation_type=relation.relation_type,
            source_id=relation.source_id,
            target_id=relation.target_id,
            data=relation.to_dict(),
        )
        self.bus.relation_changed.emit(intent, payload)
