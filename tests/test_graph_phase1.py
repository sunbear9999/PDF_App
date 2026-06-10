import os
import sqlite3
import tempfile
import unittest

from core.events.event_bus import EventBus
from core.models.ontology_model import EntityModel, EntityType, RelationModel, RelationType
from core.models.workspace_models import EdgeModel, NodeModel, WorkspaceModel
from core.ontology.registry import OntologyRegistry, RelationTrait
from core.project_manager import ProjectManager
from core.services.graph_analysis_service import GraphAnalysisService
from core.services.ontology_service import OntologyService


class TestGraphPhase1(unittest.TestCase):
    def _new_project(self, path):
        pm = ProjectManager()
        pm.create_project(path)
        return pm

    def test_fresh_project_creates_graph_tables_and_default_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            pm = self._new_project(os.path.join(tmp, "fresh.pdfproj"))
            cursor = pm._conn.cursor()
            for table in ("entities", "relations", "views", "view_entity_meta"):
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (table,))
                self.assertEqual(cursor.fetchone()[0], table)
            cursor.execute("SELECT id, view_type, name FROM views WHERE id = '1'")
            self.assertEqual(cursor.fetchone(), ("1", "view.graph", "Main Board"))

    def test_legacy_workspace_migrates_to_graph_without_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "legacy.pdfproj")
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("CREATE TABLE pdfs (path TEXT PRIMARY KEY)")
            conn.execute("CREATE TABLE highlights (id TEXT PRIMARY KEY, doc_id TEXT, page_num INTEGER, rect_coords TEXT, text_content TEXT, note_content TEXT, color TEXT)")
            conn.execute("CREATE TABLE workspaces (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            conn.execute(
                """CREATE TABLE nodes (
                    id TEXT PRIMARY KEY, highlight_id TEXT, workspace_id INTEGER,
                    quote TEXT, note_text TEXT, color TEXT, is_custom INTEGER,
                    pdf_path TEXT, page_num INTEGER, manual_font_size INTEGER,
                    x REAL, y REAL, width REAL, height REAL,
                    node_origin TEXT, is_verified INTEGER, original_text TEXT,
                    embedding_vector TEXT, node_type_id TEXT
                )"""
            )
            conn.execute(
                """CREATE TABLE edges (
                    edge_id TEXT PRIMARY KEY, source_id TEXT, target_id TEXT,
                    label TEXT, color TEXT, weight INTEGER, workspace_id INTEGER
                )"""
            )
            conn.execute("INSERT INTO pdfs (path) VALUES ('/tmp/source.pdf')")
            conn.execute("INSERT INTO workspaces (id, name) VALUES (1, 'Main Board')")
            conn.execute(
                """INSERT INTO nodes VALUES (
                    'n1', 'h1', 1, 'quoted text', 'note text', '#112233', 0,
                    '/tmp/source.pdf', 2, NULL, 10, 20, 210, 90,
                    'ai', 0, 'note text', NULL, 'workspace.node.quote'
                )"""
            )
            conn.execute(
                """INSERT INTO nodes VALUES (
                    'n2', NULL, 1, '', 'claim text', '#445566', 1,
                    NULL, NULL, NULL, 40, 60, 220, 100,
                    'human', 1, 'claim text', NULL, 'workspace.node.text'
                )"""
            )
            conn.execute("INSERT INTO edges VALUES ('e1', 'n1', 'n2', 'supports maybe', '#999999', 3, 1)")
            conn.commit()
            conn.close()

            pm = ProjectManager()
            self.assertTrue(pm.load_project(db_path))
            workspace = pm.get_workspace_data(1)
            self.assertEqual({node.id for node in workspace.nodes}, {"n1", "n2"})
            self.assertEqual(len(workspace.edges), 1)
            self.assertEqual(workspace.edges[0].label, "supports maybe")

            cursor = pm._conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM entities WHERE id IN ('n1', 'n2')")
            self.assertEqual(cursor.fetchone()[0], 2)
            pm.db_schema.init_database()
            cursor = pm._conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM entities WHERE id IN ('n1', 'n2')")
            self.assertEqual(cursor.fetchone()[0], 2)

    def test_graphdb_round_trips_json_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            pm = self._new_project(os.path.join(tmp, "roundtrip.pdfproj"))
            entity = EntityModel(
                id="claim-1",
                entity_type=EntityType.CLAIM.value,
                properties={"confidence": 0.5, "tags": ["a"]},
                state={"is_verified": False, "ai_generated": True},
            )
            pm.db_graph.upsert_entity(entity)
            loaded = pm.db_graph.get_entity("claim-1")
            self.assertEqual(loaded.properties["tags"], ["a"])
            self.assertFalse(loaded.state["is_verified"])

            pm.db_graph.upsert_entity(EntityModel(id="evidence-1", entity_type=EntityType.EVIDENCE.value))
            relation = RelationModel(
                id="rel-1",
                source_id="evidence-1",
                target_id="claim-1",
                relation_type=RelationType.SUPPORTS.value,
                evidence_ids=["quote-1"],
                properties={"confidence": 0.75},
            )
            pm.db_graph.upsert_relation(relation)
            loaded_rel = pm.db_graph.get_relation("rel-1")
            self.assertEqual(loaded_rel.evidence_ids, ["quote-1"])
            self.assertEqual(loaded_rel.properties["confidence"], 0.75)
            self.assertEqual(pm.db_graph.get_relations_by_trait(RelationTrait.EVIDENTIARY)[0].id, "rel-1")

    def test_project_source_list_excludes_extracted_bibliography_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            pm = self._new_project(os.path.join(tmp, "sources.pdfproj"))
            pdf_path = os.path.join(tmp, "paper.pdf")
            pm.pdfs.append(pdf_path)
            pm.db_graph.ensure_source_entity(pdf_path)
            pm.db_graph.upsert_entity(EntityModel(
                id="source_cited",
                entity_type=EntityType.SOURCE.value,
                origin_id="10.1000/example",
                properties={
                    "title": "Cited Source",
                    "doi": "10.1000/example",
                    "extraction_kind": "bibliography_source",
                    "cited_by_source_id": "source_parent",
                },
                state={"is_verified": False, "origin": "deterministic_extractor"},
            ))

            sources = pm.list_source_entities()
            self.assertEqual([source.properties.get("path") or source.origin_id for source in sources], [pdf_path])

    def test_registry_relation_validation_and_claim_metrics(self):
        registry = OntologyRegistry()
        self.assertTrue(registry.validate_relation(RelationType.SUPPORTS.value, EntityType.EVIDENCE.value, EntityType.CLAIM.value))
        self.assertTrue(registry.validate_relation(RelationType.SUPPORTS.value, EntityType.QUOTE.value, EntityType.CLAIM.value))
        self.assertTrue(registry.validate_relation(RelationType.CONTRADICTS.value, EntityType.QUOTE.value, EntityType.CLAIM.value))
        self.assertTrue(registry.validate_relation(RelationType.SUPPORTS.value, EntityType.QUOTE.value, EntityType.REASONING.value))
        self.assertTrue(registry.validate_relation(RelationType.REASONS.value, EntityType.REASONING.value, EntityType.CLAIM.value))
        self.assertTrue(registry.validate_relation(RelationType.DERIVED_FROM.value, EntityType.FINDING.value, EntityType.QUOTE.value))
        self.assertTrue(registry.validate_relation(RelationType.REFERENCES.value, EntityType.CLAIM.value, EntityType.SOURCE.value))
        self.assertTrue(registry.validate_relation(RelationType.CRITIQUES.value, EntityType.COUNTERARGUMENT.value, EntityType.CLAIM.value))
        self.assertFalse(registry.validate_relation(RelationType.SUPPORTS.value, EntityType.SOURCE.value, EntityType.QUESTION.value))
        self.assertTrue(registry.validate_relation("relation.attributed_to", EntityType.SOURCE.value, EntityType.SOURCE.value))

        claim = EntityModel(id="c", entity_type=EntityType.CLAIM.value)
        evidence = EntityModel(id="ev", entity_type=EntityType.EVIDENCE.value, origin_id="source-1", properties={"strength": 0.8})
        relation = RelationModel(id="r", source_id="ev", target_id="c", relation_type=RelationType.SUPPORTS.value, properties={"confidence": 0.9})
        metrics = registry.compute_metrics(claim, [relation], lambda entity_id: evidence if entity_id == "ev" else None)
        self.assertEqual(metrics["supporting_evidence_count"], 1)
        self.assertEqual(metrics["unique_source_count"], 1)
        self.assertGreater(metrics["computed_confidence"], 0)

    def test_graph_analysis_service_computes_claim_metrics_and_children(self):
        workspace = WorkspaceModel(workspace_id=1)
        workspace.nodes.append(NodeModel(
            "q1", "quote one", "", "#111111", False, 0, 0, 150, 80,
            entity_type=EntityType.QUOTE.value,
            source_id="source-a",
            entity_properties={"quote": "quote one", "exact_text": "quote one", "source_id": "source-a"},
        ))
        workspace.nodes.append(NodeModel(
            "q2", "quote two", "", "#222222", False, 0, 0, 150, 80,
            entity_type=EntityType.QUOTE.value,
            source_id="source-b",
            entity_properties={"quote": "quote two", "exact_text": "quote two", "source_id": "source-b"},
        ))
        workspace.nodes.append(NodeModel(
            "c1", "", "claim", "#333333", True, 0, 0, 150, 80,
            entity_type=EntityType.CLAIM.value,
            entity_properties={"text": "claim"},
        ))
        workspace.edges.append(EdgeModel(
            "r1", "q1", "c1", "Supports", "#888888", 2,
            relation_type=RelationType.SUPPORTS.value,
            relation_properties={"confidence": 0.8, "strength": 0.9},
        ))
        workspace.edges.append(EdgeModel(
            "r2", "q2", "c1", "Contradicts", "#888888", 2,
            relation_type=RelationType.CONTRADICTS.value,
            relation_properties={"confidence": 0.25, "strength": 0.8},
        ))

        facts = GraphAnalysisService(OntologyRegistry()).analyze_workspace(workspace)
        claim_facts = facts["c1"]
        self.assertEqual(claim_facts.metrics["supporting_evidence_count"], 1)
        self.assertEqual(claim_facts.metrics["contradicting_evidence_count"], 1)
        self.assertEqual(claim_facts.metrics["unique_source_count"], 2)
        self.assertGreater(claim_facts.metrics["computed_confidence"], 0)
        self.assertEqual(set(claim_facts.child_ids), {"q1", "q2"})

    def test_claim_metrics_include_evidence_inherited_through_reasoning(self):
        workspace = WorkspaceModel(workspace_id=1)
        workspace.nodes.append(NodeModel(
            "q1", "quoted evidence", "", "#111111", False, 0, 0, 150, 80,
            entity_type=EntityType.QUOTE.value,
            source_id="source-a",
            entity_properties={"quote": "quoted evidence", "exact_text": "quoted evidence", "source_id": "source-a"},
        ))
        workspace.nodes.append(NodeModel(
            "r1", "", "reasoning bridge", "#222222", True, 0, 0, 150, 80,
            entity_type=EntityType.REASONING.value,
            entity_properties={"text": "reasoning bridge"},
        ))
        workspace.nodes.append(NodeModel(
            "c1", "", "claim", "#333333", True, 0, 0, 150, 80,
            entity_type=EntityType.CLAIM.value,
            entity_properties={"text": "claim"},
        ))
        workspace.edges.append(EdgeModel(
            "e1", "q1", "r1", "Supports", "#888888", 2,
            relation_type=RelationType.SUPPORTS.value,
            relation_properties={"confidence": 0.9, "strength": 1.0},
        ))
        workspace.edges.append(EdgeModel(
            "e2", "r1", "c1", "Reasons For", "#888888", 2,
            relation_type=RelationType.REASONS.value,
            relation_properties={"confidence": 0.8},
        ))

        facts = GraphAnalysisService(OntologyRegistry()).analyze_workspace(workspace)
        claim_facts = facts["c1"]
        self.assertEqual(claim_facts.metrics["supporting_evidence_count"], 1)
        self.assertEqual(claim_facts.metrics["unique_source_count"], 1)
        self.assertGreater(claim_facts.metrics["computed_confidence"], 0)

    def test_workspace_service_adapter_loads_graph_backed_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            pm = self._new_project(os.path.join(tmp, "adapter.pdfproj"))
            source = pm.db_graph.ensure_source_entity("/tmp/doc.pdf")
            workspace = WorkspaceModel(workspace_id=1)
            workspace.nodes.append(NodeModel("n1", "quote", "note", "#123456", False, 1, 2, 150, 80, pdf_path="/tmp/doc.pdf", source_id=source.id, entity_type=EntityType.QUOTE.value))
            workspace.nodes.append(NodeModel("n2", "", "plain", "#654321", True, 10, 20, 160, 90, entity_type=EntityType.SOURCE.value))
            workspace.edges.append(EdgeModel(
                "e1",
                "n1",
                "n2",
                "Attributed To",
                "#888888",
                2,
                relation_type="relation.attributed_to",
                relation_properties={"context": "workspace"},
            ))
            pm.sync_workspace(workspace)

            loaded = pm.get_workspace_data(1)
            self.assertEqual({node.id for node in loaded.nodes}, {"n1", "n2"})
            self.assertEqual(loaded.edges[0].source, "n1")
            self.assertEqual(loaded.edges[0].relation_type, "relation.attributed_to")
            self.assertEqual(loaded.edges[0].relation_properties["context"], "workspace")
            loaded_source_node = next(node for node in loaded.nodes if node.id == "n1")
            self.assertEqual(loaded_source_node.source_id, source.id)
            self.assertEqual(loaded_source_node.entity_type, EntityType.QUOTE.value)

    def test_add_pdf_creates_source_entity(self):
        with tempfile.TemporaryDirectory() as tmp:
            pm = self._new_project(os.path.join(tmp, "source.pdfproj"))
            pdf_path = os.path.join(tmp, "paper.pdf")
            open(pdf_path, "wb").close()
            self.assertTrue(pm.add_pdf(pdf_path))
            source = pm.get_source_entity_by_path(pdf_path)
            self.assertIsNotNone(source)
            self.assertEqual(source.entity_type, EntityType.SOURCE.value)
            self.assertEqual(pm.get_source_path(source.id), pdf_path)

    def test_existing_graph_tables_without_updated_at_are_upgraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "old_graph.pdfproj")
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("CREATE TABLE pdfs (path TEXT PRIMARY KEY)")
            conn.execute("CREATE TABLE highlights (id TEXT PRIMARY KEY, doc_id TEXT, page_num INTEGER, rect_coords TEXT, text_content TEXT, note_content TEXT, color TEXT)")
            conn.execute("CREATE TABLE workspaces (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            conn.execute(
                """CREATE TABLE nodes (
                    id TEXT PRIMARY KEY, highlight_id TEXT, workspace_id INTEGER,
                    quote TEXT, note_text TEXT, color TEXT, is_custom INTEGER,
                    pdf_path TEXT, page_num INTEGER, manual_font_size INTEGER,
                    x REAL, y REAL, width REAL, height REAL,
                    node_origin TEXT, is_verified INTEGER, original_text TEXT,
                    embedding_vector TEXT, node_type_id TEXT
                )"""
            )
            conn.execute("CREATE TABLE edges (edge_id TEXT PRIMARY KEY, source_id TEXT, target_id TEXT, label TEXT, color TEXT, weight INTEGER, workspace_id INTEGER)")
            conn.execute("CREATE TABLE entities (id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, origin_id TEXT, properties TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL DEFAULT '{}')")
            conn.execute("CREATE TABLE relations (id TEXT PRIMARY KEY, relation_type TEXT NOT NULL, source_id TEXT NOT NULL, target_id TEXT NOT NULL, evidence_ids TEXT NOT NULL DEFAULT '[]', properties TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL DEFAULT '{}')")
            conn.execute("CREATE TABLE views (id TEXT PRIMARY KEY, view_type TEXT NOT NULL DEFAULT 'view.graph', name TEXT NOT NULL, properties TEXT NOT NULL DEFAULT '{}')")
            conn.execute("CREATE TABLE view_entity_meta (view_id TEXT NOT NULL, entity_id TEXT NOT NULL, x REAL DEFAULT 0, y REAL DEFAULT 0, color TEXT, is_collapsed INTEGER DEFAULT 0, properties TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(view_id, entity_id))")
            conn.commit()
            conn.close()

            pm = ProjectManager()
            self.assertTrue(pm.load_project(db_path))
            workspace = WorkspaceModel(workspace_id=1)
            workspace.nodes.append(NodeModel("n1", "", "hello", "#123456", True, 1, 2, 150, 80))
            pm.sync_workspace(workspace)
            self.assertEqual(pm.get_workspace_data(1).nodes[0].note, "hello")

    def test_ontology_service_verify_updates_entity_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            pm = self._new_project(os.path.join(tmp, "verify.pdfproj"))
            pm.db_graph.upsert_entity(EntityModel(id="ai-1", entity_type=EntityType.TEXT.value, state={"is_verified": False, "ai_generated": True}))
            service = OntologyService(pm, EventBus.get_instance(), OntologyRegistry())
            service.verify_entity("ai-1")
            self.assertTrue(pm.db_graph.get_entity("ai-1").state["is_verified"])


if __name__ == "__main__":
    unittest.main()
