from __future__ import annotations

from typing import List

from .rfm_model import RfmDocument


def serialize_rfm(doc: RfmDocument) -> str:
    # Re-emit tokens, with any edited frame tags normalized to include width/height changes
    output_parts: List[str] = []

    for idx, (kind, value) in enumerate(doc.segments):
        if kind == "text":
            output_parts.append(value)
            continue
        # kind == 'tag'
        inner = value[1:-1].strip()
        low = inner.lower()
        if low.startswith("frame "):
            # Identify which frame this is by segment index
            frame_obj = None
            for name, seg_idx in doc.frame_segment_indices.items():
                if seg_idx == idx:
                    frame_obj = doc.frames.get(name)
                    break
            if frame_obj is not None:
                output_parts.append(frame_obj.to_tag_str())
                continue
        if low.startswith("backdrop") and doc.backdrop_segment_index == idx:
            # Rebuild backdrop from model
            parts: List[str] = ["<backdrop"]
            if doc.backdrop_mode:
                parts.append(doc.backdrop_mode)
            if doc.backdrop_image:
                img = doc.backdrop_image
                parts.append(img if (img and ' ' not in img) else f'"{img}"')
            if doc.backdrop_bgcolor:
                parts.append("bgcolor")
                parts.append(doc.backdrop_bgcolor)
            parts.append(">")
            output_parts.append(" ".join(parts))
            continue
        # default: original (preserve exinclude and include tags as-is)
        output_parts.append(value)

    # Wrap inside <stm>..</stm> since tokenizer dropped the wrappers for logical editing
    return "<stm>\n" + "".join(output_parts) + "\n</stm>\n"


