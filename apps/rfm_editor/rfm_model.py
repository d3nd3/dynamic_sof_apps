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
    cpage_cvar: Optional[str] = None  # cvar name to resolve page at runtime
    cut_from: Optional[str] = None  # frame name this frame is cut from
    # Parsed decorations
    border_width: Optional[int] = None
    border_line_width: Optional[int] = None
    border_line_color: Optional[str] = None  # raw token (hex or name)
    backfill_color: Optional[str] = None  # raw token (hex or name)
    cursor: Optional[int] = None  # 0 or 1
    tail_extra: str = ""  # tail minus parsed border/backfill
    # Ephemeral layout position for preview rendering only
    preview_pos: Tuple[int, int] = (0, 0)

    def to_tag_str(self) -> str:
        # Minimal normalized reconstruction preserving tail
        parts = []
        if self.page:
            parts.append(f"page {self.page}")
        if self.cpage_cvar:
            parts.append(f"cpage {self.cpage_cvar}")
        if self.cut_from:
            parts.append(f"cut {self.cut_from}")
        if self.border_width is not None and self.border_line_width is not None and self.border_line_color is not None:
            parts.append(
                f"border {self.border_width} {self.border_line_width} {self.border_line_color}"
            )
        # backfill is only emitted if border exists (matches engine quirk)
        if self.backfill_color:
            parts.append(f"backfill {self.backfill_color}")
        if self.cursor is not None:
            parts.append(f"cursor {self.cursor}")
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
    # Image attributes
    tint: Optional[str] = None
    atint: Optional[str] = None
    bolt: Optional[str] = None
    bbolt: Optional[str] = None
    key_name: Optional[str] = None
    key_command: Optional[str] = None
    tip_text: Optional[str] = None
    # AText and image text overlay
    atext: Optional[str] = None
    overlay_text: Optional[str] = None
    overlay_xoff: Optional[int] = None
    overlay_yoff: Optional[int] = None
    # List area fields
    list_items: Optional[list[str]] = None
    list_match: Optional[list[str]] = None
    list_bitmask: Optional[int] = None
    list_files_root: Optional[str] = None
    list_files_base: Optional[str] = None
    list_files_ext: Optional[str] = None
    # Common area attributes
    btint: Optional[str] = None
    ctint: Optional[str] = None
    dtint: Optional[str] = None
    noshade: bool = False
    noscale: bool = False
    noborder: bool = False
    area_border_width: Optional[int] = None
    area_border_line_width: Optional[int] = None
    area_border_line_color: Optional[str] = None
    width_px: Optional[int] = None
    height_px: Optional[int] = None
    next_cmd: Optional[str] = None
    prev_cmd: Optional[str] = None
    cvar: Optional[str] = None
    cvari: Optional[str] = None
    inc: Optional[str] = None
    mod: Optional[str] = None
    xoff: Optional[int] = None
    yoff: Optional[int] = None
    tab: bool = False
    align: Optional[str] = None  # left|right|center
    # ckey/ikey
    ckey_var: Optional[str] = None
    ckey_false_command: Optional[str] = None
    ckey_true_command: Optional[str] = None
    ikey_action: Optional[str] = None
    ikey_command: Optional[str] = None
    # Conditional flags (raw capture)
    conditions: Dict[str, list[str]] = field(default_factory=dict)
    # Model-related (bghoul/ghoul)
    model_name: Optional[str] = None
    scale_val: Optional[float] = None
    time_val: Optional[float] = None

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


