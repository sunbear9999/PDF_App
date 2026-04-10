import re


_BIDI_AND_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200e\u200f\u202a-\u202e]")
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def sanitize_extracted_text(raw_text: str, collapse_whitespace: bool = False) -> str:
    """Remove PDF annotation artifacts and invalid Unicode from extracted text."""
    if not raw_text:
        return ""

    clean_text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    clean_text = clean_text.replace("\ufffc", "").replace("\ufffd", "")
    clean_text = _SURROGATE_RE.sub("", clean_text)
    clean_text = _BIDI_AND_ZERO_WIDTH_RE.sub("", clean_text)

    # Join words split across line breaks during PDF extraction.
    clean_text = re.sub(r"-\s*\n\s*", "", clean_text)

    if collapse_whitespace:
        clean_text = re.sub(r"\s+", " ", clean_text)
    else:
        clean_text = re.sub(r"[ \t]+", " ", clean_text)
        clean_text = re.sub(r" *\n *", "\n", clean_text)
        clean_text = re.sub(r"\n{3,}", "\n\n", clean_text)

    # Final safety net: remove any residual code points that cannot be UTF-8 encoded.
    clean_text = clean_text.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")

    return clean_text.strip()