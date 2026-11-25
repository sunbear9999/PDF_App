"""Printed labels and conservative piecewise page offsets."""
import re
from collections import Counter

_NUMBER = re.compile(r"^\s*[-–—]?\s*(\d{1,4}|[ivxlcdm]+)\s*[-–—]?\s*$", re.I)


def page_labels(blocks):
    labels = {}
    for block in blocks:
        native = block.structure.get("page_label")
        if native and re.fullmatch(r"<[fF][eE][fF][fF](?:[0-9a-fA-F]{4})+>", str(native)):
            native = bytes.fromhex(str(native)[5:-1]).decode("utf-16-be")
        if native and block.page_num is not None:
            labels[block.page_num] = str(native)
        for start, end, kind in block.furniture_spans:
            match = _NUMBER.fullmatch(block.exact_text[start:end]) if kind == "page_number" else None
            if match and block.page_num is not None and not native:
                labels[block.page_num] = match[1].lower()
    return labels


def target_pages(label, labels):
    """Exact labels win. Infer only from at least two agreeing neighbours.

    Extracted chapters can have gaps; never extrapolate an offset outside its
    observed run, or map an absent chapter to an unrelated physical page.
    """
    exact = [page for page, value in labels.items() if value == label]
    if exact or not label.isdigit():
        return exact
    offsets = Counter(page - int(value) for page, value in labels.items() if value.isdigit())
    result = []
    for offset, count in offsets.items():
        observed = [page for page, value in labels.items() if value.isdigit() and page - int(value) == offset]
        target = int(label) + offset
        if count >= 2 and min(observed) <= target <= max(observed):
            result.append(target)
    return result
