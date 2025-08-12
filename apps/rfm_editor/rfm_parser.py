from __future__ import annotations

import re
from typing import List, Tuple

from .rfm_model import RfmDocument, RfmElement, RfmFrame


STM_OPEN = re.compile(r"<\s*stm\s*>", re.IGNORECASE)
STM_CLOSE = re.compile(r"<\s*/\s*stm\s*>", re.IGNORECASE)


def _tokenize(content: str) -> List[Tuple[str, str]]:
    tokens: List[Tuple[str, str]] = []
    i = 0
    n = len(content)
    inside_stm = False
    has_stm = bool(STM_OPEN.search(content))
    while i < n:
        if content[i] == "<":
            j = i + 1
            in_quote = False
            while j < n:
                c = content[j]
                if c == '"':
                    in_quote = not in_quote
                if c == '>' and not in_quote:
                    j += 1
                    break
                j += 1
            tag = content[i:j]
            low = tag.lower().strip()
            if low == "<stm>":
                inside_stm = True
            elif low == "</stm>":
                inside_stm = False
            else:
                if inside_stm or not has_stm:
                    tokens.append(("tag", tag))
            i = j
        else:
            j = i
            while j < n and content[j] != "<":
                j += 1
            text = content[i:j]
            if text and (inside_stm or not has_stm):
                tokens.append(("text", text))
            i = j
    return tokens


def parse_rfm_content(content: str, file_path: str | None = None) -> RfmDocument:
    tokens = _tokenize(content)
    doc = RfmDocument(segments=tokens, file_path=file_path, doc_key=file_path or "<memory>")

    # Pass 1: collect frames and simple elements for outline/preview
    for idx, (kind, value) in enumerate(tokens):
        if kind != "tag":
            continue
        inner = value[1:-1].strip()
        if not inner:
            continue
        name, *rest = inner.split()
        lname = name.lower()

        if lname == "frame" and len(rest) >= 3:
            frame_name = rest[0]
            try:
                width = int(rest[1])
                height = int(rest[2])
            except ValueError:
                continue
            tail_tokens = rest[3:]
            frame = RfmFrame(name=frame_name, width=width, height=height)
            # Parse supported tail bits: border/backfill
            j = 0
            consumed = [False] * len(tail_tokens)
            while j < len(tail_tokens):
                tok = tail_tokens[j].lower()
                if tok == "border" and j + 3 < len(tail_tokens):
                    try:
                        frame.border_width = int(tail_tokens[j + 1])
                        frame.border_line_width = int(tail_tokens[j + 2])
                        frame.border_line_color = tail_tokens[j + 3]
                        consumed[j] = consumed[j + 1] = consumed[j + 2] = consumed[j + 3] = True
                        j += 4
                        continue
                    except ValueError:
                        pass
                if tok == "backfill" and j + 1 < len(tail_tokens):
                    frame.backfill_color = tail_tokens[j + 1]
                    consumed[j] = consumed[j + 1] = True
                    j += 2
                    continue
                if tok == "page" and j + 1 < len(tail_tokens):
                    frame.page = tail_tokens[j + 1].strip('"')
                    consumed[j] = consumed[j + 1] = True
                    j += 2
                    continue
                j += 1
            frame.raw_tail = " ".join(tail_tokens)
            extras = [t for t, c in zip(tail_tokens, consumed) if not c]
            frame.tail_extra = " ".join(extras)
            doc.frames[frame_name] = frame
            doc.frame_segment_indices[frame_name] = idx
            continue

        # Small subset of elements for preview
        elem = RfmElement(name=lname, raw_tag=value, segment_index=idx)
        if lname == "text" and len(rest) >= 1:
            # capture quoted or bare
            m = re.search(r'\btext\b\s+"([^"]*)"', inner, flags=re.IGNORECASE)
            if m:
                elem.text_content = m.group(1)
            else:
                # try first token after name
                if rest:
                    elem.text_content = rest[0].strip('"')
        elif lname == "image" and len(rest) >= 1:
            elem.image_path = rest[0].strip('"')

        # Limit to renderable ones for now
        if lname in {"text", "image", "hr", "blank"}:
            doc.elements.append(elem)

        # Backdrop
        if lname == "backdrop":
            # Simplified parse: a sequence of tokens, modes are flags until a non-flag = image
            tokens = rest[:]
            mode = None
            image = None
            bgcolor = None
            k = 0
            while k < len(tokens):
                t = tokens[k].lower()
                if t in {"tile", "stretch", "center", "left", "right"}:
                    mode = t
                    k += 1
                    continue
                if t == "bgcolor" and k + 1 < len(tokens):
                    bgcolor = tokens[k + 1]
                    k += 2
                    continue
                # First non-flag token is image path (optional)
                if image is None:
                    image = tokens[k].strip('"')
                k += 1
            doc.backdrop_segment_index = idx
            doc.backdrop_mode = mode
            doc.backdrop_image = image
            doc.backdrop_bgcolor = bgcolor

    return doc


