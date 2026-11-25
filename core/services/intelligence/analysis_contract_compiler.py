"""One live-registry compiler for built-in, saved, plugin and runtime graph contracts."""
from copy import deepcopy
import json

from core.models.analysis_contract import normalize_graph_contract
from core.models.analysis_roles import analysis_role, analysis_entity_type, analysis_workspace_type
from core.models.analysis_relations import canonical_relation
from core.models.intelligence_models import GraphQualityContract
from core.ontology.registry import OntologyRegistry
from core.registries.workspace_registry import build_default_workspace_node_type_registry


class AnalysisContractCompiler:
    VERSION = 'analysis-contract-v1'

    def __init__(self, ontology_registry=None, workspace_registry=None):
        self.ontology = ontology_registry or OntologyRegistry()
        self.workspace = workspace_registry or build_default_workspace_node_type_registry()

    def compile(self, value=None, *, template=None, density=None):
        if value is not None and not isinstance(value, dict):
            raise ValueError('Graph contract must be an object.')
        raw = deepcopy(value or {})
        template = self.template(template)
        nested = template.get('graph_contract') or {}
        if not isinstance(nested, dict):
            raise ValueError('Template graph_contract must be an object.')
        template = {**template, **nested}
        # Compiled maps are derived output, never caller-authorized type declarations.
        previous_mapping = raw.pop('node_type_map', {})
        if not isinstance(previous_mapping, dict):
            raise ValueError('Graph node_type_map must be an object.')
        for key in ('workspace_type_map', 'compiler_version'):
            raw.pop(key, None)
        explicit_nodes = bool(template.get('node_types'))
        for key in ('node_types', 'relation_types', 'root_roles', 'required_roles'):
            if key in template and (template[key] or key in {'root_roles', 'required_roles'}):
                raw[key] = deepcopy(template[key])
        if template.get('instructions'):
            raw.update(purpose=template['instructions'], custom_instructions=template['instructions'])
        if template.get('graph_contract'):
            if not isinstance(template['graph_contract'], dict):
                raise ValueError('Template graph_contract must be an object.')
            raw.update(deepcopy(template['graph_contract']))
        self._limits(raw, template.get('limits'))
        self._density(raw, density)
        nodes, mapping = self._nodes(raw.get('node_types', GraphQualityContract().node_types),
                                    {} if explicit_nodes else previous_mapping)
        raw['node_types'] = nodes
        self._roles(raw, nodes, template=template if explicit_nodes else {})
        contract = normalize_graph_contract(raw)
        contract['relation_types'] = self._relations(contract['relation_types'], mapping)
        contract['node_type_map'] = mapping
        contract['workspace_type_map'] = self._workspace(mapping)
        contract['compiler_version'] = self.VERSION
        strategy = contract.get('projection_strategy') or (
            'focused_claim' if 'tracked_claim' in contract['root_roles'] else 'argument_units')
        if strategy not in {'argument_units', 'focused_claim'}:
            raise ValueError(f"Unsupported analysis projection strategy '{strategy}'.")
        contract['projection_strategy'] = strategy
        return contract

    def _nodes(self, values, previous_mapping):
        if not isinstance(values, list) or not values:
            raise ValueError('Choose at least one graph node type.')
        result, mapping = [], {}
        for value in values:
            if not isinstance(value, str) or not value.strip():
                raise ValueError('Node types must be non-empty registered names.')
            role = analysis_role(value)
            entity = value if value.startswith('entity.') else analysis_entity_type(role)
            previous = previous_mapping.get(role)
            if previous:
                if analysis_role(previous) != analysis_role(entity):
                    raise ValueError(f"Node mapping for '{role}' changes its semantic role.")
                entity = previous
            if entity not in self.ontology.entities:
                raise ValueError(f"Unknown graph node type '{value}'. Register it in OntologyRegistry first.")
            if role not in result:
                result.append(role)
            previous = mapping.get(role)
            if previous and previous != entity and {previous, entity} != {'entity.quote', 'entity.evidence'}:
                raise ValueError(f"Ambiguous entity mapping for analysis role '{role}'.")
            mapping[role] = 'entity.evidence' if previous == 'entity.evidence' else entity
        return result, mapping

    def _relations(self, values, mapping):
        result = []
        for value in values:
            role = canonical_relation(value)
            key = f'relation.{role}'
            if key not in self.ontology.relations:
                raise ValueError(f"Unknown graph relation '{value}'. Register it in OntologyRegistry first.")
            if not any(self.ontology.validate_relation(key, source, target)
                       for source in mapping.values() for target in mapping.values()):
                raise ValueError(f"Relation '{value}' has no compatible endpoints among the selected node types.")
            if role not in result:
                result.append(role)
        return result

    def _workspace(self, mapping):
        result = {role: analysis_workspace_type(entity) for role, entity in mapping.items()}
        for role, key in result.items():
            if not self.workspace.get(key):
                raise ValueError(f"Node role '{role}' needs registered workspace renderer '{key}'.")
        return result

    @staticmethod
    def _roles(raw, nodes, *, template):
        defaults = GraphQualityContract.for_density(raw.get('density', 'standard'))
        for key in ('root_roles', 'required_roles'):
            values = raw.get(key, getattr(defaults, key))
            if not isinstance(values, list):
                raise ValueError(f"Graph contract '{key}' must be a list.")
            roles = list(dict.fromkeys(analysis_role(value) for value in values))
            if key not in raw or (template and key not in template):
                roles = [role for role in roles if role in nodes]
            missing = set(roles) - set(nodes)
            if missing:
                raise ValueError(f"Graph '{key}' contains unselected roles: {', '.join(sorted(missing))}.")
            raw[key] = roles

    @staticmethod
    def _density(raw, density):
        if not density:
            return
        if not isinstance(density, str) or density not in {'focused', 'standard', 'exhaustive'}:
            raise ValueError('Graph density must be focused, standard or exhaustive.')
        profile = GraphQualityContract.for_density(density).to_dict()
        for key in ('density', 'min_branches', 'min_sections', 'min_proposition_nodes',
                    'max_proposition_nodes', 'min_evidence_nodes', 'max_evidence_nodes', 'max_nodes', 'max_relations'):
            raw[key] = profile[key]

    @staticmethod
    def template(value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError as exc:
                raise ValueError('Analysis template must be valid JSON.') from exc
        if value is not None and not isinstance(value, dict):
            raise ValueError('Analysis template must be an object.')
        return value or {}

    @staticmethod
    def _limits(raw, limits):
        if limits is None:
            return
        if not isinstance(limits, dict):
            raise ValueError('Template quote limits must be an object.')
        aliases = {'max_quotes_per_chunk': ('quotes_per_section', 1, 4),
                   'max_sections': ('max_sections', 1, 12),
                   'max_analysis_quotes': ('max_quotes', 1, 28)}
        selection = dict(raw.get('evidence_selection') or {})
        for old, (key, lower, upper) in aliases.items():
            if old in limits:
                try:
                    selection[key] = max(lower, min(upper, int(limits[old])))
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"Quote limit '{old}' must be an integer.") from exc
        if selection:
            raw['evidence_selection'] = selection
