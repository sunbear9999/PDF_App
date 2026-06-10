import unittest

from core.models.ontology_model import EntityIntent, EntityType, RelationIntent, RelationType
from core.services.document_ingestion_service import DocumentIngestionTask


class _Signal:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class _Bus:
    def __init__(self):
        self.status_message_requested = _Signal()
        self.entity_action_requested = _Signal()
        self.relation_action_requested = _Signal()


class TestDocumentIngestionService(unittest.TestCase):
    def _task(self):
        bus = _Bus()
        return DocumentIngestionTask("/tmp/current.pdf", "source_current", None, bus), bus

    def test_bibliography_entries_create_stable_cited_sources_and_reference_relations(self):
        task, bus = self._task()
        bibliography = task._extract_bibliography_entries(
            """
            [1] Smith, J. (2020). Big Finding. Journal of Tests. doi:10.1000/example
            [2] Jones, A. (2021). Other Finding. Research Press.
            """
        )

        task._emit_bibliography_graph(bibliography)

        source_events = [
            payload for intent, payload in bus.entity_action_requested.calls
            if intent == EntityIntent.ADD and payload.entity_type == EntityType.SOURCE.value
        ]
        reference_events = [
            payload for intent, payload in bus.relation_action_requested.calls
            if intent == RelationIntent.ADD and payload.relation_type == RelationType.REFERENCES.value
        ]

        self.assertEqual(len(source_events), 2)
        self.assertTrue(all(payload.origin_id != "/tmp/current.pdf" for payload in source_events))
        self.assertTrue(all(not payload.data["properties"].get("path") for payload in source_events))
        self.assertEqual(len(reference_events), 2)
        self.assertEqual({payload.source_id for payload in reference_events}, {"source_current"})
        author_events = [
            payload for intent, payload in bus.entity_action_requested.calls
            if intent == EntityIntent.ADD
            and payload.entity_type == EntityType.PERSON_ORG.value
            and payload.data["properties"].get("extraction_kind") == "citation_author"
        ]
        authored_by_events = [
            payload for intent, payload in bus.relation_action_requested.calls
            if intent == RelationIntent.ADD and payload.relation_type == RelationType.AUTHORED_BY.value
        ]
        self.assertGreaterEqual(len(author_events), 2)
        self.assertGreaterEqual(len(authored_by_events), 2)
        self.assertEqual(
            task._parse_bibliography_entry("Smith, J. (2020). Big Finding. doi:10.1000/example").source_id,
            task._parse_bibliography_entry("Smith, John. (2020). Big Finding. DOI:10.1000/example").source_id,
        )

    def test_in_text_citations_link_to_existing_bibliography_sources(self):
        task, bus = self._task()
        bibliography = task._extract_bibliography_entries(
            """
            [1] Smith, J. (2020). Big Finding. Journal of Tests.
            Jones, A. (2021). Other Finding. Research Press.
            """
        )
        text = "The result was replicated [1]. Jones (2021) later challenged the interpretation."

        spans = task._extract_in_text_citations(text, bibliography)

        quote_events = [
            payload for intent, payload in bus.entity_action_requested.calls
            if intent == EntityIntent.ADD and payload.entity_type == EntityType.QUOTE.value
        ]
        relation_types = [
            payload.relation_type for intent, payload in bus.relation_action_requested.calls
            if intent == RelationIntent.ADD
        ]

        self.assertEqual(len(spans), 2)
        self.assertEqual(len(quote_events), 2)
        self.assertIn("relation.attributed_to", relation_types)
        self.assertIn(RelationType.REFERENCES.value, relation_types)

    def test_citation_and_bibliography_regions_are_masked_before_dates_and_people(self):
        task, bus = self._task()
        text = (
            "In 1999, Ada Lovelace described the process. "
            "(Smith, 2020) should not become a timeline event or person. "
            "References\nSmith, J. (2020). Citation Name. Journal."
        )
        bibliography_spans = task._bibliography_spans(text)
        bibliography = task._extract_bibliography_entries(task._slice_spans(text, bibliography_spans))
        citation_spans = task._extract_in_text_citations(text, bibliography)
        masked = task._mask_spans(text, bibliography_spans + [span for span, _entry in citation_spans])

        task._extract_dates(masked, text)
        task._extract_people(masked, text)

        event_props = [
            payload.data["properties"] for intent, payload in bus.entity_action_requested.calls
            if intent == EntityIntent.ADD
        ]
        timeline_dates = [props.get("date") for props in event_props if props.get("extraction_kind") == "date_context"]
        people = [props.get("title") for props in event_props if props.get("extraction_kind") == "person_org_mention"]

        self.assertIn("1999", timeline_dates)
        self.assertNotIn("2020", timeline_dates)
        self.assertIn("Ada Lovelace", people)
        self.assertNotIn("Citation Name", people)


if __name__ == "__main__":
    unittest.main()
