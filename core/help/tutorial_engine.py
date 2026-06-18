"""
core/help/tutorial_engine.py

State machine governing tutorial execution.

The engine is a pure QObject with no widget imports. It emits signals
that the GUI overlay connects to. All temporary connections are cleaned
up on cancel, completion, and failure.

States:
    IDLE → PREPARING → WAITING_FOR_TARGET → DISPLAYING_STEP
         → WAITING_FOR_INTERACTION → ADVANCING → [DISPLAYING_STEP | COMPLETED]
    Any → CANCELLED (cancel() called)
    WAITING_FOR_TARGET → FAILED (target not found within timeout)
"""
from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal

from core.help.models import TutorialDefinition, TutorialState, TutorialStep
from core.utils.managed_signal_mixin import _ManagedSignalMixin

if TYPE_CHECKING:
    from core.help.tutorial_registry import TutorialRegistry
    from core.help.ui_target_registry import UITargetRegistry
    from core.help.action_executor import ApprovedActionExecutor
    from core.events.event_bus import EventBus

log = logging.getLogger(__name__)

_POLL_INTERVAL_MS = 200
_POLL_TIMEOUT_MS = 5000


class TutorialEngine(QObject, _ManagedSignalMixin):
    """
    Drives a single tutorial from start to completion (or cancellation).

    The GUI layer connects to the signals below; the engine never imports
    any widget classes.
    """

    # Signals consumed by TutorialOverlay / HelpGUICoordinator
    state_changed = Signal(object)           # TutorialState
    step_ready = Signal(object, object)      # TutorialStep, QWidget (target)
    tutorial_completed = Signal(str)         # tutorial_id
    tutorial_cancelled = Signal(str)         # tutorial_id
    tutorial_failed = Signal(str, str)       # tutorial_id, reason

    def __init__(
        self,
        tutorial_registry: "TutorialRegistry",
        ui_target_registry: "UITargetRegistry",
        action_executor: "ApprovedActionExecutor",
        event_bus: "EventBus",
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._init_signal_tracking()

        self._registry = tutorial_registry
        self._targets = ui_target_registry
        self._executor = action_executor
        self._bus = event_bus

        self._state = TutorialState.IDLE
        self._current_tutorial: Optional[TutorialDefinition] = None
        self._current_step_index: int = 0

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_for_target)
        self._poll_elapsed_ms: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def state(self) -> TutorialState:
        return self._state

    @property
    def current_step(self) -> Optional[TutorialStep]:
        if (
            self._current_tutorial
            and 0 <= self._current_step_index < len(self._current_tutorial.steps)
        ):
            return self._current_tutorial.steps[self._current_step_index]
        return None

    @property
    def step_count(self) -> int:
        return len(self._current_tutorial.steps) if self._current_tutorial else 0

    @property
    def current_step_index(self) -> int:
        return self._current_step_index

    def start_tutorial(self, tutorial_id: str) -> bool:
        """
        Begin a tutorial. Returns False if the tutorial is unknown or one is already running.
        Cancels any in-progress tutorial before starting if called while active.
        """
        if self._state not in (
            TutorialState.IDLE,
            TutorialState.COMPLETED,
            TutorialState.CANCELLED,
            TutorialState.FAILED,
        ):
            log.warning("TutorialEngine: cannot start %r while in state %s", tutorial_id, self._state)
            return False

        tutorial = self._registry.get(tutorial_id)
        if tutorial is None:
            log.warning("TutorialEngine: tutorial %r not found", tutorial_id)
            return False
        if not tutorial.steps:
            log.warning("TutorialEngine: tutorial %r has no steps", tutorial_id)
            return False

        self._current_tutorial = tutorial
        self._current_step_index = 0
        self._set_state(TutorialState.PREPARING)
        self._advance_to_step(0)
        return True

    def advance(self) -> None:
        """Move to the next step (called when user clicks Next or the advance condition is met)."""
        if self._state != TutorialState.WAITING_FOR_INTERACTION:
            return
        self._set_state(TutorialState.ADVANCING)
        next_index = self._current_step_index + 1
        if not self._current_tutorial or next_index >= len(self._current_tutorial.steps):
            self._complete()
        else:
            self._advance_to_step(next_index)

    def go_back(self) -> None:
        """Move to the previous step."""
        if self._state != TutorialState.WAITING_FOR_INTERACTION:
            return
        prev = self._current_step_index - 1
        if prev >= 0:
            self._advance_to_step(prev)

    def cancel(self) -> None:
        """Cancel the running tutorial and clean up all connections."""
        tid = self._current_tutorial.id if self._current_tutorial else ""
        self._stop_poll()
        self._disconnect_all_tracked()
        self._set_state(TutorialState.CANCELLED)
        self.tutorial_cancelled.emit(tid)
        self._reset()

    # ------------------------------------------------------------------
    # Internal state transitions
    # ------------------------------------------------------------------

    def _advance_to_step(self, index: int) -> None:
        self._current_step_index = index
        step = self._current_tutorial.steps[index]

        # Execute any declarative before_actions (open dock, focus widget, etc.)
        self._executor.execute_many(step.before_actions)

        # Try to resolve the target immediately; poll if it isn't ready yet
        self._set_state(TutorialState.WAITING_FOR_TARGET)
        target = self._targets.resolve(step.target_id)
        if target is not None:
            self._on_target_found(target)
        else:
            self._start_poll()

    def _start_poll(self) -> None:
        self._poll_elapsed_ms = 0
        self._poll_timer.start()

    def _stop_poll(self) -> None:
        self._poll_timer.stop()
        self._poll_elapsed_ms = 0

    def _poll_for_target(self) -> None:
        self._poll_elapsed_ms += _POLL_INTERVAL_MS
        step = self.current_step
        if step is None:
            self._stop_poll()
            return
        target = self._targets.resolve(step.target_id)
        if target is not None:
            self._stop_poll()
            self._on_target_found(target)
        elif self._poll_elapsed_ms >= _POLL_TIMEOUT_MS:
            self._stop_poll()
            self._fail(
                f"Target '{step.target_id}' was not found after "
                f"{_POLL_TIMEOUT_MS // 1000}s. The dock or panel may not be open."
            )

    def _on_target_found(self, target) -> None:
        self._set_state(TutorialState.DISPLAYING_STEP)
        step = self.current_step
        if step:
            self.step_ready.emit(step, target)
            self._set_state(TutorialState.WAITING_FOR_INTERACTION)

    def _complete(self) -> None:
        tid = self._current_tutorial.id if self._current_tutorial else ""
        self._stop_poll()
        self._disconnect_all_tracked()
        self._set_state(TutorialState.COMPLETED)
        self.tutorial_completed.emit(tid)
        self._reset()

    def _fail(self, reason: str) -> None:
        tid = self._current_tutorial.id if self._current_tutorial else ""
        log.warning("TutorialEngine: tutorial %r failed — %s", tid, reason)
        self._stop_poll()
        self._disconnect_all_tracked()
        self._set_state(TutorialState.FAILED)
        self.tutorial_failed.emit(tid, reason)
        self._reset()

    def _reset(self) -> None:
        self._current_tutorial = None
        self._current_step_index = 0

    def _set_state(self, state: TutorialState) -> None:
        self._state = state
        self.state_changed.emit(state)
