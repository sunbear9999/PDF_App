# core/ontology/registry.py
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum

class RelationTrait(str, Enum):
    HIERARCHICAL = "trait.hierarchical" # e.g., Question -> Claim (Allows collapsing)
    CAUSAL = "trait.causal"             # e.g., Event -> Event (Timeline dependencies)
    SEMANTIC = "trait.semantic"         # e.g., Similar To (Clustering)
    EVIDENTIARY = "trait.evidentiary"   # e.g., Supports/Contradicts (Math/Confidence rolling)

@dataclass
class EntityBlueprint:
    type_key: str
    display_name: str
    description: str
    default_properties: Dict[str, Any] = field(default_factory=dict)
    default_state: Dict[str, Any] = field(default_factory=lambda: {"is_verified": True, "ai_generated": False})
    
@dataclass
class RelationBlueprint:
    type_key: str
    display_name: str
    traits: List[RelationTrait] = field(default_factory=list)
    valid_source_types: List[str] = field(default_factory=lambda: ["*"]) # "*" means any
    valid_target_types: List[str] = field(default_factory=lambda: ["*"])
    default_properties: Dict[str, Any] = field(default_factory=dict)

class OntologyRegistry:
    """Singleton registry for all Knowledge Graph types. Plugins will inject here."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(OntologyRegistry, cls).__new__(cls)
            cls._instance.entities: Dict[str, EntityBlueprint] = {}
            cls._instance.relations: Dict[str, RelationBlueprint] = {}
            cls._instance._register_core_ontology()
        return cls._instance

    def register_entity(self, blueprint: EntityBlueprint):
        self.entities[blueprint.type_key] = blueprint

    def register_relation(self, blueprint: RelationBlueprint):
        self.relations[blueprint.type_key] = blueprint

    def get_entity_blueprint(self, type_key: str) -> Optional[EntityBlueprint]:
        return self.entities.get(type_key, self.entities.get("entity.text"))

    def get_relation_blueprint(self, type_key: str) -> Optional[RelationBlueprint]:
        return self.relations.get(type_key, self.relations.get("relation.basic"))

    def _register_core_ontology(self):
        # --- Core Entities ---
        self.register_entity(EntityBlueprint(
            type_key="entity.claim",
            display_name="Claim",
            description="An assertion that requires evidence.",
            default_properties={"confidence": 0.0, "claim_type": "factual"}
        ))
        
        self.register_entity(EntityBlueprint(
            type_key="entity.evidence",
            display_name="Evidence",
            description="Data or quotes supporting/contradicting a claim.",
            default_properties={"strength": 1.0, "page_num": None}
        ))
        
        self.register_entity(EntityBlueprint(
            type_key="entity.question",
            display_name="Question",
            description="An open line of inquiry.",
            default_properties={"status": "open", "priority": "normal"}
        ))

        # --- Core Relations ---
        self.register_relation(RelationBlueprint(
            type_key="relation.supports",
            display_name="Supports",
            traits=[RelationTrait.EVIDENTIARY, RelationTrait.HIERARCHICAL],
            valid_source_types=["entity.evidence", "entity.claim", "entity.finding"],
            valid_target_types=["entity.claim", "entity.finding"],
            default_properties={"weight": 1.0}
        ))
        
        self.register_relation(RelationBlueprint(
            type_key="relation.answers",
            display_name="Answers",
            traits=[RelationTrait.HIERARCHICAL],
            valid_source_types=["entity.claim", "entity.finding"],
            valid_target_types=["entity.question"]
        ))