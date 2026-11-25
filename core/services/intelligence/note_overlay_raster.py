"""Bounded transparent overlay images; all dense geometry work stays off Qt."""
from io import BytesIO
import fitz
from PIL import Image, ImageDraw, ImageColor

MAX_OVERLAY_SIDE = 1024


def rasterize(service, source_path, page_indices):
    rows = service.overlays(source_path, page_indices)
    by_page = {}
    for row in rows:
        by_page.setdefault(row['page'], []).append(row)
    output = []
    with fitz.open(source_path) as document:
        for index in page_indices:
            page = document[index]
            width, height = page.rect.width, page.rect.height
            scale = min(1, MAX_OVERLAY_SIDE / max(width, height))
            image = Image.new('RGBA', (max(1, round(width*scale)), max(1, round(height*scale))))
            draw = ImageDraw.Draw(image)
            for row in by_page.get(index, []):
                color = (*ImageColor.getrgb(row.get('color') or '#b366ff'), 75)
                for rect in row.get('rects') or []:
                    draw.rectangle(tuple(value*scale for value in rect), fill=color)
            stream = BytesIO()
            image.save(stream, format='PNG')
            output.append({'page': index, 'png': stream.getvalue(), 'width': width, 'height': height})
    return output
