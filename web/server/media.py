from __future__ import annotations

import os
import re

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

_RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


def ranged_file(request: Request, path: str, media_type: str, filename: str):
    size = os.path.getsize(path)
    value = request.headers.get("range")
    headers = {"Accept-Ranges": "bytes", "Content-Disposition": f'inline; filename="{filename.replace(chr(34), "")}"'}
    if not value:
        start, end, status = 0, max(size - 1, 0), 200
    else:
        match = _RANGE.fullmatch(value.strip())
        if not match:
            raise HTTPException(416, "Invalid byte range", headers={"Content-Range": f"bytes */{size}"})
        left, right = match.groups()
        if not left and not right:
            raise HTTPException(416, "Invalid byte range")
        if not left:
            length = min(int(right), size)
            start, end = size - length, size - 1
        else:
            start = int(left)
            end = min(int(right), size - 1) if right else size - 1
        if start >= size or end < start:
            raise HTTPException(416, "Range outside file", headers={"Content-Range": f"bytes */{size}"})
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    length = max(0, end - start + 1)
    headers["Content-Length"] = str(length)

    async def chunks():
        with open(path, "rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining:
                block = handle.read(min(1024 * 256, remaining))
                if not block:
                    break
                remaining -= len(block)
                yield block

    return StreamingResponse(chunks(), status_code=status, media_type=media_type, headers=headers)
