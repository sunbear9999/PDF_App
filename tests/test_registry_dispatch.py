"""
Tests for registry-driven step dispatch.

Verifies that:
- build_default_workflow_node_type_registry() returns a populated registry
- get_by_step_type() returns the correct WorkflowNodeType
- The step_cls field points to a class implementing execute()
- All built-in step types are registered
"""
import unittest
from unittest.mock import MagicMock
from core.engine.registries import build_default_workflow_node_type_registry
from core.engine.execution_result import ExecutionResult


EXPECTED_STEP_TYPES = {
    "LLM_QUERY",
    "RAG_SEARCH",
    "PYTHON_SCRIPT",
    "USER_INPUT",
    "DATABASE_WRITE",
    "GRAPH_VALIDATOR",
    "ANALYSIS_CONTRACT",
    "DOCUMENT_CHUNK",
    "ANALYSIS_COMPACT",
    "ANALYSIS_FINALIZE",
    "ANALYSIS_SEND_TO_WORKSPACE",
    "ONTOLOGY_UPSERT",
    "AWAIT_EVENT",
    "DISPATCH_EVENT",
    "LLM_SCHEMA_QUERY",
}


class TestWorkflowNodeTypeRegistry(unittest.TestCase):
    def setUp(self):
        self.registry = build_default_workflow_node_type_registry()

    def test_all_built_in_step_types_registered(self):
        for step_type in EXPECTED_STEP_TYPES:
            node_type = self.registry.get_by_step_type(step_type)
            self.assertIsNotNone(node_type, f"Step type '{step_type}' not in registry")

    def test_registered_step_cls_is_callable(self):
        for step_type in EXPECTED_STEP_TYPES:
            node_type = self.registry.get_by_step_type(step_type)
            if node_type and node_type.step_cls:
                self.assertTrue(callable(node_type.step_cls), f"{step_type}.step_cls not callable")

    def test_step_cls_has_execute_method(self):
        for step_type in EXPECTED_STEP_TYPES:
            node_type = self.registry.get_by_step_type(step_type)
            if node_type and node_type.step_cls:
                instance = node_type.step_cls()
                self.assertTrue(hasattr(instance, "execute"), f"{step_type} missing execute()")

    def test_unknown_step_type_returns_none(self):
        result = self.registry.get_by_step_type("NONEXISTENT_TYPE_XYZ")
        self.assertIsNone(result)

    def test_registry_is_not_empty(self):
        all_types = list(self.registry.all())
        self.assertGreater(len(all_types), 10)


if __name__ == "__main__":
    unittest.main()
