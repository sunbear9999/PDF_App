"""Conservative text budget for a final model request.

The byte count is an upper bound for byte-fallback tokenizers. Providers with
an exact tokenizer can replace this estimate later without changing callers.
"""
from __future__ import annotations


def fit_text_request(
    query: str, system: str, *, context_limit: int, output_limit: int,
    overhead_tokens: int = 256,
) -> tuple[str, bool]:
    """Fit user text, preserving the system prompt or refusing the request."""
    available = int(context_limit) - int(output_limit) - int(overhead_tokens)
    system_bytes = len(system.encode("utf-8", "replace"))
    if available <= 0 or system_bytes >= available:
        raise ValueError(
            "The selected model's context is too small for this system prompt "
            f"and output budget ({system_bytes} system bytes, {available} available). "
            "Choose a larger-context model or shorten the prompt."
        )
    remaining = available - system_bytes
    encoded = query.encode("utf-8", "replace")
    if len(encoded) <= remaining:
        return query, False
    clipped = _clip_middle(encoded, remaining)
    if not clipped:
        raise ValueError("No user text fits the selected model's context budget.")
    return clipped, True


SHORTENED_MARKER = "\n\n[… shortened here to fit the model's context …]\n\n"
TAIL_SHARE = 0.5     # of the room kept: the request's end holds the question or instruction


def _clip_middle(encoded: bytes, room: int) -> str:
    """Cut from the middle, at line breaks where possible, so both ends survive: requests
    put history or instructions first and the user's question last."""
    marker = SHORTENED_MARKER.encode("utf-8")
    if room <= len(marker) * 2:
        return encoded[:room].decode("utf-8", "ignore").rstrip()
    keep = room - len(marker)
    tail_room = int(keep * TAIL_SHARE)
    head, tail = encoded[:keep - tail_room], encoded[len(encoded) - tail_room:]
    head = head[:head.rfind(b"\n")] if head.rfind(b"\n") > len(head) // 2 else head
    tail = tail[tail.find(b"\n") + 1:] if 0 <= tail.find(b"\n") < len(tail) // 2 else tail
    return (head.decode("utf-8", "ignore").rstrip() + SHORTENED_MARKER
            + tail.decode("utf-8", "ignore").lstrip())
