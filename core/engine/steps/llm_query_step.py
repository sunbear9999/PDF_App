"""
core/engine/steps/llm_query_step.py

Migrated from MasterActionRunner._run_llm(), _record_prompt_trace(), _has_citation_source().
"""
from __future__ import annotations

import json

from core.engine.steps.base_step import BaseStep
from core.engine.execution_result import SystemEvent
from core.engine.ui_payloads import build_ui_payloads
from core.models.prompt_models import PromptTraceCall, collect_step_prompt_usage
from core.plugins.plugin_step_protocol import StepContext
from core.utils.json_utils import TaggedBlockStreamFilter, extract_and_heal_json
from core.utils.state_resolver import StateResolver
from core.engine.llm_request_context import fit_query_to_context, prepare_request_context
from core.services.intelligence.retrieval_audit import (
    apply_selected_quotes, audit_for_prompt,
)


class LLMQueryStep(BaseStep):
    step_type = "LLM_QUERY"
    label = "LLM Query"
    category = "AI"
    description = "Generate text or structured JSON with a language model."
    input_schema = {
        "query": {"type": "string", "label": "Query / User Message", "required": True},
        "system_prompt": {"type": "string", "label": "System Prompt Override"},
        "model": {"type": "string", "label": "Model"},
        "images": {"type": "array", "label": "Image Inputs"},
        "context_items_key": {"type": "string", "label": "Atomic context items (state key)"},
    }

    def execute(self, context: StepContext, inputs: dict):
        # The runner passes the resolved model via the inputs dict under "_resolved_model"
        # when dispatching through the registry path.  Fall back to state.
        resolved_model = inputs.get("_resolved_model") or context.state.get("selected_model", "")

        # ------- Reconstruct the step proxy from context -------
        # StepContext carries the step's attributes via _step_proxy set by the runner.
        step = getattr(context, "_step_proxy", None)

        # ------- Build system prompt -------
        system_prompt = (getattr(step, "system_prompt", None) or inputs.get("system_prompt", "")).strip()
        pm = context.prompt_manager

        required_prompt_keys = list(getattr(step, "required_prompt_keys", []) or [])
        if required_prompt_keys and not pm:
            raise RuntimeError("Prompt Manager is required for this workflow step.")
        for required_key in required_prompt_keys:
            if not pm.get_prompt(required_key):
                raise KeyError(f"Required Prompt Manager key is unavailable: {required_key}")

        if pm and getattr(step, "prompt_key", None):
            resolved_key = StateResolver.resolve_val(step.prompt_key, context.state, pm)
            raw_prompt = pm.get_prompt(resolved_key)
            if not raw_prompt:
                raise KeyError(f"Required Prompt Manager key is unavailable: {resolved_key}")
            system_prompt = raw_prompt + "\n\n" + system_prompt

        if pm:
            req_context = getattr(step, "required_context", [])
            if "manifest" in req_context and context.state.get("include_manifest", True):
                if (getattr(step, "allow_manifest_updates", False)
                        and context.state.get("allow_manifest_updates", False)):
                    protocol = getattr(step, "manifest_update_protocol", "tagged")
                    directive_key = (
                        "Manifest Structured Update Directive"
                        if protocol == "structured"
                        else "Manifest Update Directive"
                    )
                    directive = pm.get_prompt(directive_key)
                    if not directive:
                        raise KeyError(
                            f"Required Prompt Manager key is unavailable: {directive_key}"
                        )
                    system_prompt += "\n\n" + directive
                system_prompt += "\n\n" + (pm.get_prompt("Context Inject - Manifest") or "")
            if "workspace" in req_context:
                system_prompt += "\n\n" + (pm.get_prompt("Context Inject - Workspace") or "")
            if "selected_nodes" in req_context:
                system_prompt += "\n\n" + (pm.get_prompt("Context Inject - Selected") or "")
            if "analyses" in req_context:
                system_prompt += "\n\n" + (pm.get_prompt("Context Inject - Analyses") or "")

            ui_format = getattr(step, "ui_format", inputs.get("_ui_format", ""))
            from core.registries.ai_widget_registry import AIWidgetRegistry
            _widget_defn = AIWidgetRegistry.get_instance().get(ui_format)
            if _widget_defn and _widget_defn.prompt_key:
                system_prompt += "\n\n" + (pm.get_prompt(_widget_defn.prompt_key) or "")

            has_rag = self._has_citation_source(step, context.state)
            manual_citations = getattr(step, "inline_citations", False)
            auto_citations = _widget_defn.auto_citations if _widget_defn else False
            if (manual_citations or auto_citations) and has_rag:
                system_prompt += "\n\n" + (pm.get_prompt("Inline Citation Directive") or "")

        # Resolve any {variable} references in the composed system prompt
        system_prompt = StateResolver.resolve_val(system_prompt, context.state, pm)

        # ------- LLM options -------
        base_options = dict(getattr(step, "llm_options", {}) or {})
        service = getattr(context, "llm_settings_service", None)
        if service:
            options = service.resolve_options(
                blueprint_id=getattr(context, "blueprint_id", None),
                step_path=getattr(context, "step_path", None),
                step=step,
                base_options=base_options,
            )
        else:
            options = {"temperature": 0.7, "num_predict": 2048, "num_ctx": 16384, "json_mode": False}
            options.update(base_options)
        options = StateResolver.resolve_val(options, context.state, pm)
        for key in ("temperature", "num_predict", "num_ctx"):
            if key in options and options[key] not in (None, ""):
                try:
                    options[key] = float(options[key]) if key == "temperature" else int(options[key])
                except Exception:
                    pass
        if "json_mode" in options and not isinstance(options["json_mode"], bool):
            options["json_mode"] = str(options["json_mode"]).strip().lower() in {"1", "true", "yes", "on"}
        if options.get("num_predict", 1) <= 0:
            options["num_predict"] = 2048
        role = getattr(step, "_resolved_model_role", None) or getattr(step, "model_role", None)
        options = self._clamp_options_for_model(options, resolved_model, role, context)

        output_schema = getattr(step, "output_schema", None)
        if output_schema:
            options["json_mode"] = True
            schema_str = json.dumps(output_schema, indent=2)
            if pm:
                enforcer_key = (
                    getattr(step, "output_schema_prompt_key", None)
                    or "JSON Schema Enforcer"
                )
                enforcer = pm.get_prompt(enforcer_key)
                if not enforcer:
                    raise KeyError(
                        "Required Prompt Manager key is unavailable: "
                        f"{enforcer_key}"
                    )
                system_prompt += "\n\n" + enforcer.replace("{schema_str}", schema_str)
        elif options.get("json_mode") and "JSON" not in system_prompt:
            if not pm:
                raise RuntimeError("Prompt Manager is required for JSON-mode instructions.")
            directive = pm.get_prompt("JSON Only Directive")
            if not directive:
                raise KeyError("Required Prompt Manager key is unavailable: JSON Only Directive")
            system_prompt += "\n\n" + directive

        if getattr(step, "show_reasoning", False) and not options.get("json_mode"):
            if not pm:
                raise RuntimeError("Prompt Manager is required for reasoning instructions.")
            directive = pm.get_prompt("Reasoning Disclosure Directive") or ""
            if not directive:
                raise KeyError(
                    "Required Prompt Manager key is unavailable: "
                    "Reasoning Disclosure Directive"
                )
            system_prompt += "\n\n" + directive

        query_text, context_notice, context_updates, trace_context = prepare_request_context(
            inputs, context, step, system_prompt, options)
        if context_notice:
            system_prompt += "\n\n" + context_notice

        trace_inputs = dict(inputs)
        trace_inputs["query"] = query_text
        trace_inputs.update(trace_context)

        # ------- Prompt trace -------
        trace_event = self._build_trace_event(step, trace_inputs, resolved_model, system_prompt, options, context)

        # ------- LLM call -------
        lm = context.llm_manager
        abort_event = None
        if context.abort_check:
            import threading
            abort_event = threading.Event()
            original_check = context.abort_check

            class _AbortAdapter:
                def is_set(self_):
                    return original_check()
            abort_event = _AbortAdapter()

        # Streaming callback via WorkflowStepAPI event — picked up by runner's progress_update
        stream_chunks: list[str] = []
        manifest_filter = TaggedBlockStreamFilter("UPDATE_MANIFEST")

        def _stream_cb(chunk: str) -> None:
            stream_chunks.append(chunk)
            visible_chunk = manifest_filter.feed(chunk)
            if context.api and visible_chunk:
                context.api.emit_event("stream_chunk", {"chunk": visible_chunk})

        query_kwargs = {
            "question": query_text,
            "selected_model": resolved_model,
            "custom_system_prompt": system_prompt,
            "abort_event": abort_event,
            "callback": _stream_cb,
            "rag_enabled": False,
            "json_mode": options.get("json_mode"),
            "temperature": options.get("temperature"),
            "num_predict": options.get("num_predict"),
            "num_ctx": options.get("num_ctx") or 16384,
            "stop": options.get("stop_sequences"),
            "images": inputs.get("images") or [],
            "required_capabilities": (
                getattr(step, "required_model_capabilities", [])
                or inputs.get("required_model_capabilities")
                or []
            ),
        }
        retry_attempts = max(0, int(getattr(step, "llm_retry_attempts", 0) or 0))
        try:
            raw_result = self._query_with_transport_retries(
                lm,
                query_kwargs,
                retry_attempts,
                abort_event,
            )
        except (ConnectionError, OSError) as exc:
            if not getattr(step, "allow_manifest_updates", False):
                raise
            raw_result = f"[Generation Error: {exc}]"
        trailing_visible = manifest_filter.flush()
        if context.api and trailing_visible:
            context.api.emit_event("stream_chunk", {"chunk": trailing_visible})

        generation_failed = "[Generation Error" in str(raw_result)
        if generation_failed and not getattr(step, "allow_manifest_updates", False):
            raise ConnectionError(f"Engine Failed: {raw_result.strip()}")

        if output_schema:
            success, parsed = extract_and_heal_json(raw_result)
            if success:
                if isinstance(parsed, dict) and "final_output" in parsed:
                    raw_result = json.dumps(parsed["final_output"])
                else:
                    raw_result = json.dumps(parsed)
                source_key = getattr(step, "citation_source_key", None)
                if source_key and context.state.get(source_key):
                    from core.services.intelligence.context_sources import STATE_KEY
                    raw_result = self._restore_citation_metadata(
                        raw_result, str(context.state[source_key]), context.state.get(STATE_KEY)
                    )

        # ------- Build UI payloads -------
        ui_format = getattr(step, "ui_format", inputs.get("_ui_format", "silent"))
        trace_id = context.trace_id
        ui_title = getattr(step, "ui_title", "AI Result")

        system_events = [trace_event] if trace_event else []

        if ui_format == "workspace_graph":
            # Emit the graph on the bus instead of building a restorable payload.
            # UIRouter's workspace_graph special case is no longer needed for this path.
            from core.events.domains.workspace_events import WorkspaceEvent, WorkspaceEventPayload
            system_events.append(
                self.bus_emit_event(
                    "ai_graph_generated",
                    WorkspaceEvent.AI_GRAPH_GENERATED,
                    WorkspaceEventPayload(result_text=raw_result),
                )
            )
            return self.build_result(raw_result, system_events=system_events, state_updates=context_updates)

        payloads = build_ui_payloads(ui_format, raw_result, step=step, trace_id=trace_id, title=ui_title)
        audit = audit_for_prompt(
            context.state.get("retrieval_context_audit"), query_text
        )
        response_citations = []
        for payload in payloads:
            if payload.get("type") != "citation_cards":
                continue
            items = payload.get("items") or []
            if isinstance(items, dict):
                items = items.get("citations") or [items]
            response_citations.extend(
                item for item in items if isinstance(item, dict)
            )
        if response_citations:
            audit = apply_selected_quotes(audit, response_citations)
        if audit.get("candidates") and ui_format != "silent":
            rag_payload = {
                "type": "rag_context_view",
                "audit": audit,
                "attach_to_response": True,
            }
            hide_index = next(
                (
                    index for index, payload in enumerate(payloads)
                    if payload.get("type") == "hide_status"
                ),
                len(payloads),
            )
            payloads.insert(hide_index, rag_payload)
            if hide_index < len(payloads) - 1:
                payloads[-1]["keep_active"] = True

        return self.build_result(
            raw_result,
            ui_payloads=payloads,
            system_events=system_events,
            state_updates=context_updates,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @classmethod
    def _query_with_transport_retries(cls, lm, query_kwargs, retry_attempts, abort_event):
        for attempt in range(retry_attempts + 1):
            try:
                result = lm.query(**query_kwargs)
            except (ConnectionError, OSError):
                if attempt >= retry_attempts or (abort_event and abort_event.is_set()):
                    raise
                continue
            if not cls._is_retryable_generation_error(result):
                return result
            if attempt >= retry_attempts or (abort_event and abort_event.is_set()):
                return result
        return result

    @staticmethod
    def _is_retryable_generation_error(result) -> bool:
        text = str(result or "").strip().lower()
        if "[generation error" not in text:
            return False
        retryable_markers = (
            "connection aborted",
            "connection reset",
            "remote disconnected",
            "remotedisconnected",
            "server disconnected",
            "temporarily unavailable",
            "bad gateway",
            "status 502",
            "status 503",
            "status 504",
        )
        return any(marker in text for marker in retryable_markers)

    def _has_citation_source(self, step, state: dict) -> bool:
        source_key = getattr(step, "citation_source_key", None)
        if not source_key:
            return False
        value = state.get(source_key)
        if value is None:
            return False
        text = str(value).strip()
        return bool(text and text not in {"[]", "{}", "No relevant documents found.", "RAG is offline or collection is empty."})

    @staticmethod
    def _restore_citation_metadata(raw_result: str, source_context: str, sources=None) -> str:
        """Point each model citation at its passage (by label, else exact quote) and fill in the
        routing data the app kept for that passage; the model never copies ids."""
        from core.services.intelligence import context_sources
        try:
            payload = json.loads(raw_result)
        except Exception:
            return raw_result
        citations = payload.get("citations") if isinstance(payload, dict) else payload
        if not isinstance(citations, list):
            return raw_result
        candidates = context_sources.blocks(source_context, sources)
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            found = context_sources.match(citation, candidates)
            if not found:
                continue
            for key in ("doc_name", "source_type", "source_id", "artifact_id", "page"):
                if found.get(key) not in (None, ""):
                    citation[key] = found[key]
            citation["source"] = found["label"]
            citation["source_locator"] = dict(found.get("source_locator") or {})
        return json.dumps(payload)

    def _build_trace_event(self, step, inputs: dict, model: str, system_prompt: str, options: dict, context: StepContext):
        try:
            import hashlib
            usage = collect_step_prompt_usage(step) if step else None
            trace_options = dict(options or {})
            trace_options["image_count"] = len(inputs.get("images") or [])
            trace_options["required_model_capabilities"] = list(getattr(step, "required_model_capabilities", []) or [])
            trace_options["retry_attempts"] = int(getattr(step, "llm_retry_attempts", 0) or 0)
            if '_context_packing' in inputs:
                trace_options['context_packing'] = inputs['_context_packing']
            prompt_versions = {}
            manager = getattr(context, "prompt_manager", None)
            for key in getattr(usage, "explicit", []) if usage else []:
                try:
                    content = manager.get_prompt(key) if manager else ""
                    prompt_versions[key] = hashlib.sha256(
                        str(content or "").encode("utf-8", "replace")
                    ).hexdigest()[:12]
                except Exception:
                    prompt_versions[key] = "unavailable"
            state = context.state if isinstance(context.state, dict) else {}
            artifact_inputs = list(dict.fromkeys(
                str(value) for value in (
                    (state.get("analysis_evidence_aliases") or {}).values()
                ) if value
            ))
            if '_artifact_input_ids' in inputs:
                artifact_inputs = inputs['_artifact_input_ids']
            call_dict = {
                "step_id": getattr(step, "step_id", ""),
                "step_type": self.step_type,
                "model": str(model or ""),
                "model_role": getattr(step, "_resolved_model_role", None) or getattr(step, "model_role", None),
                "query": str(inputs.get("query", "")),
                "system_prompt": str(system_prompt or ""),
                "prompt_key": getattr(step, "prompt_key", None),
                "explicit_prompt_keys": getattr(usage, "explicit", []) if usage else [],
                "implicit_prompt_keys": getattr(usage, "implicit", []) if usage else [],
                "prompt_versions": prompt_versions,
                "artifact_input_ids": artifact_inputs,
                "call_budget": dict(state.get("analysis_model_call_budget") or {}),
                "llm_options": trace_options,
            }
            return SystemEvent(event_type="prompt_trace", payload=call_dict)
        except Exception as exc:
            import traceback as _tb
            print(f"[LLMQueryStep] Trace event build failed for step "
                  f"'{getattr(step, 'step_id', '?')}': {exc}\n{_tb.format_exc()}")
            return None

    def _clamp_options_for_model(self, options: dict, model: str, role: str, context: StepContext) -> dict:
        options = dict(options or {})
        svc = getattr(context, "model_role_service", None)
        max_ctx = 8192
        if svc and hasattr(svc, "max_context_tokens_for_model"):
            try:
                max_ctx = int(svc.max_context_tokens_for_model(model, role=role or ""))
            except Exception:
                pass
        requested_ctx = int(options.get("num_ctx") or max_ctx)
        options["num_ctx"] = max(256, min(requested_ctx, max_ctx))
        requested_predict = int(options.get("num_predict") or 1024)
        options["num_predict"] = max(64, min(requested_predict, max(64, options["num_ctx"] // 3)))
        options["context_clamped"] = requested_ctx != options["num_ctx"] or requested_predict != options["num_predict"]
        options["model_context_ceiling"] = max_ctx
        return options

    def _fit_query_to_context(
        self, query: str, system_prompt: str, options: dict, prompt_manager=None
    ) -> tuple[str, str]:
        return fit_query_to_context(query, system_prompt, options, prompt_manager)
