"""
LegacyDocParser — best-effort support for legacy binary .doc files.

There is no good pure-Python parser for the pre-2007 binary Word format.
This shells out to LibreOffice headless (`soffice --convert-to txt`) if
installed, converting to plain text and reusing PlainTextParser on the
result. If LibreOffice isn't on PATH, is_available() returns False with a
clear unavailable_reason — the format registers but never silently fails.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import List

from core.models.extraction_models import DocumentBlock
from core.services.extraction.formats.plaintext_parser import PlainTextParser

_PARSER_PROVIDER = "legacy_doc_parser"
UNAVAILABLE_REASON = "Legacy .doc support requires LibreOffice (soffice) installed on PATH"
_CONVERT_TIMEOUT_SECONDS = 60


def _find_soffice() -> str:
    return shutil.which("soffice") or shutil.which("libreoffice") or ""


class LegacyDocParser:
    provider_id = _PARSER_PROVIDER
    unavailable_reason = UNAVAILABLE_REASON

    @staticmethod
    def is_available() -> bool:
        return bool(_find_soffice())

    def parse(self, path: str, doc_id: str) -> List[DocumentBlock]:
        binary = _find_soffice()
        if not binary:
            return []
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                subprocess.run(
                    [binary, "--headless", "--convert-to", "txt:Text", "--outdir", tmp_dir, path],
                    check=True, capture_output=True, timeout=_CONVERT_TIMEOUT_SECONDS,
                )
            except Exception:
                return []
            base = os.path.splitext(os.path.basename(path))[0]
            txt_path = os.path.join(tmp_dir, base + ".txt")
            if not os.path.isfile(txt_path):
                return []
            return PlainTextParser().parse(txt_path, doc_id)
