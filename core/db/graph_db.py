# core/db/graph_db.py
import sqlite3
import json
from typing import List, Optional
from core.models.ontology_models import EntityModel, RelationModel
from core.db.base_db import BaseDB

class GraphDB(BaseDB):
    def upsert_entity(self, entity: EntityModel):
        if not self._conn: return
        try:
            self._conn.execute("""
                INSERT INTO entities (id, entity_type, origin_id, properties, state, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    entity_type=excluded.entity_type,
                    origin_id=excluded.origin_id,
                    properties=excluded.properties,
                    state=excluded.state,
                    updated_at=CURRENT_TIMESTAMP
            """, (
                entity.id, entity.entity_type, entity.origin_id,
                json.dumps(entity.properties), json.dumps(entity.state)
            ))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error upserting entity {entity.id}: {e}")

    def get_entity(self, entity_id: str) -> Optional[EntityModel]:
        if not self._conn: return None
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT id, entity_type, origin_id, properties, state FROM entities WHERE id = ?", (entity_id,))
            row = cursor.fetchone()
            if row:
                return EntityModel(
                    id=row[0],
                    entity_type=row[1],
                    origin_id=row[2],
                    properties=json.loads(row[3]) if row[3] else {},
                    state=json.loads(row[4]) if row[4] else {}
                )
            return None
        except sqlite3.Error as e:
            print(f"Error getting entity {entity_id}: {e}")
            return None

    def delete_entity(self, entity_id: str):
        if not self._conn: return
        try:
            # Foreign keys ON DELETE CASCADE will automatically wipe relations and view_entity_meta
            self._conn.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error deleting entity {entity_id}: {e}")

    def upsert_relation(self, relation: RelationModel):
        if not self._conn: return
        try:
            self._conn.execute("""
                INSERT INTO relations (id, relation_type, source_id, target_id, evidence_ids, properties, state)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    relation_type=excluded.relation_type,
                    source_id=excluded.source_id,
                    target_id=excluded.target_id,
                    evidence_ids=excluded.evidence_ids,
                    properties=excluded.properties,
                    state=excluded.state
            """, (
                relation.id, relation.relation_type, relation.source_id, relation.target_id,
                json.dumps(relation.evidence_ids), json.dumps(relation.properties), json.dumps(relation.state)
            ))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error upserting relation {relation.id}: {e}")

    def get_relations_for_entity(self, entity_id: str, as_source: bool = True, as_target: bool = True) -> List[RelationModel]:
        if not self._conn: return []
        relations = []
        try:
            cursor = self._conn.cursor()
            query = "SELECT id, relation_type, source_id, target_id, evidence_ids, properties, state FROM relations WHERE "
            conditions = []
            params = []
            if as_source:
                conditions.append("source_id = ?")
                params.append(entity_id)
            if as_target:
                conditions.append("target_id = ?")
                params.append(entity_id)
                
            query += " OR ".join(conditions)
            cursor.execute(query, tuple(params))
            
            for row in cursor.fetchall():
                relations.append(RelationModel(
                    id=row[0], relation_type=row[1], source_id=row[2], target_id=row[3],
                    evidence_ids=json.loads(row[4]) if row[4] else [],
                    properties=json.loads(row[5]) if row[5] else {},
                    state=json.loads(row[6]) if row[6] else {}
                ))
            return relations
        except sqlite3.Error as e:
            print(f"Error getting relations for {entity_id}: {e}")
            return []