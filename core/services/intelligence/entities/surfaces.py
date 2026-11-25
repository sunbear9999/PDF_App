"""Token-trie propagation over accepted names, preserving source offsets."""
import copy
import re
import unicodedata
from collections import defaultdict

# Apostrophes join name parts (O'Brien) but not the possessive "’s" (Trump’s).
_TOKEN = re.compile(r"[^\W_]+(?:(?:[’']|-)(?![sS]\b)[^\W_]+)*", re.UNICODE)
_PROPER = {"PERSON", "ORG", "LOCATION", "NORP", "LANGUAGE", "PRODUCT", "EVENT", "WORK", "LAW", "FAC", "TITLE", "UNKNOWN"}


def _loses_capitals(accepted_surface, occurrence_tokens):
    """A proper name only matches where its capitalised words stay capitalised:
    "Cah" never becomes "cah", "o Ka" never "o ka"; "the 1868 Treaty" still matches."""
    accepted = [m[0] for m in _TOKEN.finditer(accepted_surface)]
    return len(accepted) != len(occurrence_tokens) or any(
        word[:1].isupper() and token[0][:1].islower() for word, token in zip(accepted, occurrence_tokens))
_END = ""
# Not running text: names here are listings, not mentions. Bibliography
# entries are references; their author names are not prose mentions.
_EXCLUDED = {"bibliography", "toc", "index", "glossary", "header", "footer", "boilerplate"}
_EXCLUDED_REGIONS = {"contents", "references", "index", "glossary"}
_NOTE_TYPES = {"footnote", "endnote"}


def region(row):
    """Where a sentence sits: body, note, caption, table or heading; None when excluded."""
    meta = row.get("metadata") or {}
    block_type = meta.get("block_type")
    area = (meta.get("structure") or {}).get("region", "body")
    if block_type in _EXCLUDED or area in _EXCLUDED_REGIONS:
        return None
    if block_type in _NOTE_TYPES or area == "notes":
        return "note"
    return block_type if block_type in {"caption", "table", "heading"} else "body"


def eligible(row, *, discover=True):
    """Headings receive known names but do not discover new ones."""
    where = region(row)
    return where is not None and (not discover or where != "heading")


def folded(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text).casefold()
                   if not unicodedata.combining(c))


def has_letter(tokens):
    return any(c.isalpha() for token in tokens for c in token)


def complete(mentions, records, templates, cancel_check=lambda: False):
    trie, owners = {}, defaultdict(set)
    for mention in mentions:
        tokens = tuple(folded(m[0]) for m in _TOKEN.finditer(mention.surface_text))
        if not has_letter(tokens):
            continue  # "1%" tokenises to "1": a bare number is not a name to match elsewhere
        owners[tokens].add((mention.entity_type, mention.normalized_key))
        node = trie
        for token in tokens:
            node = node.setdefault(token, {})
        node[_END] = mention
    covered = defaultdict(list)
    for mention in mentions:
        if mention.char_start is not None and mention.char_end is not None:
            covered[mention.sentence_id].append((mention.char_start, mention.char_end))
    added = []
    for row in records:
        if cancel_check():
            return mentions
        if not eligible(row, discover=False):
            continue
        sentence = str(row.get("storage_ref_id") or row.get("artifact_id") or "")
        template = templates[sentence]
        text = row.get("exact_text") or ""
        tokens = list(_TOKEN.finditer(text))
        for index, token in enumerate(tokens):
            node, candidates = trie, []
            for end in range(index, len(tokens)):
                if end > index and text[tokens[end-1].end():tokens[end].start()].strip():
                    break  # punctuation cannot join names across clauses
                node = node.get(folded(tokens[end][0]))
                if node is None:
                    break
                if _END in node:
                    candidates.append((end, node[_END]))
            for end, accepted in reversed(candidates):
                key = tuple(folded(t[0]) for t in tokens[index:end+1])
                start, stop = token.start(), tokens[end].end()
                if len(owners[key]) != 1 or any(a < stop and start < b for a, b in covered[sentence]):
                    continue
                surface = text[start:stop]
                if accepted.entity_type in _PROPER and _loses_capitals(accepted.surface_text, tokens[index:end+1]):
                    continue
                mention = copy.copy(template)
                mention.surface_text, mention.char_start, mention.char_end = surface, start, stop
                mention.entity_type, mention.normalized_key = accepted.entity_type, accepted.normalized_key
                mention.extractor_id, mention.confidence = "known_surface", accepted.confidence
                mention.metadata = {**template.metadata, "method": "propagated", "matched_surface": accepted.surface_text}
                covered[sentence].append((start, stop))
                added.append(mention)
                break
    return mentions + added
