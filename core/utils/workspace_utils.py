import os

from core.models.workspace_models import EdgeModel, NodeModel
from typing import Tuple


def normalize_annotation_text(annotation: dict) -> tuple[str, str]:
    quote = annotation.get("subject") or annotation.get("text_content") or ""
    note = annotation.get("content") or annotation.get("note_text") or ""
    return quote, note


def compute_node_dimensions(quote: str, note: str) -> tuple[int, int]:
    length = len((quote or "") + (note or ""))
    if length < 50:
        return 200, 70
    if length < 150:
        return 250, 110
    return 300, 160


def truncate_display_name(name: str, max_length: int = 18) -> str:
    if not name:
        return ""
    if len(name) <= max_length:
        return name
    return f"{name[:max_length-1]}\u2026"


def build_pdf_display_name(pdf_path: str, max_length: int = 18) -> str:
    return truncate_display_name(os.path.basename(pdf_path or ""), max_length)


def format_highlight_item_label(highlight: dict) -> str:
    text_content = (highlight.get("text_content") or "[Empty Highlight]").strip()
    doc_name = os.path.basename(highlight.get("doc_id") or "Unknown PDF")
    page_num = highlight.get("page_num")
    page_label = f"Pg {page_num + 1}" if isinstance(page_num, int) else "Unknown Page"
    return f"{doc_name} - {page_label}: {text_content}"


def node_model_from_node(node, workspace_id: int) -> NodeModel:
    return NodeModel(
        id=node.node_id,
        quote=node.quote,
        note=node.note,
        color=node.color,
        is_custom=getattr(node, 'is_custom', False),
        x=node.pos().x(),
        y=node.pos().y(),
        width=getattr(node, 'base_width', 0),
        height=getattr(node, 'base_height', 0),
        highlight_id=getattr(node, 'highlight_id', None),
        workspace_id=workspace_id,
        pdf_path=getattr(node, 'pdf_path', None),
        page_num=getattr(node, 'page_num', None),
        manual_font_size=getattr(node, 'manual_font_size', None),
        node_origin=getattr(node, 'node_origin', 'human'),
        is_verified=int(getattr(node, 'is_verified', 0)),
        original_text=getattr(node, 'original_text', getattr(node, 'note', '')),
        node_type_id=getattr(node, 'node_type_id', ''),
    )


def edge_model_from_edge(edge) -> EdgeModel:
    return EdgeModel(
        id=getattr(edge, 'edge_id', ''),
        source=edge.source_node.node_id,
        target=edge.dest_node.node_id,
        label=edge.label_text,
        color=edge.base_color.name(),
        weight=getattr(edge, 'weight', 0),
    )
