import unittest
import sys
import types


if "PySide6" not in sys.modules:
    pyside = types.ModuleType("PySide6")
    qtcore = types.ModuleType("PySide6.QtCore")

    class QObject:
        def __init__(self, *args, **kwargs):
            pass

    class _BoundSignal:
        def __init__(self):
            self._callbacks = []

        def connect(self, callback):
            self._callbacks.append(callback)

        def emit(self, *args, **kwargs):
            for callback in list(self._callbacks):
                callback(*args, **kwargs)

    class Signal:
        def __init__(self, *args, **kwargs):
            self._name = None

        def __set_name__(self, owner, name):
            self._name = name

        def __get__(self, instance, owner):
            if instance is None:
                return self
            bound = instance.__dict__.get(self._name)
            if bound is None:
                bound = _BoundSignal()
                instance.__dict__[self._name] = bound
            return bound

    qtcore.QObject = QObject
    qtcore.Signal = Signal
    pyside.QtCore = qtcore
    sys.modules["PySide6"] = pyside
    sys.modules["PySide6.QtCore"] = qtcore

if "core.utils.doc_parser" not in sys.modules:
    doc_parser = types.ModuleType("core.utils.doc_parser")

    class DocumentParser:
        @staticmethod
        def chunk_document_for_analysis(*args, **kwargs):
            return []

    doc_parser.DocumentParser = DocumentParser
    sys.modules["core.utils.doc_parser"] = doc_parser

from core.events.event_bus import EventBus
from core.models.ontology_model import EntityType, RelationType
from core.ontology.registry import OntologyRegistry
from core.services.analysis_app_service import AnalysisAppService


class TestAnalysisAppService(unittest.TestCase):
    def _service(self):
        return AnalysisAppService(
            project_manager=None,
            prompt_manager=None,
            registry=OntologyRegistry(),
            event_bus=EventBus.get_instance(),
        )

    def test_compact_aliases_normalize_to_connected_argument_graph(self):
        service = self._service()
        template = {
            "node_types": [EntityType.CLAIM.value, EntityType.REASONING.value, EntityType.QUOTE.value],
            "relation_types": [RelationType.SUPPORTS.value, RelationType.REASONS.value],
            "allow_text_nodes": False,
        }
        contract = service._build_contract(template)
        raw = {
            "summary": "Argument chunk",
            "entities": [
                {"temp_id": "c1", "type": "claim", "text": "The intervention improved outcomes."},
                {"temp_id": "r1", "type": "reasoning", "text": "The measured improvement is treated as causal evidence."},
                {"temp_id": "q1", "type": "quote", "text": "outcomes improved by 20%", "exact_text": "outcomes improved by 20%", "page": 3},
            ],
            "relations": [],
        }

        normalized = service._normalize_graph_object(raw, "chunk0", contract)
        types = {entity["type"] for entity in normalized["entities"]}
        self.assertEqual(types, {EntityType.CLAIM.value, EntityType.REASONING.value, EntityType.QUOTE.value})
        quote = next(entity for entity in normalized["entities"] if entity["type"] == EntityType.QUOTE.value)
        self.assertEqual(quote["exact_text"], "outcomes improved by 20%")
        relation_types = {(rel["source"], rel["target"], rel["type"]) for rel in normalized["relations"]}
        self.assertIn(("chunk0_q1", "chunk0_r1", RelationType.SUPPORTS.value), relation_types)
        self.assertIn(("chunk0_r1", "chunk0_c1", RelationType.REASONS.value), relation_types)

    def test_tuple_array_output_still_normalizes(self):
        service = self._service()
        template = {
            "node_types": [EntityType.CLAIM.value, EntityType.REASONING.value, EntityType.QUOTE.value],
            "relation_types": [RelationType.SUPPORTS.value, RelationType.REASONS.value],
            "allow_text_nodes": False,
        }
        contract = service._build_contract(template)
        raw = {
            "nodes": [
                ["c1", "claim", "A claim", {"confidence": 0.8}],
                ["r1", "reasoning", "A warrant", {"reasoning_role": "warrant"}],
                ["q1", "quote", "verbatim evidence", {"page": 5}],
            ],
            "edges": [
                ["e1", "supports", "q1", "r1", {"confidence": 0.7}],
                ["e2", "reasons", "r1", "c1", {"evidence_ids": ["q1"]}],
            ],
        }

        normalized = service._normalize_graph_object(raw, "chunk1", contract)
        self.assertEqual(len(normalized["entities"]), 3)
        self.assertEqual(len(normalized["relations"]), 2)
        quote = next(entity for entity in normalized["entities"] if entity["type"] == EntityType.QUOTE.value)
        self.assertEqual(quote["exact_text"], "verbatim evidence")


if __name__ == "__main__":
    unittest.main()
