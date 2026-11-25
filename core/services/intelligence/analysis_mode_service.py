"""
AnalysisModeService — the runnable seam for analysis modes.

Turns an `AnalysisModeSpec` (from `AnalysisModeRegistry`) into a running
blueprint. It resolves the spec's `blueprint_id` in the `BlueprintRegistry`,
checks the `intelligence_analysis_runs` cache, and dispatches
`WorkflowIntent.RUN_BLUEPRINT` through the EventBus so execution goes through
`WorkflowRunnerService` + `MasterActionRunner` (rules §1.1 — never a direct LLM
call). Modes stay discoverable from the registry and plugin-addable.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from copy import deepcopy
import hashlib
import json
from typing import Any, Dict, List, Optional

from core.events.domains.analysis_events import AnalysisEvent, AnalysisPayload
from core.events.domains.workflow_events import WorkflowIntent, WorkflowPayload
from core.services.intelligence.analysis_contract_compiler import AnalysisContractCompiler
from core.services.intelligence.analysis_prerequisite_service import AnalysisPrerequisiteService, analysis_source_ids
from core.models.analysis_requirements import describe_missing


class AnalysisModeService:
    # Invalidate pre-Phase-8 graphs whose topology implied unproven semantics.
    EXECUTION_CONTRACT_VERSION = "universal-analysis-graph-v12"
    cancellable_operations = frozenset({'run'})

    def __init__(
        self, mode_registry=None, blueprint_registry=None,
        project_manager=None, bus=None, artifact_service=None,
        prompt_manager=None, source_intelligence_service=None,
    ):
        self.mode_registry = mode_registry
        self.blueprint_registry = blueprint_registry
        self.project_manager = project_manager
        self.bus = bus
        self.artifact_service = artifact_service
        self.prompt_manager = prompt_manager
        self.compiler = getattr(mode_registry, 'compiler', None) or AnalysisContractCompiler()
        self.prerequisites = AnalysisPrerequisiteService(project_manager, source_intelligence_service)

    @property
    def db(self):
        return getattr(self.project_manager, 'db_intelligence', None)

    def list_modes(self, mount_point: str = "") -> List[Dict[str, Any]]:
        from dataclasses import asdict
        if not self.mode_registry:
            return []
        modes = self.mode_registry.for_mount(mount_point) if mount_point else self.mode_registry.all()
        return [asdict(m) for m in modes]

    def get_cached(self, mode_id: str, params: Dict[str, Any]) -> Optional[Any]:
        db = getattr(self.project_manager, "db_intelligence", None)
        if not db:
            return None
        spec = self.mode_registry.get(mode_id) if self.mode_registry else None
        if not spec:
            return None
        try:
            resolved = self._resolve_params(spec, params or {})
        except ValueError:
            return None
        self._sync_annotations(resolved)
        run = db.get_analysis_run(
            mode_id, self._params_hash(resolved), _doc_hash(resolved)
        )
        return run.get("output") if run else None

    def run(self, mode_id: str, params: Optional[Dict[str, Any]] = None,
            target_id: Optional[str] = None, cancel_check=None) -> Dict[str, Any]:
        params = dict(params or {})
        spec = self.mode_registry.get(mode_id) if self.mode_registry else None
        if not spec:
            return {"error": f"Unknown analysis mode '{mode_id}'"}
        validation = self._validate_params(spec, params)
        if validation:
            return {'error': validation}
        try:
            params = self._resolve_params(spec, params)
            spec = self._execution_spec(spec, params)
            readiness = self.prerequisites.check(spec, params)
        except ValueError as exc:
            return {'error': str(exc)}
        if spec.execution_class != 'prepared_blueprint' and not readiness['ready']:
            return {'status': 'not_ready', 'mode': mode_id, 'missing': readiness['missing'],
                    'message': describe_missing(readiness['missing']) or spec.empty_state}
        self._sync_annotations(params)

        cache_hash = self._params_hash(params)
        db = getattr(self.project_manager, "db_intelligence", None)
        cached_run = (
            db.get_analysis_run(mode_id, cache_hash, _doc_hash(params))
            if db else None
        )
        cached = cached_run.get("output") if cached_run else None
        if cancel_check and cancel_check():
            return {'status': 'cancelled', 'mode': mode_id}
        if cached is not None:
            self._emit_result(cached, target_id=target_id, cached=True)
            return {"status": "cached", "mode": mode_id, "result": cached}

        return self._dispatch(spec, params, target_id, cache_hash, cancel_check)

    def _dispatch(self, spec, params, target_id, cache_hash, cancel_check):
        mode_id = spec.id
        try:
            blueprint = self._create_blueprint(spec, target_id)
        except ValueError as exc:
            return {'error': str(exc)}

        # Stamp cache-key state so a mode's ARTIFACT_WRITE / caches key consistently.
        params.setdefault("analysis_cache_parameters", {
            key: value for key, value in params.items()
            if key not in {"params_hash", "doc_hash", "analysis_cache_parameters"}
        })
        params.setdefault("analysis_target_id", target_id)
        params["params_hash"] = cache_hash
        params["doc_hash"] = _doc_hash(params)

        if cancel_check and cancel_check():
            return {'status': 'cancelled', 'mode': mode_id}
        if self.bus:
            self.bus.analysis_result_changed.emit(
                AnalysisEvent.RUN_STARTED,
                AnalysisPayload(
                    template_id=mode_id,
                    result={"target_id": target_id, "mode_label": spec.label},
                ),
            )
            self.bus.workflow_action_requested.emit(
                WorkflowIntent.RUN_BLUEPRINT,
                WorkflowPayload(blueprint=blueprint, initial_state=params,
                                blueprint_id=spec.blueprint_id, target_id=target_id),
            )
            return {"status": "dispatched", "mode": mode_id, "blueprint_id": spec.blueprint_id}
        return {"error": "EventBus unavailable"}

    def _create_blueprint(self, spec, target_id):
        if not self.blueprint_registry:
            raise ValueError('Blueprint registry unavailable')
        kwargs = {'pm': self.project_manager}
        if spec.blueprint_id == 'intelligence_analysis_mode':
            kwargs['analysis_spec'] = spec
        try:
            blueprint = self.blueprint_registry.create(spec.blueprint_id, **kwargs)
        except Exception as exc:
            raise ValueError(f"Blueprint '{spec.blueprint_id}' unavailable: {exc}") from exc
        if blueprint is None:
            raise ValueError(f"Blueprint '{spec.blueprint_id}' not registered")
        error = self._enforce_model_budget(spec, blueprint)
        if error:
            raise ValueError(error)
        self._route_visible_outputs(blueprint, target_id)
        return blueprint

    def _resolved_graph_contract(self, spec, params):
        if spec.result_kind not in {'graph', 'composite'}:
            return {}
        contract = self.compiler.compile(spec.graph_contract,
            template=params.get('analysis_template'), density=params.get('analysis_detail_profile'))
        if params.get('custom_instructions'):
            contract['custom_instructions'] = str(params['custom_instructions'])
        contract['allow_repair_call'] = bool(contract.get('allow_repair_call')
            and spec.model_call_budget.max_calls >= 2 and spec.model_call_budget.allow_repair_calls)
        return contract

    def _resolve_params(self, spec, params):
        resolved = {key: value for key, value in deepcopy(dict(params)).items() if key not in {
            'params_hash', 'doc_hash', 'analysis_cache_parameters', 'analysis_target_id'}}
        for key, schema in spec.params_schema.items():
            if isinstance(schema, dict) and 'default' in schema:
                resolved.setdefault(key, deepcopy(schema['default']))
        contract = self._resolved_graph_contract(spec, resolved)
        template = self.compiler.template(resolved.get('analysis_template'))
        effective = replace(spec, graph_contract=contract,
            required_stages=template.get('required_stages', spec.required_stages),
            artifact_requirements=template.get('artifact_requirements', spec.artifact_requirements),
            required_artifact_types=[] if 'artifact_requirements' in template else spec.required_artifact_types)
        effective = self.mode_registry.validate(effective)
        resolved.update(analysis_mode_id=spec.id, analysis_mode_label=spec.label,
            analysis_focus=resolved.get('analysis_focus') or resolved.get('claim')
                           or resolved.get('user_query') or resolved.get('query') or '',
            allowed_source_ids=analysis_source_ids(self.project_manager, resolved),
            analysis_graph_contract=json.dumps(contract, ensure_ascii=False),
            analysis_model_call_budget=asdict(spec.model_call_budget),
            analysis_prerequisites={'required_stages': list(effective.required_stages),
                                    'artifact_requirements': deepcopy(effective.artifact_requirements)})
        resolved['analysis_allow_repair'] = bool(resolved.get('analysis_allow_repair', True)
                                                 and contract.get('allow_repair_call', False))
        template = resolved.get('analysis_template') or {'mode': spec.label,
            'description': spec.description, 'result_kind': spec.result_kind, 'graph_contract': contract}
        resolved['analysis_template_json'] = template if isinstance(template, str) else json.dumps(template, ensure_ascii=False)
        return resolved

    @staticmethod
    def _execution_spec(spec, params):
        return replace(spec, graph_contract=json.loads(params['analysis_graph_contract']),
                       required_artifact_types=[], **params['analysis_prerequisites'])

    def readiness(self, mode_id, params=None):
        spec = self.mode_registry.get(mode_id) if self.mode_registry else None
        if not spec:
            return {'ready': False, 'missing': [{'kind': 'mode', 'message': 'Unknown analysis mode.'}]}
        try:
            resolved = self._resolve_params(spec, params or {})
            return {**self.prerequisites.check(self._execution_spec(spec, resolved), resolved),
                    'graph_contract': json.loads(resolved['analysis_graph_contract'])}
        except ValueError as exc:
            return {'ready': False, 'missing': [{'kind': 'contract', 'message': str(exc)}]}

    def _params_hash(self, params: Dict[str, Any]) -> str:
        db = getattr(self.project_manager, "db_intelligence", None)
        source_ids = [str(value) for value in params.get("allowed_source_ids") or [] if value]
        if not source_ids and params.get("source_id"):
            source_ids = [str(params["source_id"])]
        state = (
            db.knowledge_signature(
                source_ids=source_ids,
                artifact_families=[
                    "source", "structure", "argument", "summary",
                    "annotation", "relation",
                ],
            )
            if db and hasattr(db, "knowledge_signature")
            else db.analysis_input_signature(source_ids=source_ids)
            if db and hasattr(db, "analysis_input_signature")
            else (db.state_signature() if db else "0")
        )
        payload = {
            "params": {
                key: value for key, value in (params or {}).items()
                if key not in ("params_hash", "doc_hash")
            },
            "artifact_state": state,
            "execution_contract_version": self.EXECUTION_CONTRACT_VERSION,
            "prompt_signature": self._prompt_signature(),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:24]

    def _prompt_signature(self) -> str:
        manager = self.prompt_manager
        if not manager or not hasattr(manager, "get_prompt"):
            return "unavailable"
        keys = (
            "Intelligence Universal Graph System v6",
            "Intelligence Universal Graph Query v6",
            "Intelligence Universal Graph Repair Query v4",
            "Intelligence Mode Graph Output Enforcer v4",
            "JSON Only Directive",
            "Context Budget Notice",
        )
        payload = {
            key: str(manager.get_prompt(key) or "") for key in keys
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:20]

    def _sync_annotations(self, params: Dict[str, Any]) -> None:
        """Synchronize user annotations before deriving any cache signature."""
        service = self.artifact_service
        if not service or not hasattr(service, "sync_all_annotation_artifacts"):
            return
        source_ids = [
            str(value) for value in params.get("allowed_source_ids") or [] if value
        ]
        if not source_ids and params.get("source_id"):
            source_ids = [str(params["source_id"])]
        try:
            if source_ids:
                for source_id in source_ids:
                    service.sync_all_annotation_artifacts(source_id)
            else:
                service.sync_all_annotation_artifacts()
        except Exception:
            # Analysis remains usable in projects that do not expose notes.
            pass

    @staticmethod
    def _walk_steps(steps):
        for step in steps or []:
            yield step
            yield from AnalysisModeService._walk_steps(
                list(getattr(step, "if_true", None) or [])
            )
            yield from AnalysisModeService._walk_steps(
                list(getattr(step, "if_false", None) or [])
            )

    @classmethod
    def _enforce_model_budget(cls, spec, blueprint) -> str:
        """Validate and clamp a blueprint before it reaches the runner."""
        budget = spec.model_call_budget
        model_steps = [
            step for step in cls._walk_steps(getattr(blueprint, "steps", []))
            if str(getattr(step, "step_type", "")).upper() in {
                "LLM_QUERY", "LLM_SCHEMA_QUERY"
            }
        ]
        if not bool((getattr(spec, "graph_contract", None) or {}).get(
            "allow_repair_call", budget.allow_repair_calls
        )):
            model_steps = [
                step for step in model_steps
                if str(getattr(step, "step_id", "")) != "repair_mode_graph"
            ]
        if len(model_steps) > int(budget.max_calls or 0):
            return (
                f"Analysis mode '{spec.id}' exceeds its model-call budget "
                f"({len(model_steps)} requested, {budget.max_calls} allowed)."
            )
        allowed_roles = set(budget.allowed_roles or [])
        for step in model_steps:
            role = str(getattr(step, "model_role", "") or "")
            if allowed_roles and role not in allowed_roles:
                return (
                    f"Analysis mode '{spec.id}' requested disallowed model role "
                    f"'{role or 'unspecified'}'."
                )
            options = getattr(step, "llm_options", None)
            if not isinstance(options, dict):
                options = {}
                step.llm_options = options
            if budget.max_output_tokens:
                requested = int(options.get("num_predict") or budget.max_output_tokens)
                options["num_predict"] = min(requested, budget.max_output_tokens)
            if budget.max_input_tokens:
                requested_ctx = int(
                    options.get("num_ctx")
                    or budget.max_input_tokens + budget.max_output_tokens
                )
                options["num_ctx"] = min(
                    requested_ctx,
                    budget.max_input_tokens + budget.max_output_tokens,
                )
            if not budget.allow_repair_calls:
                step.llm_retry_attempts = 0
        return ""

    @classmethod
    def _route_visible_outputs(cls, blueprint, target_id: Optional[str]) -> None:
        if not target_id:
            return
        for step in cls._walk_steps(getattr(blueprint, "steps", [])):
            if str(getattr(step, "ui_format", "silent")) != "silent":
                step.ui_target = target_id

    @staticmethod
    def _validate_params(spec, params: Dict[str, Any]) -> str:
        for key, schema in (spec.params_schema or {}).items():
            if not isinstance(schema, dict):
                continue
            value = params.get(key)
            # Backward-compatible aliases used by the existing mode UI.
            if value in (None, "", []) and key == "query":
                value = params.get("user_query")
            if value in (None, "", []) and key == "schema":
                value = params.get("extraction_schema")
            if schema.get("required") and value in (None, "", []):
                return f"Missing required input: {schema.get('label') or key}"
            if schema.get("type") == "json" and value not in (None, "", []):
                if isinstance(value, str):
                    try:
                        parsed = json.loads(value)
                    except Exception:
                        return f"{schema.get('label') or key} must be valid JSON."
                    if not isinstance(parsed, (dict, list)):
                        return f"{schema.get('label') or key} must be a JSON object or array."
        return ""

    def _emit_result(self, envelope: Dict[str, Any], *, target_id: Optional[str], cached: bool) -> None:
        if not self.bus:
            return
        result = dict(envelope.get("output") or {})
        result["analysis_envelope"] = {
            key: value for key, value in envelope.items() if key != "output"
        }
        result["cache_status"] = "hit" if cached else envelope.get("cache_status", "miss")
        self.bus.analysis_result_changed.emit(
            AnalysisEvent.RESULT_READY,
            AnalysisPayload(
                run_id=envelope.get("run_id"), template_id=envelope.get("mode_id"),
                result={**result, "target_id": target_id},
            ),
        )
        self.bus.analysis_result_changed.emit(
            AnalysisEvent.RUN_COMPLETED,
            AnalysisPayload(
                run_id=envelope.get("run_id"), template_id=envelope.get("mode_id"),
                result={"target_id": target_id, "cached": cached},
            ),
        )


def _params_hash(params: Dict[str, Any]) -> str:
    payload = {k: v for k, v in (params or {}).items()
               if k not in ("analysis_mode_id", "params_hash", "doc_hash")}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


def _doc_hash(params: Dict[str, Any]) -> str:
    key = str(params.get("source_id") or params.get("doc_path")
              or params.get("allowed_source_ids") or "project")
    return hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:16]
