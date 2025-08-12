from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class RfmFrame:
    name: str
    width: int
    height: int
    raw_tail: str = ""  # everything after the first 3 tokens, e.g. 'border 40 0 clear backfill clear'
    page: Optional[str] = None  # name or filename without/with .rmf
    # Parsed decorations
    border_width: Optional[int] = None
    border_line_width: Optional[int] = None
    border_line_color: Optional[str] = None  # raw token (hex or name)
    backfill_color: Optional[str] = None  # raw token (hex or name)
    tail_extra: str = ""  # tail minus parsed border/backfill
    # Ephemeral layout position for preview rendering only
    preview_pos: Tuple[int, int] = (0, 0)

    def to_tag_str(self) -> str:
        # Minimal normalized reconstruction preserving tail
        parts = []
        if self.page:
            parts.append(f"page {self.page}")
        if self.border_width is not None and self.border_line_width is not None and self.border_line_color is not None:
            parts.append(
                f"border {self.border_width} {self.border_line_width} {self.border_line_color}"
            )
        # backfill is only emitted if border exists (matches engine quirk)
        if parts and self.backfill_color:
            parts.append(f"backfill {self.backfill_color}")
        if self.tail_extra.strip():
            parts.append(self.tail_extra.strip())
        tail_combined = " ".join(p for p in parts if p)
        tail = f" {tail_combined}" if tail_combined else ""
        return f"<frame {self.name} {self.width} {self.height}{tail}>"


@dataclass
class RfmElement:
    name: str
    raw_tag: str  # original tag including <>
    segment_index: int = -1

    # Optional parsed fields for a small subset we support editing/viewing
    text_content: Optional[str] = None
    image_path: Optional[str] = None

    def summary(self) -> str:
        if self.name == "text" and self.text_content is not None:
            return self.text_content
        if self.name == "image" and self.image_path is not None:
            return self.image_path
        return self.raw_tag[1:-1][:80]


@dataclass
class RfmDocument:
    # in-order segments preserve raw formatting and unknown tags/text
    # Each segment is (kind, value), where kind in {"tag", "text"}
    segments: List[Tuple[str, str]] = field(default_factory=list)

    # Mappings and collections for convenience
    frames: Dict[str, RfmFrame] = field(default_factory=dict)
    elements: List[RfmElement] = field(default_factory=list)

    # For updating tags during serialization
    frame_segment_indices: Dict[str, int] = field(default_factory=dict)

    # Backdrop (single, last takes precedence)
    backdrop_segment_index: Optional[int] = None
    backdrop_mode: Optional[str] = None  # tile|stretch|center|left|right
    backdrop_image: Optional[str] = None
    backdrop_bgcolor: Optional[str] = None  # raw token (expects ARGB like 0xffrrggbb or #aarrggbb)

    # File association
    file_path: Optional[str] = None  # absolute path
    doc_key: Optional[str] = None  # stable key (file_path or synthetic)


