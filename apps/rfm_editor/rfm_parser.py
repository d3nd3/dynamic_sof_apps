from __future__ import annotations

import re
from typing import List, Tuple
from pathlib import Path

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


def _resolve_include_path(target: str, base_dir: Path) -> Path | None:
    """Resolve an <include X> target to an absolute file path.

    Resolution strategy:
    - Absolute path as-is (with optional .rmf if no extension)
    - base_dir / target (with optional .rmf if no extension)
    """
    t = target.strip().strip('"')
    raw = Path(t)
    candidates: List[Path] = []
    # Helper to add with/without .rmf
    def add_variants(p: Path) -> None:
        if p.suffix:
            candidates.append(p)
        else:
            candidates.append(p.with_suffix(".rmf"))
            candidates.append(p)

    if raw.is_absolute():
        add_variants(raw)
    else:
        add_variants(base_dir / raw)
    for c in candidates:
        try:
            # Resolve symlinks and normalize
            rp = c.resolve()
            if rp.exists() and rp.is_file():
                return rp
        except Exception:
            continue
    return None


def _expand_includes(tokens: List[Tuple[str, str]], base_dir: Path, seen: set[Path] | None = None) -> List[Tuple[str, str]]:
    """Inline <include target> by replacing the tag with the referenced file's tokens.

    - Recurses into nested includes
    - Skips expansion on cycles (already-seen paths)
    - Leaves the original <include> tag untouched if resolution or read fails
    """
    seen = seen or set()
    out: List[Tuple[str, str]] = []
    for kind, value in tokens:
        if kind == "tag":
            inner = value[1:-1].strip()
            if inner:
                parts = inner.split()
                if parts and parts[0].lower() == "include" and len(parts) >= 2:
                    target = parts[1].strip('"')
                    resolved = _resolve_include_path(target, base_dir)
                    if resolved and resolved not in seen:
                        try:
                            text = resolved.read_text(encoding="utf-8", errors="ignore")
                            sub_tokens = _tokenize(text)
                            # Recurse with the included file's directory and updated seen set
                            out.extend(_expand_includes(sub_tokens, resolved.parent, seen | {resolved}))
                            continue  # replaced this <include> tag
                        except Exception:
                            pass
        out.append((kind, value))
    return out


def parse_rfm_content(content: str, file_path: str | None = None) -> RfmDocument:
    tokens = _tokenize(content)
    # Expand <include> tags in-place before building the model
    try:
        base_dir = Path(file_path).parent if file_path else Path.cwd()
    except Exception:
        base_dir = Path.cwd()
    tokens = _expand_includes(tokens, base_dir)
    doc = RfmDocument(segments=tokens, file_path=file_path, doc_key=file_path or "<memory>")

    # Pass 1: collect frames and simple elements for outline/preview
    for idx, (kind, value) in enumerate(tokens):
        # Allow free text between tags to behave like <text ...>
        if kind == "text":
            try:
                s = value
                # Collapse whitespace (including newlines) and strip
                s = re.sub(r"\s+", " ", s).strip()
                # Remove surrounding quotes if the whole token is quoted
                if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
                    s = s[1:-1]
                if s:
                    elem = RfmElement(name="text", raw_tag=value, segment_index=idx)
                    elem.text_content = s
                    doc.elements.append(elem)
            except Exception:
                pass
            continue
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
            # Parse supported tail bits: page/border/backfill/cut/cursor
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
                if tok == "cut" and j + 1 < len(tail_tokens):
                    frame.cut_from = tail_tokens[j + 1]
                    consumed[j] = consumed[j + 1] = True
                    j += 2
                    continue
                if tok == "cursor" and j + 1 < len(tail_tokens):
                    try:
                        frame.cursor = int(tail_tokens[j + 1])
                        consumed[j] = consumed[j + 1] = True
                        j += 2
                        continue
                    except ValueError:
                        pass
                if tok == "page" and j + 1 < len(tail_tokens):
                    frame.page = tail_tokens[j + 1].strip('"')
                    consumed[j] = consumed[j + 1] = True
                    j += 2
                    continue
                if tok == "cpage" and j + 1 < len(tail_tokens):
                    frame.cpage_cvar = tail_tokens[j + 1].strip('"')
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

        # Small subset of elements for preview + capture basic layout state changes
        elem = RfmElement(name=lname, raw_tag=value, segment_index=idx)

        # Helper to parse common area attributes from a token list starting at index s
        def _apply_common_area_attrs(elem: RfmElement, tokens: list[str], s: int) -> None:
            k2 = s
            while k2 < len(tokens):
                t2 = tokens[k2].lower()
                if t2 == "tint" and k2 + 1 < len(tokens):
                    elem.tint = tokens[k2 + 1]
                    k2 += 2; continue
                if t2 == "atint" and k2 + 1 < len(tokens):
                    elem.atint = tokens[k2 + 1]
                    k2 += 2; continue
                if t2 == "btint" and k2 + 1 < len(tokens):
                    elem.btint = tokens[k2 + 1]
                    k2 += 2; continue
                if t2 == "ctint" and k2 + 1 < len(tokens):
                    elem.ctint = tokens[k2 + 1]
                    k2 += 2; continue
                if t2 == "dtint" and k2 + 1 < len(tokens):
                    elem.dtint = tokens[k2 + 1]
                    k2 += 2; continue
                if t2 == "bolt" and k2 + 1 < len(tokens):
                    elem.bolt = tokens[k2 + 1].strip('"')
                    k2 += 2; continue
                if t2 == "bbolt" and k2 + 1 < len(tokens):
                    elem.bbolt = tokens[k2 + 1].strip('"')
                    k2 += 2; continue
                if t2 == "key" and k2 + 2 < len(tokens):
                    elem.key_name = tokens[k2 + 1]
                    elem.key_command = tokens[k2 + 2].strip('"')
                    k2 += 3; continue
                if t2 == "ckey" and k2 + 3 < len(tokens):
                    elem.ckey_var = tokens[k2 + 1]
                    elem.ckey_false_command = tokens[k2 + 2].strip('"')
                    elem.ckey_true_command = tokens[k2 + 3].strip('"')
                    k2 += 4; continue
                if t2 == "ikey" and k2 + 2 < len(tokens):
                    elem.ikey_action = tokens[k2 + 1]
                    elem.ikey_command = tokens[k2 + 2].strip('"')
                    k2 += 3; continue
                if t2 == "tip" and k2 + 1 < len(tokens):
                    elem.tip_text = tokens[k2 + 1].strip('"')
                    k2 += 2; continue
                if t2 == "noshade":
                    elem.noshade = True; k2 += 1; continue
                if t2 == "noscale":
                    elem.noscale = True; k2 += 1; continue
                if t2 == "noborder":
                    elem.noborder = True; k2 += 1; continue
                if t2 == "border" and k2 + 3 < len(tokens):
                    try:
                        elem.area_border_width = int(tokens[k2 + 1])
                        elem.area_border_line_width = int(tokens[k2 + 2])
                        elem.area_border_line_color = tokens[k2 + 3]
                        k2 += 4; continue
                    except ValueError:
                        pass
                if t2 == "width" and k2 + 1 < len(tokens):
                    try:
                        elem.width_px = int(tokens[k2 + 1]); k2 += 2; continue
                    except ValueError:
                        pass
                if t2 == "height" and k2 + 1 < len(tokens):
                    try:
                        elem.height_px = int(tokens[k2 + 1]); k2 += 2; continue
                    except ValueError:
                        pass
                if t2 == "next" and k2 + 1 < len(tokens):
                    elem.next_cmd = tokens[k2 + 1].strip('"'); k2 += 2; continue
                if t2 == "prev" and k2 + 1 < len(tokens):
                    elem.prev_cmd = tokens[k2 + 1].strip('"'); k2 += 2; continue
                if t2 == "cvar" and k2 + 1 < len(tokens):
                    elem.cvar = tokens[k2 + 1]; k2 += 2; continue
                if t2 == "cvari" and k2 + 1 < len(tokens):
                    elem.cvari = tokens[k2 + 1]; k2 += 2; continue
                if t2 == "inc" and k2 + 1 < len(tokens):
                    elem.inc = tokens[k2 + 1]; k2 += 2; continue
                if t2 == "mod" and k2 + 1 < len(tokens):
                    elem.mod = tokens[k2 + 1]; k2 += 2; continue
                if t2 == "xoff" and k2 + 1 < len(tokens):
                    try:
                        elem.xoff = int(tokens[k2 + 1]); k2 += 2; continue
                    except ValueError:
                        pass
                if t2 == "yoff" and k2 + 1 < len(tokens):
                    try:
                        elem.yoff = int(tokens[k2 + 1]); k2 += 2; continue
                    except ValueError:
                        pass
                if t2 == "tab": elem.tab = True; k2 += 1; continue
                if t2 == "align" and k2 + 1 < len(tokens):
                    elem.align = tokens[k2 + 1].lower(); k2 += 2; continue
                if t2 in {"iflt","ifgt","ifle","ifge","ifne","ifeq","ifset","ifclr"}:
                    vals: list[str] = []
                    if k2 + 1 < len(tokens):
                        vals.append(tokens[k2 + 1]); k2 += 2
                    else:
                        k2 += 1
                    elem.conditions.setdefault(t2, []).extend(vals)
                    continue
                k2 += 1
        if lname == "text" and len(rest) >= 1:
            # capture quoted or bare
            m = re.search(r'\btext\b\s+"([^"]*)"', inner, flags=re.IGNORECASE)
            if m:
                elem.text_content = m.group(1)
            else:
                # try first token after name
                if rest:
                    # For bare text until a known attribute keyword, join tokens up to first recognized attr
                    stop_at = {"tint","atint","btint","ctint","dtint","bolt","bbolt","key","ckey","ikey","tip","noshade","noscale","noborder","border","width","height","next","prev","cvar","cvari","inc","mod","xoff","yoff","tab","align"}
                    collected: list[str] = []
                    for tok in rest:
                        if tok.lower() in stop_at:
                            break
                        collected.append(tok)
                    elem.text_content = " ".join(s.strip('"') for s in collected) if collected else rest[0].strip('"')
            # Support atext as prefix text
            m2 = re.search(r'\batext\b\s+"([^"]*)"', inner, flags=re.IGNORECASE)
            if m2:
                elem.atext = m2.group(1)
            # Apply common attributes (skip text value)
            # Shift index by number of consumed value tokens (collected)
            consumed = 1
            try:
                consumed = max(1, len(collected))
            except Exception:
                consumed = 1
            _apply_common_area_attrs(elem, rest, consumed)
        elif lname == "image" and len(rest) >= 1:
            # first arg could be quoted or bare; allow missing extension (e.g., weapons/w_shotgun)
            arg0 = rest[0].strip('"')
            elem.image_path = arg0
            # Parse common attributes in a simple sequential pass (applies to most area types)
            _apply_common_area_attrs(elem, rest, 1)
            # Parse overlay text on images: text <string> <xoff> <yoff>
            mimg = re.search(r'\btext\b\s+"([^"]*)"\s+(-?\d+)\s+(-?\d+)', inner, flags=re.IGNORECASE)
            if mimg:
                elem.overlay_text = mimg.group(1)
                try:
                    elem.overlay_xoff = int(mimg.group(2))
                    elem.overlay_yoff = int(mimg.group(3))
                except ValueError:
                    pass
        elif lname == "ctext" and len(rest) >= 1:
            # First argument is a cvar name (not a literal text)
            m = re.search(r'\bctext\b\s+"([^"]*)"', inner, flags=re.IGNORECASE)
            if m:
                elem.cvar = m.group(1)
            else:
                elem.cvar = rest[0].strip('"')
            _apply_common_area_attrs(elem, rest, 1)
        elif lname == "ticker" and len(rest) >= 1:
            # Try to read quoted text for ticker
            m = re.search(r'\bticker\b\s+"([^"]*)"', inner, flags=re.IGNORECASE)
            if m:
                elem.text_content = m.group(1)
            else:
                if rest:
                    elem.text_content = rest[0].strip('"')
            _apply_common_area_attrs(elem, rest, 1)
        elif lname in {"hr", "hbr", "br", "blank", "list", "slider", "input", "setkey", "popup", "selection", "ghoul", "gpm", "filebox", "filereq", "loadbox", "serverbox", "serverdetail", "players", "listfile", "users", "chat", "rooms", "bghoul"}:
            # Parse common attributes for these areas
            _apply_common_area_attrs(elem, rest, 0)
            # Capture minimal model props for ghoul/bghoul
            if lname in {"ghoul", "bghoul"} and rest:
                # First token after name is model (quoted or bare)
                elem.model_name = rest[0].strip('"')
                # Scan for scale/time
                for i in range(1, len(rest)):
                    t = rest[i].lower()
                    if t == "scale" and i + 1 < len(rest):
                        try:
                            elem.scale_val = float(rest[i + 1])
                        except ValueError:
                            pass
                    if t == "time" and i + 1 < len(rest):
                        try:
                            elem.time_val = float(rest[i + 1])
                        except ValueError:
                            pass
            # list specifics
            if lname == "list" and rest:
                # items list may be quoted and comma separated
                mlist = re.search(r'\blist\b\s+"([^"]*)"', inner, flags=re.IGNORECASE)
                if mlist:
                    elem.list_items = [s.strip() for s in mlist.group(1).split(',')]
                # match list
                mmatch = re.search(r'\bmatch\b\s+"([^"]*)"', inner, flags=re.IGNORECASE)
                if mmatch:
                    elem.list_match = [s.strip() for s in mmatch.group(1).split(',')]
                # bitmask
                mbit = re.search(r'\bbitmask\b\s+(\d+)', inner, flags=re.IGNORECASE)
                if mbit:
                    try:
                        elem.list_bitmask = int(mbit.group(1))
                    except ValueError:
                        pass
                # files root base ext
                mfiles = re.search(r'\bfiles\b\s+"([^"]*)"\s+"([^"]*)"\s+"([^"]*)"', inner, flags=re.IGNORECASE)
                if mfiles:
                    elem.list_files_root = mfiles.group(1)
                    elem.list_files_base = mfiles.group(2)
                    elem.list_files_ext = mfiles.group(3)

        # Extend: include center/left/right/normal (layout), ctext, font (as a mode marker), include, ticker, bghoul
        # For now these are displayed in the outline and minimally rendered where applicable
        if lname in {"text", "ctext", "image", "hr", "blank", "center", "left", "right", "normal", "font", "include", "ticker", "bghoul"}:
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


