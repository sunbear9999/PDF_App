"""Chapter labels shared by body headings and note subheadings."""
import re

_WORDS = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split()
_NUMBERS = {word: str(index) for index, word in enumerate(_WORDS)}
_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}
CHAPTER = re.compile(r"(?i)^(?:notes\s+(?:to|for)\s+)?(?:chapter|chapitre|capítulo|kapitel)\s+([\w-]+)\b")


def chapter_scope(text):
    match = CHAPTER.match(text.strip())
    if not match:
        return ""
    label = match[1].casefold()
    if label in _NUMBERS:
        label = _NUMBERS[label]
    elif label.startswith("twenty-") and label[7:] in _NUMBERS:
        label = str(20 + int(_NUMBERS[label[7:]]))
    elif re.fullmatch("[ivxlc]+", label):
        values = [_ROMAN[c] for c in label]
        label = str(sum(-n if i+1 < len(values) and n < values[i+1] else n for i, n in enumerate(values)))
    return "chapter:" + label
