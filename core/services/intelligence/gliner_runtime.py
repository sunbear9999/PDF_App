"""GLiNER span recognition with onnxruntime and numpy only (no torch).

GLiNER reads a prompt of entity labels followed by the words of a text and
scores every span of up to ``max_width`` words against every label. The
exported ONNX graph takes:

* ``input_ids`` / ``attention_mask``: ``[CLS] <<ENT>> label ... <<SEP>> words [SEP]``
  tokenised word by word;
* ``words_mask``: for the first sub-token of each text word, its 1-based word
  index, else 0;
* ``text_lengths``: the number of text words;
* ``span_idx`` / ``span_mask``: every (start, end) word pair, end = start + width.

It returns logits ``[batch, words, max_width, labels]``. Decoding keeps spans
above a per-label threshold, then the highest-scoring non-overlapping ones.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from core.services.intelligence.onnx_text_runtime import LazyOnnxSession

# GLiNER's "whitespace" splitter: words, hyphen/underscore compounds, or one symbol.
WORD_SPLIT = re.compile(r"\w+(?:[-_]\w+)*|\S")
DEFAULT_THRESHOLD = 0.5
MAX_SEQUENCE = 512         # sub-tokens per window, prompt included
PACK_WORDS = 200           # words of packed short texts per model call
MAX_WORDS = 300            # longer texts are scored in windows of this many words
WINDOW_OVERLAP = 30


@dataclass
class GlinerSpan:
    start: int          # character offsets in the scored text
    end: int
    label: str
    score: float


class GlinerRuntime:
    def __init__(self, model_dir: str) -> None:
        self.runtime = LazyOnnxSession(model_dir, max_length=512)
        self.model_dir = model_dir
        self._config: Optional[Dict] = None

    def is_available(self) -> bool:
        return self.runtime.is_installed() and os.path.isfile(os.path.join(self.model_dir, "gliner_config.json"))

    def reload(self) -> None:
        self.runtime.reset()
        self._config = None

    @property
    def config(self) -> Dict:
        if self._config is None:
            try:
                with open(os.path.join(self.model_dir, "gliner_config.json"), encoding="utf-8") as stream:
                    self._config = json.load(stream)
            except (OSError, ValueError):
                self._config = {}
        return self._config

    def predict(self, text: str, labels: Sequence[str],
                thresholds: Optional[Dict[str, float]] = None) -> List[GlinerSpan]:
        """Non-overlapping labelled spans in ``text`` (empty when the model is unavailable)."""
        if not text.strip() or not labels or not self.runtime.ensure():
            return []
        words = [(m.start(), m.end()) for m in WORD_SPLIT.finditer(text)]
        spans: List[GlinerSpan] = []
        step = MAX_WORDS - WINDOW_OVERLAP
        for first in range(0, max(1, len(words)), step):
            window = words[first:first + MAX_WORDS]
            if not window:
                break
            spans.extend(self._score_window(text, window, list(labels), thresholds or {}))
            if first + MAX_WORDS >= len(words):
                break
        return greedy_non_overlapping(spans)

    def predict_many(self, texts: Sequence[str], labels: Sequence[str],
                     thresholds: Optional[Dict[str, float]] = None) -> List[List[GlinerSpan]]:
        """Spans per text, scoring several short texts per model call.

        Texts are packed into windows of up to ``PACK_WORDS`` words joined by
        newlines; spans crossing a text boundary are dropped and offsets are
        mapped back to each text."""
        out: List[List[GlinerSpan]] = [[] for _ in texts]
        pack: List[int] = []
        words = 0
        for index, text in enumerate(texts):
            count = len(WORD_SPLIT.findall(text))
            if pack and words + count > PACK_WORDS:
                self._predict_pack(texts, pack, labels, thresholds, out)
                pack, words = [], 0
            pack.append(index)
            words += count
        if pack:
            self._predict_pack(texts, pack, labels, thresholds, out)
        return out

    def _predict_pack(self, texts, pack, labels, thresholds, out) -> None:
        starts, joined = [], ""
        for index in pack:
            starts.append(len(joined))
            joined += texts[index] + "\n"
        for span in self.predict(joined, labels, thresholds):
            slot = max(i for i, start in enumerate(starts) if start <= span.start)
            base, end = starts[slot], starts[slot] + len(texts[pack[slot]])
            if span.end <= end:
                out[pack[slot]].append(GlinerSpan(span.start - base, span.end - base, span.label, span.score))

    def _score_window(self, text, window, labels, thresholds) -> List[GlinerSpan]:
        session, tokenizer = self.runtime.session, self.runtime.tokenizer
        max_width = int(self.config.get("max_width") or 12)
        feeds = encode(tokenizer, labels, [text[a:b] for a, b in window], max_width,
                       self.config.get("ent_token", "<<ENT>>"), self.config.get("sep_token", "<<SEP>>"))
        if feeds is None:   # too many sub-tokens for one pass: score each half
            if len(window) < 2:
                return []
            half = len(window) // 2
            return (self._score_window(text, window[:half], labels, thresholds)
                    + self._score_window(text, window[half:], labels, thresholds))
        names = {item.name for item in session.get_inputs()}
        logits = session.run(None, {k: v for k, v in feeds.items() if k in names})[0][0]
        return decode(logits, window, labels, thresholds)


def encode(tokenizer, labels: List[str], words: List[str], max_width: int,
           ent_token: str = "<<ENT>>", sep_token: str = "<<SEP>>") -> Optional[Dict]:
    import numpy as np
    prompt: List[str] = []
    for label in labels:
        prompt += [ent_token, label]
    prompt.append(sep_token)
    tokens = prompt + words
    ids, mask = [], []
    for index, token in enumerate(tokens):
        pieces = tokenizer.encode(token, add_special_tokens=False).ids or [tokenizer.token_to_id("<unk>")
                                                                             or tokenizer.token_to_id("[UNK]") or 0]
        if token in (ent_token, sep_token):
            pieces = [tokenizer.token_to_id(token) or pieces[0]]
        word = index - len(prompt) + 1 if index >= len(prompt) else 0
        ids += pieces
        mask += [word] + [0] * (len(pieces) - 1)
    prefix, suffix = special_tokens(tokenizer)
    if len(ids) + len(prefix) + len(suffix) > MAX_SEQUENCE:
        return None
    ids = prefix + ids + suffix
    mask = [0] * len(prefix) + mask + [0] * len(suffix)
    count = len(words)
    starts = np.repeat(np.arange(count), max_width)
    ends = starts + np.tile(np.arange(max_width), count)
    return {
        "input_ids": np.asarray([ids], dtype=np.int64),
        "attention_mask": np.ones((1, len(ids)), dtype=np.int64),
        "words_mask": np.asarray([mask], dtype=np.int64),
        "text_lengths": np.asarray([[count]], dtype=np.int64),
        "span_idx": np.stack([starts, np.minimum(ends, count - 1)], axis=-1)[None].astype(np.int64),
        "span_mask": (ends < count)[None],
    }


def special_tokens(tokenizer) -> Tuple[List[int], List[int]]:
    """The ids the tokenizer's post-processor puts around a sequence
    ([CLS] ... [SEP] for DeBERTa, ... </s> for mT5)."""
    plain = tokenizer.encode("x", add_special_tokens=False).ids
    full = tokenizer.encode("x").ids
    start = next((i for i in range(len(full)) if full[i:i + len(plain)] == plain), 0)
    return full[:start], full[start + len(plain):]


def decode(logits, window: List[Tuple[int, int]], labels: List[str],
           thresholds: Dict[str, float]) -> List[GlinerSpan]:
    import numpy as np
    probs = 1.0 / (1.0 + np.exp(-logits))
    out: List[GlinerSpan] = []
    count = len(window)
    for start, width, label_index in zip(*np.nonzero(probs > min([DEFAULT_THRESHOLD, *thresholds.values()]))):
        end = start + width
        label = labels[label_index]
        score = float(probs[start, width, label_index])
        if end < count and score >= thresholds.get(label, DEFAULT_THRESHOLD):
            out.append(GlinerSpan(window[start][0], window[end][1], label, round(score, 4)))
    return out


def greedy_non_overlapping(spans: List[GlinerSpan]) -> List[GlinerSpan]:
    kept: List[GlinerSpan] = []
    for span in sorted(spans, key=lambda s: (-s.score, s.start)):
        if all(span.end <= other.start or other.end <= span.start for other in kept):
            kept.append(span)
    return sorted(kept, key=lambda s: s.start)
