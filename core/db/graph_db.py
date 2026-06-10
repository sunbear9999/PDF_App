# core/db/graph_db.py
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Dict, Iterable, List, Optional

from core.db.base_db import BaseDB
from core.models.ontology_model import (
    EntityModel,
    EntityType,
    RelationModel,
    RelationType,
    ViewEntityMetaModel,
    ViewModel,
    ViewType,
)
from core.models.workspace_models import EdgeModel, NodeModel, WorkspaceModel


class GraphDB(BaseDB):
    def upsert_entity(self, entity: EntityModel, commit: bool = True):
        if not self._conn:
            return
        self._execute_with_optional_updated_at(
            "entities",
            """
            INSERT INTO entities (id, entity_type, origin_id, properties, state, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                entity_type=excluded.entity_type,
                origin_id=excluded.origin_id,
                properties=excluded.properties,
                state=excluded.state,
                updated_at=CURRENT_TIMESTAMP
            """,
            """
            INSERT INTO entities (id, entity_type, origin_id, properties, state)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                entity_type=excluded.entity_type,
                origin_id=excluded.origin_id,
                properties=excluded.properties,
                state=excluded.state
            """,
            (entity.id, entity.entity_type, entity.origin_id, self._dump(entity.properties), self._dump(entity.state)),
        )
        if commit:
            self._conn.commit()

    def get_entity(self, entity_id: str) -> Optional[EntityModel]:
        if not self._conn:
            return None
        cursor = self._conn.cursor()
        cursor.execute("SELECT id, entity_type, origin_id, properties, state FROM entities WHERE id = ?", (entity_id,))
        row = cursor.fetchone()
        return self._entity_from_row(row) if row else None

    def get_entities_by_type(self, entity_type: str) -> List[EntityModel]:
        if not self._conn:
            return []
        cursor = self._conn.cursor()
        cursor.execute("SELECT id, entity_type, origin_id, properties, state FROM entities WHERE entity_type = ?", (entity_type,))
        return [self._entity_from_row(row) for row in cursor.fetchall()]

    def ensure_source_entity(self, pdf_path: str, properties: Optional[Dict] = None, commit: bool = True) -> Optional[EntityModel]:
        if not pdf_path:
            return None
        existing = self.get_source_entity_by_path(pdf_path)
        props = dict(existing.properties) if existing else {}
        props.update({
            "path": pdf_path,
            "title": props.get("title") or self._basename(pdf_path),
        })
        if properties:
            props.update(properties)
        entity = EntityModel(
            id=existing.id if existing else self._source_entity_id(pdf_path),
            entity_type=EntityType.SOURCE.value,
            origin_id=pdf_path,
            properties=props,
            state=dict(existing.state) if existing else {"is_verified": True, "ai_generated": False, "origin": "human"},
        )
        self.upsert_entity(entity, commit=commit)
        return entity

    def get_source_entity_by_path(self, pdf_path: str) -> Optional[EntityModel]:
        if not self._conn or not pdf_path:
            return None
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT id, entity_type, origin_id, properties, state
            FROM entities
            WHERE entity_type = ? AND (origin_id = ? OR json_extract(properties, '$.path') = ?)
            LIMIT 1
            """,
            (EntityType.SOURCE.value, pdf_path, pdf_path),
        )
        row = cursor.fetchone()
        return self._entity_from_row(row) if row else None

    def get_source_entity(self, source_id: str) -> Optional[EntityModel]:
        entity = self.get_entity(source_id) if source_id else None
        return entity if entity and entity.entity_type == EntityType.SOURCE.value else None

    def get_source_path(self, source_id: str) -> Optional[str]:
        source = self.get_source_entity(source_id)
        if not source:
            return None
        return source.properties.get("path") or source.origin_id

    def list_source_entities(self, document_paths: Optional[Iterable[str]] = None) -> List[EntityModel]:
        sources = self.get_entities_by_type(EntityType.SOURCE.value)
        if document_paths is None:
            return sources
        allowed = {str(path) for path in document_paths if path}
        return [
            source for source in sources
            if (source.properties.get("path") or source.origin_id) in allowed
        ]

    def rename_source_entity(self, old_path: str, new_path: str, commit: bool = True) -> Optional[EntityModel]:
        source = self.get_source_entity_by_path(old_path)
        if not source:
            source = self.ensure_source_entity(new_path, commit=False)
        source.origin_id = new_path
        source.properties["path"] = new_path
        source.properties["title"] = self._basename(new_path)
        self.upsert_entity(source, commit=False)
        self._rewrite_source_path_references(old_path, new_path, source.id)
        if commit:
            self._conn.commit()
        return source

    def remove_source_entity(self, pdf_path: str, purge: bool = False, commit: bool = True):
        source = self.get_source_entity_by_path(pdf_path)
        if not source:
            return
        if purge:
            self.delete_entity(source.id, commit=commit)
            return
        source.state["is_removed"] = True
        source.properties["path"] = pdf_path
        self.upsert_entity(source, commit=commit)

    def get_unverified_entities(self, limit: Optional[int] = None, entity_type: Optional[str] = None, offset: int = 0) -> List[EntityModel]:
        if not self._conn:
            return []
        cursor = self._conn.cursor()
        params = []
        where = ["COALESCE(json_extract(state, '$.is_verified'), 1) = 0"]
        if entity_type and entity_type != "ALL":
            where.append("entity_type = ?")
            params.append(entity_type)
        sql = f"""
            SELECT id, entity_type, origin_id, properties, state
            FROM entities
            WHERE {' AND '.join(where)}
            ORDER BY updated_at DESC
        """
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset or 0)])
        cursor.execute(sql, tuple(params))
        return [self._entity_from_row(row) for row in cursor.fetchall()]

    def delete_entity(self, entity_id: str, commit: bool = True):
        if not self._conn:
            return
        self._conn.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
        if commit:
            self._conn.commit()

    def upsert_relation(self, relation: RelationModel, commit: bool = True):
        if not self._conn:
            return
        params = (
            relation.id,
            relation.relation_type,
            relation.source_id,
            relation.target_id,
            self._dump(relation.evidence_ids),
            self._dump(relation.properties),
            self._dump(relation.state),
        )
        self._execute_with_optional_updated_at(
            "relations",
            """
            INSERT INTO relations (id, relation_type, source_id, target_id, evidence_ids, properties, state, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                relation_type=excluded.relation_type,
                source_id=excluded.source_id,
                target_id=excluded.target_id,
                evidence_ids=excluded.evidence_ids,
                properties=excluded.properties,
                state=excluded.state,
                updated_at=CURRENT_TIMESTAMP
            """,
            """
            INSERT INTO relations (id, relation_type, source_id, target_id, evidence_ids, properties, state)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                relation_type=excluded.relation_type,
                source_id=excluded.source_id,
                target_id=excluded.target_id,
                evidence_ids=excluded.evidence_ids,
                properties=excluded.properties,
                state=excluded.state
            """,
            params,
        )
        if commit:
            self._conn.commit()

    def get_relation(self, relation_id: str) -> Optional[RelationModel]:
        if not self._conn:
            return None
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT id, relation_type, source_id, target_id, evidence_ids, properties, state FROM relations WHERE id = ?",
            (relation_id,),
        )
        row = cursor.fetchone()
        return self._relation_from_row(row) if row else None

    def delete_relation(self, relation_id: str, commit: bool = True):
        if not self._conn:
            return
        self._conn.execute("DELETE FROM relations WHERE id = ?", (relation_id,))
        if commit:
            self._conn.commit()

    def get_relations_for_entity(self, entity_id: str, as_source: bool = True, as_target: bool = True) -> List[RelationModel]:
        if not self._conn or not entity_id or not (as_source or as_target):
            return []
        conditions = []
        params = []
        if as_source:
            conditions.append("source_id = ?")
            params.append(entity_id)
        if as_target:
            conditions.append("target_id = ?")
            params.append(entity_id)
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT id, relation_type, source_id, target_id, evidence_ids, properties, state FROM relations WHERE " + " OR ".join(conditions),
            tuple(params),
        )
        return [self._relation_from_row(row) for row in cursor.fetchall()]

    def get_relations_by_type(self, relation_type: str) -> List[RelationModel]:
        if not self._conn:
            return []
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT id, relation_type, source_id, target_id, evidence_ids, properties, state FROM relations WHERE relation_type = ?",
            (relation_type,),
        )
        return [self._relation_from_row(row) for row in cursor.fetchall()]

    def get_relations_by_trait(self, trait, registry=None) -> List[RelationModel]:
        if not self._conn:
            return []
        if registry is None:
            from core.ontology.registry import OntologyRegistry
            registry = OntologyRegistry()
        trait_value = getattr(trait, "value", trait)
        allowed_types = {
            blueprint.type_key
            for blueprint in registry.all_relations()
            if trait_value in {getattr(item, "value", item) for item in blueprint.traits}
        }
        if not allowed_types:
            return []
        placeholders = ",".join("?" for _ in allowed_types)
        cursor = self._conn.cursor()
        cursor.execute(
            f"SELECT id, relation_type, source_id, target_id, evidence_ids, properties, state FROM relations WHERE relation_type IN ({placeholders})",
            tuple(allowed_types),
        )
        return [self._relation_from_row(row) for row in cursor.fetchall()]

    def upsert_view(self, view: ViewModel, commit: bool = True):
        if not self._conn:
            return
        self._execute_with_optional_updated_at(
            "views",
            """
            INSERT INTO views (id, view_type, name, properties, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                view_type=excluded.view_type,
                name=excluded.name,
                properties=excluded.properties,
                updated_at=CURRENT_TIMESTAMP
            """,
            """
            INSERT INTO views (id, view_type, name, properties)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                view_type=excluded.view_type,
                name=excluded.name,
                properties=excluded.properties
            """,
            (str(view.id), view.view_type, view.name, self._dump(view.properties)),
        )
        if commit:
            self._conn.commit()

    def get_view(self, view_id: str) -> Optional[ViewModel]:
        if not self._conn:
            return None
        cursor = self._conn.cursor()
        cursor.execute("SELECT id, view_type, name, properties FROM views WHERE id = ?", (str(view_id),))
        row = cursor.fetchone()
        return ViewModel(id=row[0], view_type=row[1], name=row[2], properties=self._load_dict(row[3])) if row else None

    def get_views(self) -> List[ViewModel]:
        if not self._conn:
            return [ViewModel(id="1", name="Main Board", view_type=ViewType.GRAPH.value)]
        cursor = self._conn.cursor()
        cursor.execute("SELECT id, view_type, name, properties FROM views ORDER BY CAST(id AS INTEGER), id")
        return [ViewModel(id=row[0], view_type=row[1], name=row[2], properties=self._load_dict(row[3])) for row in cursor.fetchall()]

    def delete_view(self, view_id: str, commit: bool = True):
        if not self._conn:
            return
        self._conn.execute("DELETE FROM views WHERE id = ?", (str(view_id),))
        self._remove_view_id_from_relations(str(view_id))
        if commit:
            self._conn.commit()

    def upsert_view_entity_meta(self, meta: ViewEntityMetaModel, commit: bool = True):
        if not self._conn:
            return
        params = (str(meta.view_id), meta.entity_id, meta.x, meta.y, meta.color, int(meta.is_collapsed), self._dump(meta.properties))
        self._execute_with_optional_updated_at(
            "view_entity_meta",
            """
            INSERT INTO view_entity_meta (view_id, entity_id, x, y, color, is_collapsed, properties, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(view_id, entity_id) DO UPDATE SET
                x=excluded.x,
                y=excluded.y,
                color=excluded.color,
                is_collapsed=excluded.is_collapsed,
                properties=excluded.properties,
                updated_at=CURRENT_TIMESTAMP
            """,
            """
            INSERT INTO view_entity_meta (view_id, entity_id, x, y, color, is_collapsed, properties)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(view_id, entity_id) DO UPDATE SET
                x=excluded.x,
                y=excluded.y,
                color=excluded.color,
                is_collapsed=excluded.is_collapsed,
                properties=excluded.properties
            """,
            params,
        )
        if commit:
            self._conn.commit()

    def get_view_entity_meta(self, view_id: str, entity_id: str) -> Optional[ViewEntityMetaModel]:
        if not self._conn:
            return None
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT view_id, entity_id, x, y, color, is_collapsed, properties FROM view_entity_meta WHERE view_id = ? AND entity_id = ?",
            (str(view_id), entity_id),
        )
        row = cursor.fetchone()
        return self._meta_from_row(row) if row else None

    def get_entities_for_view(self, view_id: str) -> List[EntityModel]:
        if not self._conn:
            return []
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT e.id, e.entity_type, e.origin_id, e.properties, e.state,
                   m.view_id, m.entity_id, m.x, m.y, m.color, m.is_collapsed, m.properties
            FROM view_entity_meta m
            JOIN entities e ON e.id = m.entity_id
            WHERE m.view_id = ?
            ORDER BY e.updated_at
            """,
            (str(view_id),),
        )
        entities = []
        for row in cursor.fetchall():
            entity = self._entity_from_row(row[:5])
            entity.view_meta = self._meta_from_row(row[5:]).to_dict()
            entities.append(entity)
        return entities

    def get_relations_for_view(self, view_id: str) -> List[RelationModel]:
        if not self._conn:
            return []
        view_id = str(view_id)
        entity_ids = {entity.id for entity in self.get_entities_for_view(view_id)}
        if not entity_ids:
            return []
        cursor = self._conn.cursor()
        cursor.execute("SELECT id, relation_type, source_id, target_id, evidence_ids, properties, state FROM relations")
        relations = []
        for row in cursor.fetchall():
            relation = self._relation_from_row(row)
            view_ids = [str(v) for v in relation.properties.get("view_ids", [])]
            if relation.source_id in entity_ids and relation.target_id in entity_ids and (not view_ids or view_id in view_ids):
                relations.append(relation)
        return relations

    def remove_entity_from_view(self, view_id: str, entity_id: str, commit: bool = True):
        if not self._conn:
            return
        self._conn.execute("DELETE FROM view_entity_meta WHERE view_id = ? AND entity_id = ?", (str(view_id), entity_id))
        if commit:
            self._conn.commit()

    def get_workspace_data(self, workspace_id=1) -> WorkspaceModel:
        view_id = str(workspace_id)
        workspace = WorkspaceModel(workspace_id=int(workspace_id))
        for entity in self.get_entities_for_view(view_id):
            workspace.nodes.append(self._node_from_entity(entity, int(workspace_id)))
        for relation in self.get_relations_for_view(view_id):
            workspace.edges.append(self._edge_from_relation(relation))
        return workspace

    def sync_workspace(self, workspace: WorkspaceModel):
        if not self._conn:
            return
        view_id = str(workspace.workspace_id)
        cursor = self._conn.cursor()
        cursor.execute("BEGIN TRANSACTION")
        try:
            self.upsert_view(ViewModel(id=view_id, name=self._view_name(view_id), view_type=ViewType.GRAPH.value), commit=False)
            incoming_node_ids = {node.id for node in workspace.nodes}
            for node in workspace.nodes:
                self.upsert_entity(self._entity_from_node(node), commit=False)
                self.upsert_view_entity_meta(self._meta_from_node(node, view_id), commit=False)
            if incoming_node_ids:
                placeholders = ",".join("?" for _ in incoming_node_ids)
                cursor.execute(
                    f"DELETE FROM view_entity_meta WHERE view_id = ? AND entity_id NOT IN ({placeholders})",
                    [view_id] + list(incoming_node_ids),
                )
            else:
                cursor.execute("DELETE FROM view_entity_meta WHERE view_id = ?", (view_id,))

            incoming_edge_ids = {edge.id for edge in workspace.edges}
            for edge in workspace.edges:
                self.upsert_relation(self._relation_from_edge(edge, view_id), commit=False)
            self._delete_view_relations_not_in(view_id, incoming_edge_ids)
            self._conn.commit()
        except sqlite3.Error:
            self._conn.rollback()
            raise

    def sync_workspace_delta(self, delta: WorkspaceModel):
        if not self._conn:
            return
        view_id = str(delta.workspace_id)
        cursor = self._conn.cursor()
        cursor.execute("BEGIN TRANSACTION")
        try:
            self.upsert_view(ViewModel(id=view_id, name=self._view_name(view_id), view_type=ViewType.GRAPH.value), commit=False)
            for node in delta.nodes:
                self.upsert_entity(self._entity_from_node(node), commit=False)
                self.upsert_view_entity_meta(self._meta_from_node(node, view_id), commit=False)
            for node_id in delta.deleted_node_ids:
                self.remove_entity_from_view(view_id, node_id, commit=False)
            for edge in delta.edges:
                self.upsert_relation(self._relation_from_edge(edge, view_id), commit=False)
            for edge_id in delta.deleted_edge_ids:
                self._remove_relation_from_view(edge_id, view_id)
            self._conn.commit()
        except sqlite3.Error:
            self._conn.rollback()
            raise

    def set_entity_verification(self, entity_id: str, is_verified: bool):
        entity = self.get_entity(entity_id)
        if not entity:
            return
        entity.state["is_verified"] = bool(is_verified)
        self.upsert_entity(entity)

    def _entity_from_node(self, node: NodeModel) -> EntityModel:
        has_source = bool(node.source_id or node.pdf_path or node.highlight_id or node.quote)
        entity_type = node.entity_type or (node.node_type_id if node.node_type_id.startswith("entity.") else (EntityType.QUOTE.value if has_source and not node.is_custom else EntityType.TEXT.value))
        properties = {
            "quote": node.quote or "",
            "exact_text": node.quote or "",
            "text": node.note or "",
            "note_text": node.note or "",
            "color": node.color,
            "is_custom": bool(node.is_custom),
            "pdf_path": node.pdf_path,
            "page_num": node.page_num,
            "highlight_id": node.highlight_id,
            "manual_font_size": node.manual_font_size,
            "original_text": node.original_text or node.note or "",
            "node_type_id": node.node_type_id or "",
        }
        properties.update(node.entity_properties or {})
        if entity_type in {EntityType.QUOTE.value, EntityType.EVIDENCE.value}:
            quote_text = properties.get("exact_text") or properties.get("quote") or node.quote or ""
            note_text = properties.get("note_text")
            if note_text is None:
                note_text = node.note or ""
            if quote_text and str(note_text or "").strip() == str(quote_text).strip():
                note_text = ""
            properties["quote"] = quote_text
            properties["exact_text"] = quote_text
            properties["text"] = quote_text
            properties["note_text"] = note_text
        source_id = node.source_id or properties.get("source_id")
        if node.pdf_path:
            source = self.ensure_source_entity(node.pdf_path, commit=False)
            source_id = source.id if source else source_id
            properties["pdf_path"] = node.pdf_path
        if source_id:
            properties["source_id"] = source_id
        return EntityModel(
            id=node.id,
            entity_type=entity_type,
            origin_id=node.highlight_id or node.pdf_path,
            properties=properties,
            state={
                "is_verified": bool(node.is_verified),
                "ai_generated": node.node_origin == "ai",
                "origin": node.node_origin or "human",
                **(node.entity_state or {}),
            },
        )

    def _meta_from_node(self, node: NodeModel, view_id: str) -> ViewEntityMetaModel:
        return ViewEntityMetaModel(
            view_id=view_id,
            entity_id=node.id,
            x=node.x or 0,
            y=node.y or 0,
            color=node.color,
            is_collapsed=False,
            properties={
                "width": node.width or 150,
                "height": node.height or 80,
                "manual_font_size": node.manual_font_size,
                "legacy_workspace_id": view_id,
            },
        )

    def _node_from_entity(self, entity: EntityModel, workspace_id: int) -> NodeModel:
        props = entity.properties or {}
        meta = entity.view_meta or {}
        meta_props = meta.get("properties", {}) if isinstance(meta, dict) else {}
        state = entity.state or {}
        node_type_id = props.get("node_type_id") or ("workspace.node.quote" if entity.entity_type in {EntityType.QUOTE.value, EntityType.EVIDENCE.value} else "workspace.node.text")
        source_id = props.get("source_id")
        pdf_path = props.get("pdf_path") or (self.get_source_path(source_id) if source_id else None)
        note_text = props.get("note_text")
        if note_text is None:
            note_text = "" if entity.entity_type in {EntityType.QUOTE.value, EntityType.EVIDENCE.value} else (props.get("text") or props.get("title") or "")
        return NodeModel(
            id=entity.id,
            highlight_id=props.get("highlight_id"),
            workspace_id=workspace_id,
            quote=props.get("quote") or props.get("exact_text") or "",
            note=note_text,
            color=meta.get("color") or props.get("color") or "#333333",
            is_custom=bool(props.get("is_custom", entity.entity_type == EntityType.TEXT.value)),
            pdf_path=pdf_path,
            page_num=props.get("page_num") if props.get("page_num") is not None else props.get("page"),
            manual_font_size=meta_props.get("manual_font_size") or props.get("manual_font_size"),
            x=meta.get("x", 0),
            y=meta.get("y", 0),
            width=meta_props.get("width", 150),
            height=meta_props.get("height", 80),
            node_origin=state.get("origin", "ai" if state.get("ai_generated") else "human"),
            is_verified=int(bool(state.get("is_verified", False))),
            original_text=props.get("original_text") or props.get("note_text") or props.get("text") or "",
            node_type_id=node_type_id,
            entity_type=entity.entity_type,
            source_id=source_id,
            entity_properties=dict(props),
            entity_state=dict(state),
        )

    def _relation_from_edge(self, edge: EdgeModel, view_id: str) -> RelationModel:
        existing = self.get_relation(edge.id)
        properties = dict(existing.properties) if existing else {}
        properties.update(edge.relation_properties or {})
        view_ids = {str(v) for v in properties.get("view_ids", [])}
        view_ids.add(str(view_id))
        properties.update({
            "label": edge.label or "",
            "color": edge.color or "#888888",
            "weight": int(edge.weight or 2),
            "view_ids": sorted(view_ids),
            "legacy_workspace_id": str(view_id),
        })
        return RelationModel(
            id=edge.id,
            source_id=edge.source,
            target_id=edge.target,
            relation_type=edge.relation_type or (existing.relation_type if existing else RelationType.BASIC.value),
            evidence_ids=list(edge.evidence_ids or (existing.evidence_ids if existing else [])),
            properties=properties,
            state=dict(edge.relation_state or (existing.state if existing else {"is_verified": True, "origin": "workspace"})),
        )

    def _edge_from_relation(self, relation: RelationModel) -> EdgeModel:
        props = relation.properties or {}
        return EdgeModel(
            id=relation.id,
            source=relation.source_id,
            target=relation.target_id,
            label=props.get("label") or "",
            color=props.get("color") or "#888888",
            weight=int(props.get("weight") or 2),
            relation_type=relation.relation_type,
            evidence_ids=list(relation.evidence_ids or []),
            relation_properties=dict(props),
            relation_state=dict(relation.state or {}),
        )

    def _delete_view_relations_not_in(self, view_id: str, incoming_edge_ids: Iterable[str]):
        incoming = set(incoming_edge_ids)
        cursor = self._conn.cursor()
        cursor.execute("SELECT id, relation_type, source_id, target_id, evidence_ids, properties, state FROM relations")
        for row in cursor.fetchall():
            relation = self._relation_from_row(row)
            view_ids = {str(v) for v in relation.properties.get("view_ids", [])}
            if view_id not in view_ids or relation.id in incoming:
                continue
            self._remove_relation_from_view(relation.id, view_id)

    def _remove_view_id_from_relations(self, view_id: str):
        cursor = self._conn.cursor()
        cursor.execute("SELECT id, properties FROM relations")
        for relation_id, raw_props in cursor.fetchall():
            props = self._load_dict(raw_props)
            view_ids = [str(v) for v in props.get("view_ids", []) if str(v) != view_id]
            if view_ids:
                props["view_ids"] = view_ids
                cursor.execute("UPDATE relations SET properties = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (self._dump(props), relation_id))
            else:
                cursor.execute("DELETE FROM relations WHERE id = ?", (relation_id,))

    def _remove_relation_from_view(self, relation_id: str, view_id: str):
        relation = self.get_relation(relation_id)
        if not relation:
            return
        view_ids = [str(v) for v in relation.properties.get("view_ids", []) if str(v) != str(view_id)]
        if view_ids:
            relation.properties["view_ids"] = view_ids
            self.upsert_relation(relation, commit=False)
        else:
            self.delete_relation(relation_id, commit=False)

    def _view_name(self, view_id: str) -> str:
        view = self.get_view(view_id)
        return view.name if view else ("Main Board" if str(view_id) == "1" else f"Board {view_id}")

    def _source_entity_id(self, pdf_path: str) -> str:
        return f"source:{uuid.uuid5(uuid.NAMESPACE_URL, pdf_path or '')}"

    def _basename(self, path: str) -> str:
        return path.rstrip("/").split("/")[-1] if path else ""

    def _rewrite_source_path_references(self, old_path: str, new_path: str, source_id: str):
        cursor = self._conn.cursor()
        cursor.execute("SELECT id, entity_type, origin_id, properties, state FROM entities")
        for row in cursor.fetchall():
            entity = self._entity_from_row(row)
            changed = False
            if entity.origin_id == old_path and entity.entity_type != EntityType.SOURCE.value:
                entity.origin_id = new_path
                changed = True
            if entity.properties.get("pdf_path") == old_path:
                entity.properties["pdf_path"] = new_path
                changed = True
            if entity.properties.get("path") == old_path and entity.entity_type == EntityType.SOURCE.value:
                entity.properties["path"] = new_path
                changed = True
            if entity.properties.get("source_id") and entity.properties.get("pdf_path") == new_path:
                entity.properties["source_id"] = source_id
                changed = True
            if changed:
                self.upsert_entity(entity, commit=False)

    def _entity_from_row(self, row) -> EntityModel:
        return EntityModel(
            id=row[0],
            entity_type=row[1],
            origin_id=row[2],
            properties=self._load_dict(row[3]),
            state=self._load_dict(row[4]),
        )

    def _relation_from_row(self, row) -> RelationModel:
        return RelationModel(
            id=row[0],
            relation_type=row[1],
            source_id=row[2],
            target_id=row[3],
            evidence_ids=self._load_list(row[4]),
            properties=self._load_dict(row[5]),
            state=self._load_dict(row[6]),
        )

    def _meta_from_row(self, row) -> ViewEntityMetaModel:
        return ViewEntityMetaModel(
            view_id=row[0],
            entity_id=row[1],
            x=row[2] or 0,
            y=row[3] or 0,
            color=row[4],
            is_collapsed=bool(row[5]),
            properties=self._load_dict(row[6]),
        )

    def _dump(self, value) -> str:
        return json.dumps(value if value is not None else {})

    def _execute_with_optional_updated_at(self, table_name: str, sql_with_timestamp: str, sql_without_timestamp: str, params):
        try:
            self._conn.execute(sql_with_timestamp, params)
        except sqlite3.OperationalError as e:
            if "updated_at" not in str(e):
                raise
            self._conn.execute(sql_without_timestamp, params)

    def _load_dict(self, value) -> Dict:
        if not value:
            return {}
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}

    def _load_list(self, value) -> List:
        if not value:
            return []
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (TypeError, json.JSONDecodeError):
            return []
