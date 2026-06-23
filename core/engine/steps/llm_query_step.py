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
            if "manifest" in req_context:
                if (getattr(step, "allow_manifest_updates", False)
                        and context.state.get("allow_manifest_updates", False)):
                    system_prompt += "\n\n" + (pm.get_prompt("Manifest Update Directive") or "")
                system_prompt += "\n\n" + (pm.get_prompt("Context Inject - Manifest") or "")
            if "workspace" in req_context:
                system_prompt += "\n\n" + (pm.get_prompt("Context Inject - Workspace") or "")
            if "selected_nodes" in req_context:
                system_prompt += "\n\n" + (pm.get_prompt("Context Inject - Selected") or "")
            if "analyses" in req_context:
                system_prompt += "\n\n" + (pm.get_prompt("Context Inject - Analyses") or "")

            ui_format = getattr(step, "ui_format", inputs.get("_ui_format", ""))
            if ui_format == "chat_widgets":
                system_prompt += "\n\n" + (pm.get_prompt("Format Enforcer - Chat Widgets") or "")
            elif ui_format == "data_table":
                system_prompt += "\n\n" + (pm.get_prompt("Format Enforcer - Data Table") or "")
            elif ui_format == "card_grid":
                system_prompt += "\n\n" + (pm.get_prompt("Format Enforcer - Card Grid") or "")

            elif ui_format == "live_stream":
                system_prompt += "\n\n" + (pm.get_prompt("Format Enforcer - Live Stream") or "")

            if getattr(step, "inline_citations", False) and self._has_citation_source(step, context.state):
                system_prompt += "\n\n" + (pm.get_prompt("Inline Citation Directive") or "")

        # Resolve any {variable} references in the composed system prompt
        system_prompt = StateResolver.resolve_val(system_prompt, context.state, pm)

        # ------- LLM options -------
        options = {"temperature": 0.7, "num_predict": 2048, "json_mode": False}
        options.update(getattr(step, "llm_options", {}))
        options = StateResolver.resolve_val(options, context.state, pm)
        for key in ("temperature", "num_predict", "num_ctx"):
            if key in options and options[key] not in (None, ""):
                try:
                    options[key] = float(options[key]) if key == "temperature" else int(options[key])
                except Exception:
                    pass
        if options.get("num_predict", 1) <= 0:
            options["num_predict"] = 2048

        output_schema = getattr(step, "output_schema", None)
        if output_schema:
            options["json_mode"] = True
            schema_str = json.dumps(output_schema, indent=2)
            if pm:
                enforcer = pm.get_prompt("JSON Schema Enforcer")
                if not enforcer:
                    raise KeyError("Required Prompt Manager key is unavailable: JSON Schema Enforcer")
                system_prompt += "\n\n" + enforcer.replace("{schema_str}", schema_str)
        elif options.get("json_mode") and "JSON" not in system_prompt:
            system_prompt += "\n\nCRITICAL: Output ONLY valid JSON. No markdown blocks, no explanations."

        # ------- Prompt trace -------
        trace_event = self._build_trace_event(step, inputs, resolved_model, system_prompt, options, context)

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

        raw_result = lm.query(
            question=inputs.get("query", ""),
            selected_model=resolved_model,
            custom_system_prompt=system_prompt,
            abort_event=abort_event,
            callback=_stream_cb,
            rag_enabled=False,
            json_mode=options.get("json_mode"),
            temperature=options.get("temperature"),
            num_predict=options.get("num_predict"),
            num_ctx=options.get("num_ctx") or 16384,
            stop=options.get("stop_sequences"),
            images=inputs.get("images") or [],
            required_capabilities=getattr(step, "required_model_capabilities", []) or inputs.get("required_model_capabilities") or [],
        )
        trailing_visible = manifest_filter.flush()
        if context.api and trailing_visible:
            context.api.emit_event("stream_chunk", {"chunk": trailing_visible})

        if str(raw_result).strip().startswith("[Generation Error"):
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
                    raw_result = self._restore_citation_metadata(
                        raw_result, str(context.state[source_key])
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
            return self.build_result(raw_result, system_events=system_events)

        payloads = build_ui_payloads(ui_format, raw_result, step=step, trace_id=trace_id, title=ui_title)

        return self.build_result(
            raw_result,
            ui_payloads=payloads,
            system_events=system_events,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

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
    def _restore_citation_metadata(raw_result: str, source_context: str) -> str:
        """Match exact citation quotes back to RAG blocks and restore routing data."""
        try:
            payload = json.loads(raw_result)
        except Exception:
            return raw_result
        citations = payload.get("citations") if isinstance(payload, dict) else payload
        if not isinstance(citations, list):
            return raw_result

        blocks = []
        for raw_block in source_context.split("--- DOCUMENT:")[1:]:
            block = "--- DOCUMENT:" + raw_block
            metadata = {}
            locator = {}
            for line in block.splitlines():
                if line.startswith("CITATION_LOCATOR_") and ":" in line:
                    key, value = line.split(":", 1)
                    locator[key.removeprefix("CITATION_LOCATOR_")] = value.strip()
                elif line.startswith("CITATION_") and ":" in line:
                    key, value = line.split(":", 1)
                    metadata[key.removeprefix("CITATION_").lower()] = value.strip()
            metadata["source_locator"] = locator
            metadata["_normalized_block"] = " ".join(block.lower().split())
            blocks.append(metadata)

        for citation in citations:
            if not isinstance(citation, dict):
                continue
            quote = " ".join(str(citation.get("quote", "")).lower().split())
            doc_name = str(citation.get("doc_name", "")).strip().lower()

            def score(block):
                value = 0
                if quote and quote in block["_normalized_block"]:
                    value += 100
                if doc_name and doc_name == block.get("doc_name", "").lower():
                    value += 20
                return value

            match = max(blocks, key=score, default=None)
            if not match or score(match) == 0:
                continue
            for key in ("doc_name", "source_type", "source_id", "page", "start", "end"):
                if key in match:
                    value = match[key]
                    if key in {"page", "start", "end"}:
                        try:
                            value = float(value) if "." in value else int(value)
                        except (TypeError, ValueError):
                            pass
                    citation[key] = value
            citation["source_locator"] = match.get("source_locator", {})
        return json.dumps(payload)

    def _build_trace_event(self, step, inputs: dict, model: str, system_prompt: str, options: dict, context: StepContext):
        try:
            usage = collect_step_prompt_usage(step) if step else None
            trace_options = dict(options or {})
            trace_options["image_count"] = len(inputs.get("images") or [])
            trace_options["required_model_capabilities"] = list(getattr(step, "required_model_capabilities", []) or [])
            call_dict = {
                "step_id": getattr(step, "step_id", ""),
                "step_type": self.step_type,
                "model": str(model or ""),
                "query": str(inputs.get("query", "")),
                "system_prompt": str(system_prompt or ""),
                "prompt_key": getattr(step, "prompt_key", None),
                "explicit_prompt_keys": getattr(usage, "explicit", []) if usage else [],
                "implicit_prompt_keys": getattr(usage, "implicit", []) if usage else [],
                "llm_options": trace_options,
            }
            return SystemEvent(event_type="prompt_trace", payload=call_dict)
        except Exception as exc:
            import traceback as _tb
            print(f"[LLMQueryStep] Trace event build failed for step "
                  f"'{getattr(step, 'step_id', '?')}': {exc}\n{_tb.format_exc()}")
            return None
