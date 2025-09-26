from __future__ import annotations

from typing import Tuple, Callable, Optional
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import (
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
)

from .rfm_model import RfmDocument, RfmElement, RfmFrame


class RfmRenderer:
    def __init__(self) -> None:
        self.frame_pen = QPen(QColor(80, 160, 255, 255))
        self.frame_brush = QBrush(QColor(80, 160, 255, 40))
        self.content_pen = QPen(QColor(240, 240, 240, 255))
        # Legacy maps (name/segment_index) kept for backward compatibility
        self.frame_rects: dict[str, QRectF] = {}
        self.element_rects: dict[int, QRectF] = {}
        # Collision-free maps keyed by document key
        # doc_key -> frame_name -> rect
        self.frame_rects_by_doc: dict[str, dict[str, QRectF]] = {}
        # doc_key -> element_segment_index -> rect
        self.element_rects_by_doc: dict[str, dict[int, QRectF]] = {}
        self.content_rect: QRectF = QRectF(0, 0, 0, 0)
        # Screen profile for auto-fill. Width remains 640 across profiles.
        self.max_screen_width: int = 640
        self.max_screen_height: int = 480
        # Roots for resolving relative asset paths
        self.menu_root: str | None = None
        self.resource_root: str | None = None
        # Currently active frame name (for labeling only that frame in WYSIWYG view)
        self.active_frame_name: str | None = None
        # Optional resolver for <page> targets and feature toggle for sub-frame rendering
        self.page_resolver: Optional[Callable[[str, Optional[str]], Optional[RfmDocument]]] = None
        self.subframe_rendering_enabled: bool = False
        # Exinclude render mode and resolver for dynamic expansion
        self.exinclude_mode: str = "zero"  # or "nonzero"
        self.exinclude_parser: Optional[Callable[[str, Optional[str], str], Optional[RfmDocument]]] = None

    def _doc_key_of(self, doc: RfmDocument) -> str:
        try:
            k = getattr(doc, 'doc_key', None)
            if isinstance(k, str) and k:
                return k
        except Exception:
            pass
        try:
            fp = getattr(doc, 'file_path', None)
            if isinstance(fp, str) and fp:
                return fp
        except Exception:
            pass
        return "<memory>"

    def _resolve_image_path(self, path: str) -> str | None:
        # Absolute path that exists
        try:
            norm = path.strip().strip('"')
            # Normalize Windows separators to POSIX-style
            norm = norm.replace("\\", "/")
            p = Path(norm)
            if p.is_absolute() and p.exists():
                return str(p)
        except Exception:
            pass
        # Fetch roots from main window
        # Prefer renderer's configured roots (kept in sync by the main window)
        resource_root = getattr(self, "resource_root", None)
        menu_root = getattr(self, "menu_root", None)
        candidates: list[Path] = []
        raw = Path(norm)
        raw_has_ext = raw.suffix != ""
        subpaths = [raw]
        # If the path already starts with pics/menus, also try as-is under roots
        # Otherwise, also try under pics/menus/
        if not (len(raw.parts) >= 2 and raw.parts[0].lower() == "pics" and raw.parts[1].lower() == "menus"):
            subpaths.append(Path("pics") / "menus" / raw)
        roots = [resource_root, menu_root]
        # If no extension, try likely ones in order
        try_exts = [""] if raw_has_ext else [".m32", ".png", ".jpg", ".jpeg", ".bmp"]
        for root in roots:
            if not root:
                continue
            try:
                r = Path(root)
            except Exception:
                continue
            for sp in subpaths:
                for ext in try_exts:
                    if ext:
                        candidates.append(r / (sp.as_posix() + ext))
                    else:
                        candidates.append(r / sp)
        # Final: try raw path in CWD (with .m32 if missing)
        for ext in try_exts:
            if ext:
                candidates.append(Path(norm + ext))
            else:
                candidates.append(Path(norm))
        for c in candidates:
            try:
                if c.exists():
                    return str(c)
            except Exception:
                continue
        # Case-insensitive directory walk fallback
        def _ci_find(root_dir: Path, rel: Path) -> Path | None:
            try:
                curr = root_dir
                parts = list(rel.parts)
                for i, part in enumerate(parts):
                    entries = list(curr.iterdir())
                    match = None
                    for e in entries:
                        if e.name.lower() == part.lower():
                            match = e
                            break
                    if match is None:
                        return None
                    curr = match
                return curr if curr.exists() else None
            except Exception:
                return None
        for root in roots:
            if not root:
                continue
            base = Path(root)
            for sp in subpaths:
                for ext in try_exts:
                    target = Path(sp.as_posix() + ext) if ext else sp
                    found = _ci_find(base, target)
                    if found is not None and found.exists():
                        return str(found)
        return None

    def render_document(self, doc: RfmDocument, scene: QGraphicsScene) -> None:
        # If the document contains exinclude tags and a parser is provided, create a transient doc expanded for current mode
        try:
            expanded_doc = None
            if self.exinclude_parser and doc and getattr(doc, 'file_path', None):
                # Re-read from serialized current doc to keep edits
                from .rfm_serializer import serialize_rfm
                serialized = serialize_rfm(doc)
                expanded_doc = self.exinclude_parser(serialized, getattr(doc, 'file_path', None), self.exinclude_mode)
            # Fallback if expansion produced an empty/invalid document
            def _is_valid(d) -> bool:
                try:
                    return bool(getattr(d, 'segments', None)) and (len(getattr(d, 'frames', {})) > 0 or len(getattr(d, 'elements', [])) > 0)
                except Exception:
                    return False
            if expanded_doc is not None and _is_valid(expanded_doc):
                working_doc = expanded_doc
            else:
                working_doc = doc
        except Exception:
            working_doc = doc
        # Build parent-child relationships via 'cut' (frames nested inside other frames)
        all_frames = list(working_doc.frames.values())
        frames_by_name = {f.name: f for f in all_frames}
        children_by_parent: dict[str, list[RfmFrame]] = {}
        top_level_frames: list[RfmFrame] = []
        for f in all_frames:
            parent = getattr(f, 'cut_from', None)
            if parent and parent in frames_by_name:
                children_by_parent.setdefault(parent, []).append(f)
            else:
                top_level_frames.append(f)

        # Arrange top-level frames in a vertical stack; nested frames inside their parent's inner rect
        y_cursor = 0.0
        margin = 0.0
        frame_rects: list[tuple[QRectF, RfmFrame]] = []

        def clamp_dims_for_border(w: float, h: float, f: RfmFrame) -> tuple[float, float]:
            bw = float(f.border_width or 0)
            if bw * 2 >= w:
                w = max(1.0, w)
            if bw * 2 >= h:
                h = max(1.0, h)
            return w, h

        def inner_rect_of(rect: QRectF, f: RfmFrame) -> QRectF:
            bw = float(f.border_width or 0)
            inner = rect.adjusted(bw, bw, -bw, -bw)
            if inner.width() <= 0 or inner.height() <= 0:
                return QRectF(rect)
            return inner

        top_level_rects: dict[str, QRectF] = {}
        for f in top_level_frames:
            w_val = float(self.max_screen_width if f.width == 0 else min(f.width, self.max_screen_width))
            h_val = float(self.max_screen_height if f.height == 0 else min(f.height, self.max_screen_height))
            w_val, h_val = clamp_dims_for_border(w_val, h_val, f)
            rect = QRectF(0.0, y_cursor, w_val, h_val)
            f.preview_pos = (int(rect.x()), int(rect.y()))
            frame_rects.append((rect, f))
            top_level_rects[f.name] = rect
            y_cursor += rect.height() + margin

        def layout_children(parent: RfmFrame, parent_rect: QRectF) -> None:
            # Place children left-to-right within parent's inner rect (normal layout)
            container = inner_rect_of(parent_rect, parent)
            x_cursor = float(container.left())
            children = list(children_by_parent.get(parent.name, []) or [])
            for ch in children:
                # Remaining width from current cursor
                available_w = float(container.right() - x_cursor)
                if available_w <= 0:
                    break
                # Width/height rules: 0 means fill remaining/parent respectively
                w_target = available_w if int(getattr(ch, 'width', 0) or 0) == 0 else float(min(int(ch.width), available_w))
                h_target = float(container.height()) if int(getattr(ch, 'height', 0) or 0) == 0 else float(min(int(ch.height), container.height()))
                w_val, h_val = clamp_dims_for_border(max(1.0, w_target), max(1.0, h_target), ch)
                rect = QRectF(x_cursor, float(container.top()), w_val, h_val)
                ch.preview_pos = (int(rect.x()), int(rect.y()))
                frame_rects.append((rect, ch))
                # Recurse for deeper nesting
                layout_children(ch, rect)
                # Advance cursor for next sibling
                x_cursor = float(rect.right())

        for f in top_level_frames:
            layout_children(f, top_level_rects[f.name])

        # Fixed screen content area: always exactly one screen per selected ratio
        screen_rect = QRectF(0, 0, float(self.max_screen_width), float(self.max_screen_height))
        self.content_rect = screen_rect

        # Draw backdrop behind frames
        if working_doc.backdrop_bgcolor:
            bg = self._color_from_token(working_doc.backdrop_bgcolor)
            bg_item = scene.addRect(screen_rect, QPen(Qt.NoPen), QBrush(bg))
            bg_item.setZValue(-100)
        if working_doc.backdrop_image:
            self._draw_backdrop_image(scene, screen_rect, working_doc.backdrop_image, working_doc.backdrop_mode)

        # Now draw frames on top
        # Reset rect caches for a fresh top-level render
        self.frame_rects.clear()
        self.element_rects.clear()
        self.frame_rects_by_doc.clear()
        self.element_rects_by_doc.clear()
        for rect, frame in frame_rects:
            # Draw full frame rect; items outside the screen are naturally clipped by the scene rect
            self._draw_frame(scene, rect, frame)
            self.frame_rects[frame.name] = rect
            try:
                dk = self._doc_key_of(working_doc)
                self.frame_rects_by_doc.setdefault(dk, {})[frame.name] = rect
            except Exception:
                pass

        # If any frame extends beyond the visible screen bottom, draw a stronger bottom fade as a hint
        try:
            extends_below = any(r.bottom() > screen_rect.bottom() for r, _ in frame_rects)
            if extends_below:
                from PySide6.QtGui import QLinearGradient
                fade_h = 28.0
                fade_rect = QRectF(
                    screen_rect.left(),
                    screen_rect.bottom() - fade_h,
                    screen_rect.width(),
                    fade_h,
                )
                grad = QLinearGradient(fade_rect.left(), fade_rect.top(), fade_rect.left(), fade_rect.bottom())
                # Transparent to a stronger dark overlay for clearer indication
                grad.setColorAt(0.0, QColor(0, 0, 0, 0))
                grad.setColorAt(0.5, QColor(0, 0, 0, 140))
                grad.setColorAt(1.0, QColor(0, 0, 0, 220))
                fade_item = scene.addRect(fade_rect, QPen(Qt.NoPen), QBrush(grad))
                fade_item.setZValue(9000)
        except Exception:
            pass

        # Simple content renderer: lay out elements within the first frame rect
        host_rect = frame_rects[0][0] if frame_rects else screen_rect
        self._draw_elements(scene, working_doc, host_rect)

        # Optionally render sub-documents referenced by frame.page into each frame's inner area
        if self.subframe_rendering_enabled:
            try:
                visited: set[str] = set()
                base_key: Optional[str] = getattr(working_doc, 'file_path', None)
                if isinstance(base_key, str) and base_key:
                    visited.add(base_key)
                for rect, frame in frame_rects:
                    page_name = getattr(frame, 'page', None)
                    if not page_name:
                        continue
                    if not self.page_resolver:
                        continue
                    try:
                        subdoc = self.page_resolver(page_name, base_key)
                    except Exception:
                        subdoc = None
                    if not subdoc:
                        continue
                    # Avoid cycles
                    sub_key = getattr(subdoc, 'file_path', None)
                    if isinstance(sub_key, str) and sub_key in visited:
                        continue
                    inner = self._inner_rect_of(rect, frame)
                    if inner.width() <= 0 or inner.height() <= 0:
                        continue
                    self._render_document_into(subdoc, scene, inner, visited | ({sub_key} if isinstance(sub_key, str) and sub_key else set()))
            except Exception:
                pass

    def _draw_frame(self, scene: QGraphicsScene, rect: QRectF, frame: RfmFrame) -> None:
        # Preview outline: only draw when an actual border is present (non-zero, non-clear)
        draw_preview_outline = (
            frame.border_width is not None
            and frame.border_line_width is not None
            and frame.border_line_width > 0
            and frame.border_line_color is not None
            and str(frame.border_line_color).lower() != "clear"
        )
        if draw_preview_outline:
            outer_pen = QPen(QColor(120, 160, 220, 180))
            outer_pen.setWidth(1)
            outer_pen.setCosmetic(True)
            outline_inset = outer_pen.widthF() / 2.0
            outline_rect = rect.adjusted(outline_inset, outline_inset, -outline_inset, -outline_inset)
            if outline_rect.width() > 0 and outline_rect.height() > 0:
                oitem = scene.addRect(outline_rect, outer_pen)
                try:
                    oitem.setZValue(-50)
                except Exception:
                    pass
        else:
            # Fallback: draw a subtle dashed outline so frames are visible even without border/backfill
            try:
                pen = QPen(QColor(120, 160, 220, 110))
                pen.setCosmetic(True)
                pen.setWidth(1)
                pen.setStyle(Qt.PenStyle.DashLine)
                orect = rect.adjusted(0.5, 0.5, -0.5, -0.5)
                if orect.width() > 0 and orect.height() > 0:
                    oitem = scene.addRect(orect, pen)
                    try:
                        oitem.setZValue(-50)
                    except Exception:
                        pass
            except Exception:
                pass

        # Border/backfill
        if frame.border_width is not None and frame.border_line_width is not None and frame.border_line_color is not None:
            bw = max(0, int(frame.border_width))
            inner = rect.adjusted(bw, bw, -bw, -bw)
            if inner.width() > 0 and inner.height() > 0:
                # Backfill: inside the border region
                if frame.backfill_color:
                    fill_color = self._color_from_token(frame.backfill_color)
                    bitem = scene.addRect(inner, QPen(Qt.NoPen), QBrush(fill_color))
                    try:
                        bitem.setZValue(-60)
                    except Exception:
                        pass
                # Border line: centered in the border band
                if frame.border_line_width > 0 and str(frame.border_line_color).lower() != "clear":
                    line_pen = QPen(self._color_from_token(frame.border_line_color))
                    line_pen.setWidth(max(1, frame.border_line_width))
                    line_pen.setCosmetic(True)
                    center = float(bw) / 2.0
                    line_rect = rect.adjusted(center, center, -center, -center)
                    if line_rect.width() > 0 and line_rect.height() > 0:
                        litem = scene.addRect(line_rect, line_pen)
                        try:
                            litem.setZValue(-55)
                        except Exception:
                            pass
        else:
            # No explicit border: still honor backfill if provided by tail
            if getattr(frame, 'backfill_color', None):
                fill_color = self._color_from_token(frame.backfill_color)
                if rect.width() > 0 and rect.height() > 0:
                    bitem = scene.addRect(rect, QPen(Qt.NoPen), QBrush(fill_color))
                    try:
                        bitem.setZValue(-60)
                    except Exception:
                        pass

        # Frame label is drawn by the main window as a top-most overlay; do not draw it here to avoid duplicates

    def _draw_elements(self, scene: QGraphicsScene, doc: RfmDocument, host_rect: QRectF) -> None:
        dk = self._doc_key_of(doc)
        # Layout state
        mode = "normal"  # normal | left | right | center
        cursor_x = host_rect.x() + 12
        cursor_y = host_rect.y() + 28
        line_height = 18.0
        spacing = 8.0
        line_start_x = cursor_x
        line_end_x = host_rect.x() + host_rect.width() - 12

        # Buffer for centered rows: accumulate items until a flush event, then center them as a group
        center_row_items: list[dict] = []
        center_row_max_h: float = 0.0

        def flush_center_row() -> None:
            nonlocal center_row_items, center_row_max_h, cursor_x, cursor_y, line_height
            if not center_row_items:
                return
            total_w = sum(float(it.get("width", 0.0)) for it in center_row_items)
            total_w += spacing * max(0, len(center_row_items) - 1)
            available_w = float(line_end_x - line_start_x)
            # Center within the available band [line_start_x, line_end_x]
            start_x_f = float(line_start_x) + max(0.0, (available_w - total_w)) / 2.0
            # Snap to integer pixel to avoid half-pixel bias
            try:
                start_x = float(int(round(start_x_f)))
            except Exception:
                start_x = start_x_f
            x = start_x
            for it in center_row_items:
                kind = it.get("kind")
                seg_idx = it.get("segment_index")
                if kind == "text":
                    item: QGraphicsSimpleTextItem = it["item"]
                    scene.addItem(item)
                    try:
                        item.setZValue(20000)
                    except Exception:
                        pass
                    item.setPos(QPointF(x, cursor_y))
                    rect = item.mapRectToScene(item.boundingRect())
                    if seg_idx is not None:
                        self.element_rects[int(seg_idx)] = rect
                        try:
                            self.element_rects_by_doc.setdefault(dk, {})[int(seg_idx)] = rect
                        except Exception:
                            pass
                elif kind == "pixmap":
                    pm = it["pixmap"]
                    pm_item = scene.addPixmap(pm)
                    pm_item.setOffset(QPointF(x, cursor_y))
                    try:
                        pm_item.setTransformationMode(Qt.TransformationMode.FastTransformation)
                    except Exception:
                        pass
                    draw_rect = pm_item.mapRectToScene(pm_item.boundingRect())
                    # Overlay text if any
                    ov_text = it.get("overlay_text")
                    if ov_text:
                        t_item = QGraphicsSimpleTextItem(str(ov_text))
                        t_item.setBrush(QColor(210, 210, 210))
                        ox = int(it.get("overlay_xoff") or 0)
                        oy = int(it.get("overlay_yoff") or 0)
                        t_item.setPos(QPointF(x + ox, cursor_y + oy))
                        try:
                            t_item.setZValue(20000)
                        except Exception:
                            pass
                        scene.addItem(t_item)
                    # Bolt marker in top-right corner if present
                    try:
                        if bool(it.get("bolt")):
                            b_item = QGraphicsSimpleTextItem("B")
                            b_item.setBrush(QColor(255, 200, 0))
                            iw = float(it.get("width", pm.width()))
                            bx = x + max(0.0, iw - b_item.boundingRect().width() - 3.0)
                            by = cursor_y + 2.0
                            b_item.setPos(QPointF(bx, by))
                            try:
                                b_item.setZValue(20000)
                            except Exception:
                                pass
                            scene.addItem(b_item)
                    except Exception:
                        pass
                    if seg_idx is not None:
                        self.element_rects[int(seg_idx)] = draw_rect
                        try:
                            if draw_rect is not None:
                                self.element_rects_by_doc.setdefault(dk, {})[int(seg_idx)] = draw_rect
                        except Exception:
                            pass
                elif kind == "image_placeholder":
                    w = float(it.get("width", 80.0))
                    h = float(it.get("height", 32.0))
                    rect_item = scene.addRect(QRectF(x, cursor_y, w, h), QPen(QColor(140, 140, 140)), QBrush(QColor(90, 90, 90)))
                    from pathlib import Path as _P
                    pth = it.get("path") or ""
                    label = scene.addSimpleText((_P(pth).name if pth else "<image>"))
                    label.setBrush(QColor(210, 210, 210))
                    label.setPos(QPointF(x + 6, cursor_y + 6))
                    try:
                        label.setZValue(20000)
                    except Exception:
                        pass
                    # Bolt marker for placeholder
                    try:
                        if bool(it.get("bolt")):
                            b_item = QGraphicsSimpleTextItem("B")
                            b_item.setBrush(QColor(255, 200, 0))
                            bx = x + max(0.0, w - b_item.boundingRect().width() - 3.0)
                            by = cursor_y + 2.0
                            b_item.setPos(QPointF(bx, by))
                            try:
                                b_item.setZValue(20000)
                            except Exception:
                                pass
                            scene.addItem(b_item)
                    except Exception:
                        pass
                    draw_rect = rect_item.mapRectToScene(rect_item.rect())
                    if seg_idx is not None:
                        self.element_rects[int(seg_idx)] = draw_rect
                x += float(it.get("width", 0.0)) + spacing
            # Advance to next line after placing the centered row
            if mode in {"normal", "left"}:
                cursor_x = line_start_x
            elif mode == "right":
                cursor_x = line_end_x
            else:  # center
                cursor_x = (line_start_x + line_end_x) / 2.0
            cursor_y += max(center_row_max_h, line_height) + spacing
            line_height = 18.0
            center_row_items.clear()
            center_row_max_h = 0.0

        def new_line():
            nonlocal cursor_x, cursor_y
            cursor_x = line_start_x
            cursor_y += line_height + spacing

        self.element_rects.clear()
        for elem in doc.elements:
            # Handle layout commands that affect following elements
            if elem.name in {"center", "left", "right", "normal"}:
                # If exiting center mode, flush any pending row first
                if mode == "center" and center_row_items:
                    flush_center_row()
                mode = elem.name
                # Reset X for new mode to current line
                if mode in {"normal", "left"}:
                    cursor_x = line_start_x
                elif mode == "right":
                    cursor_x = line_end_x
                elif mode == "center":
                    # Center mode starts a fresh centered row on a new line
                    new_line()
                    cursor_x = (line_start_x + line_end_x) / 2.0
                continue
            
            if elem.name in {"text", "ctext"}:
                # Use literal text for <text>; for <ctext> show a placeholder since CVars are unknown here
                base_text = elem.text_content if elem.name == "text" else "cvar_content"
                # Prepend atext if present (applies to <text>; harmless for <ctext> if set)
                content = (elem.atext + " " if getattr(elem, 'atext', None) else "") + base_text
                if mode == "center":
                    item: QGraphicsSimpleTextItem = scene.addSimpleText(content)
                    item.setBrush(QColor(240, 240, 240))
                    rect_local = item.boundingRect()
                    w = float(rect_local.width()); h = float(rect_local.height())
                    available_w = float(line_end_x - line_start_x)
                    start_x_f = float(line_start_x) + max(0.0, (available_w - w)) / 2.0
                    try:
                        cx = float(int(round(start_x_f)))
                    except Exception:
                        cx = start_x_f
                    item.setPos(QPointF(cx, cursor_y))
                    try:
                        item.setZValue(20000)
                    except Exception:
                        pass
                    rect = item.mapRectToScene(item.boundingRect())
                    self.element_rects[elem.segment_index] = rect
                    try:
                        self.element_rects_by_doc.setdefault(dk, {})[int(elem.segment_index)] = rect
                    except Exception:
                        pass
                    # Bolt marker for text (center mode)
                    try:
                        if getattr(elem, 'bolt', None) or getattr(elem, 'bbolt', None):
                            b_item = QGraphicsSimpleTextItem("B")
                            b_item.setBrush(QColor(255, 200, 0))
                            bx = rect.right() - b_item.boundingRect().width() - 3.0
                            by = rect.top() + 2.0
                            b_item.setPos(QPointF(bx, by))
                            try:
                                b_item.setZValue(20000)
                            except Exception:
                                pass
                            scene.addItem(b_item)
                    except Exception:
                        pass
                    line_height = max(line_height, h)
                    new_line()
                else:
                    item: QGraphicsSimpleTextItem = scene.addSimpleText(content)
                    item.setBrush(QColor(240, 240, 240))
                    # Measure
                    rect_local = item.boundingRect()
                    w = rect_local.width()
                    h = rect_local.height()
                    # Position based on mode
                    if mode == "right":
                        x = cursor_x - w
                    else:  # normal/left
                        x = cursor_x
                    item.setPos(QPointF(x, cursor_y))
                    try:
                        item.setZValue(20000)
                    except Exception:
                        pass
                    rect = item.mapRectToScene(item.boundingRect())
                    self.element_rects[elem.segment_index] = rect
                    try:
                        self.element_rects_by_doc.setdefault(dk, {})[int(elem.segment_index)] = rect
                    except Exception:
                        pass
                    # Bolt marker for text
                    try:
                        if getattr(elem, 'bolt', None) or getattr(elem, 'bbolt', None):
                            b_item = QGraphicsSimpleTextItem("B")
                            b_item.setBrush(QColor(255, 200, 0))
                            bx = rect.right() - b_item.boundingRect().width() - 3.0
                            by = rect.top() + 2.0
                            b_item.setPos(QPointF(bx, by))
                            try:
                                b_item.setZValue(20000)
                            except Exception:
                                pass
                            scene.addItem(b_item)
                    except Exception:
                        pass
                    # Advance cursor for normal/left/right
                    if mode in {"normal", "left"}:
                        cursor_x = x + w + spacing
                        line_height = max(line_height, h)
                    elif mode == "right":
                        cursor_x = x - spacing
                        line_height = max(line_height, h)
            elif elem.name in {"hr", "hbr", "br"}:
                # If there is a pending centered row, place it first
                if mode == "center" and center_row_items:
                    flush_center_row()
                width = host_rect.width() - 24
                pen = QPen(QColor(180, 180, 180, 200))
                pen.setWidth(1)
                # Compute start X based on mode for br/hbr/hr
                start_x = line_start_x
                if mode == "right":
                    start_x = line_end_x - max(10.0, width)
                elif mode == "center":
                    start_x = (line_start_x + line_end_x - max(10.0, width)) / 2.0
                line: QGraphicsLineItem = scene.addLine(start_x, cursor_y + 4, start_x + max(10.0, width), cursor_y + 4, pen)
                rect = line.mapRectToScene(line.boundingRect())
                self.element_rects[elem.segment_index] = rect
                try:
                    self.element_rects_by_doc.setdefault(dk, {})[int(elem.segment_index)] = rect
                except Exception:
                    pass
                # Bolt marker for rules
                try:
                    if getattr(elem, 'bolt', None) or getattr(elem, 'bbolt', None):
                        b_item = QGraphicsSimpleTextItem("B")
                        b_item.setBrush(QColor(255, 200, 0))
                        bx = rect.right() - b_item.boundingRect().width() - 3.0
                        by = rect.top() + 2.0
                        b_item.setPos(QPointF(bx, by))
                        try:
                            b_item.setZValue(20000)
                        except Exception:
                            pass
                        scene.addItem(b_item)
                except Exception:
                    pass
                # br resets X to left and moves down at next layout change; hbr always below
                new_line()
                if elem.name == "br":
                    # Reset layout baseline x
                    if mode == "right":
                        cursor_x = line_end_x
                    elif mode == "center":
                        cursor_x = (line_start_x + line_end_x) / 2.0
                    else:
                        cursor_x = line_start_x
            elif elem.name == "list":
                # Minimal list rendering: show atext and current/first item
                atext = getattr(elem, 'atext', None) or ""
                items = getattr(elem, 'list_items', None) or []
                display = items[0] if items else "<list>"
                content = (atext + " " if atext else "") + display
                if mode == "center":
                    item: QGraphicsSimpleTextItem = scene.addSimpleText(content)
                    item.setBrush(QColor(240, 240, 240))
                    rect_local = item.boundingRect()
                    w = float(rect_local.width()); h = float(rect_local.height())
                    available_w = float(line_end_x - line_start_x)
                    start_x_f = float(line_start_x) + max(0.0, (available_w - w)) / 2.0
                    try:
                        cx = float(int(round(start_x_f)))
                    except Exception:
                        cx = start_x_f
                    item.setPos(QPointF(cx, cursor_y))
                    try:
                        item.setZValue(20000)
                    except Exception:
                        pass
                    rect = item.mapRectToScene(item.boundingRect())
                    self.element_rects[elem.segment_index] = rect
                    try:
                        self.element_rects_by_doc.setdefault(dk, {})[int(elem.segment_index)] = rect
                    except Exception:
                        pass
                    # Bolt marker for list (center)
                    try:
                        if getattr(elem, 'bolt', None) or getattr(elem, 'bbolt', None):
                            b_item = QGraphicsSimpleTextItem("B")
                            b_item.setBrush(QColor(255, 200, 0))
                            bx = rect.right() - b_item.boundingRect().width() - 3.0
                            by = rect.top() + 2.0
                            b_item.setPos(QPointF(bx, by))
                            try:
                                b_item.setZValue(20000)
                            except Exception:
                                pass
                            scene.addItem(b_item)
                    except Exception:
                        pass
                    line_height = max(line_height, h)
                    new_line()
                else:
                    item: QGraphicsSimpleTextItem = scene.addSimpleText(content)
                    item.setBrush(QColor(240, 240, 240))
                    rect_local = item.boundingRect()
                    w = rect_local.width(); h = rect_local.height()
                    if mode == "right":
                        x = cursor_x - w
                    else:
                        x = cursor_x
                    item.setPos(QPointF(x, cursor_y))
                    try:
                        item.setZValue(20000)
                    except Exception:
                        pass
                    rect = item.mapRectToScene(item.boundingRect())
                    self.element_rects[elem.segment_index] = rect
                    try:
                        self.element_rects_by_doc.setdefault(dk, {})[int(elem.segment_index)] = rect
                    except Exception:
                        pass
                    # Bolt marker for list
                    try:
                        if getattr(elem, 'bolt', None) or getattr(elem, 'bbolt', None):
                            b_item = QGraphicsSimpleTextItem("B")
                            b_item.setBrush(QColor(255, 200, 0))
                            bx = rect.right() - b_item.boundingRect().width() - 3.0
                            by = rect.top() + 2.0
                            b_item.setPos(QPointF(bx, by))
                            try:
                                b_item.setZValue(20000)
                            except Exception:
                                pass
                            scene.addItem(b_item)
                    except Exception:
                        pass
                    if mode in {"normal", "left"}:
                        cursor_x = x + w + spacing
                        line_height = max(line_height, h)
                    elif mode == "right":
                        cursor_x = x - spacing
                        line_height = max(line_height, h)
            elif elem.name == "font":
                # Minimal: ignore actual font changes; could extend to adjust line height/tint
                continue
            elif elem.name == "ticker" and elem.text_content:
                # Minimal ticker: draw text once, same as text, but with wider default width
                if mode == "center":
                    item: QGraphicsSimpleTextItem = scene.addSimpleText(elem.text_content)
                    item.setBrush(QColor(240, 200, 120))
                    rect_local = item.boundingRect()
                    w = float(rect_local.width())
                    h = float(rect_local.height())
                    available_w = float(line_end_x - line_start_x)
                    start_x_f = float(line_start_x) + max(0.0, (available_w - w)) / 2.0
                    try:
                        cx = float(int(round(start_x_f)))
                    except Exception:
                        cx = start_x_f
                    item.setPos(QPointF(cx, cursor_y))
                    try:
                        item.setZValue(20000)
                    except Exception:
                        pass
                    rect = item.mapRectToScene(item.boundingRect())
                    self.element_rects[elem.segment_index] = rect
                    # Bolt marker for ticker (center)
                    try:
                        if getattr(elem, 'bolt', None) or getattr(elem, 'bbolt', None):
                            b_item = QGraphicsSimpleTextItem("B")
                            b_item.setBrush(QColor(255, 200, 0))
                            bx = rect.right() - b_item.boundingRect().width() - 3.0
                            by = rect.top() + 2.0
                            b_item.setPos(QPointF(bx, by))
                            try:
                                b_item.setZValue(20000)
                            except Exception:
                                pass
                            scene.addItem(b_item)
                    except Exception:
                        pass
                    line_height = max(line_height, h)
                    new_line()
                else:
                    item: QGraphicsSimpleTextItem = scene.addSimpleText(elem.text_content)
                    item.setBrush(QColor(240, 200, 120))
                    rect_local = item.boundingRect()
                    w = rect_local.width()
                    h = rect_local.height()
                    if mode == "right":
                        x = cursor_x - w
                    else:
                        x = cursor_x
                    item.setPos(QPointF(x, cursor_y))
                    try:
                        item.setZValue(20000)
                    except Exception:
                        pass
                    rect = item.mapRectToScene(item.boundingRect())
                    self.element_rects[elem.segment_index] = rect
                    # Bolt marker for ticker
                    try:
                        if getattr(elem, 'bolt', None) or getattr(elem, 'bbolt', None):
                            b_item = QGraphicsSimpleTextItem("B")
                            b_item.setBrush(QColor(255, 200, 0))
                            bx = rect.right() - b_item.boundingRect().width() - 3.0
                            by = rect.top() + 2.0
                            b_item.setPos(QPointF(bx, by))
                            try:
                                b_item.setZValue(20000)
                            except Exception:
                                pass
                            scene.addItem(b_item)
                    except Exception:
                        pass
                    if mode in {"normal", "left"}:
                        cursor_x = x + w + spacing
                        line_height = max(line_height, h)
                    elif mode == "right":
                        cursor_x = x - spacing
                        line_height = max(line_height, h)
            elif elem.name == "bghoul":
                # Not rendered in preview; skip
                continue
            elif elem.name == "blank":
                # Flush any pending centered row first
                if mode == "center" and center_row_items:
                    flush_center_row()
                reserve_h = line_height
                if hasattr(elem, "height") and elem.height:
                    reserve_h = float(elem.height)
                rect_blank = QRectF(cursor_x, cursor_y, 10.0, reserve_h)
                self.element_rects[elem.segment_index] = rect_blank
                # Bolt marker for blank
                try:
                    if getattr(elem, 'bolt', None) or getattr(elem, 'bbolt', None):
                        b_item = QGraphicsSimpleTextItem("B")
                        b_item.setBrush(QColor(255, 200, 0))
                        bx = rect_blank.right() - b_item.boundingRect().width() - 3.0
                        by = rect_blank.top() + 2.0
                        b_item.setPos(QPointF(bx, by))
                        try:
                            b_item.setZValue(20000)
                        except Exception:
                            pass
                        scene.addItem(b_item)
                except Exception:
                    pass
                cursor_y += reserve_h
            elif elem.name == "image":
                # Try to draw the image. Support .m32 via m32lib.
                path = elem.image_path or ""
                pm_item = None
                draw_rect: QRectF | None = None
                if mode == "center":
                    # Place a single centered image per line
                    try:
                        from .m32lib import qpixmap_from_m32_file
                        from PySide6.QtGui import QPixmap
                        pm: QPixmap | None = None
                        resolved = self._resolve_image_path(path) if path else None
                        if resolved and resolved.lower().endswith(".m32"):
                            pm = qpixmap_from_m32_file(resolved)
                        elif resolved:
                            pm = QPixmap(resolved)
                    except Exception:
                        pm = None
                    if pm is not None and not pm.isNull():
                        iw = float(pm.width()); ih = float(pm.height())
                        available_w = float(line_end_x - line_start_x)
                        start_x_f = float(line_start_x) + max(0.0, (available_w - iw)) / 2.0
                        try:
                            cx = float(int(round(start_x_f)))
                        except Exception:
                            cx = start_x_f
                        pm_item = scene.addPixmap(pm)
                        pm_item.setOffset(QPointF(cx, cursor_y))
                        try:
                            pm_item.setTransformationMode(Qt.TransformationMode.FastTransformation)
                        except Exception:
                            pass
                        draw_rect = pm_item.mapRectToScene(pm_item.boundingRect())
                        # Overlay text
                        if getattr(elem, 'overlay_text', None) and draw_rect is not None:
                            ox = (elem.overlay_xoff or 0)
                            oy = (elem.overlay_yoff or 0)
                            t_item = scene.addSimpleText(elem.overlay_text)
                            t_item.setBrush(QColor(210, 210, 210))
                            t_item.setPos(QPointF(cx + ox, cursor_y + oy))
                            try:
                                t_item.setZValue(20000)
                            except Exception:
                                pass
                        # Bolt marker
                        try:
                            if getattr(elem, 'bolt', None) or getattr(elem, 'bbolt', None):
                                b_item = QGraphicsSimpleTextItem("B")
                                b_item.setBrush(QColor(255, 200, 0))
                                bx = cx + max(0.0, iw - b_item.boundingRect().width() - 3.0)
                                by = cursor_y + 2.0
                                b_item.setPos(QPointF(bx, by))
                                try:
                                    b_item.setZValue(20000)
                                except Exception:
                                    pass
                                scene.addItem(b_item)
                        except Exception:
                            pass
                        self.element_rects[elem.segment_index] = draw_rect
                        try:
                            if draw_rect is not None:
                                self.element_rects_by_doc.setdefault(dk, {})[int(elem.segment_index)] = draw_rect
                        except Exception:
                            pass
                        line_height = max(line_height, ih)
                        new_line()
                    else:
                        # Placeholder centered
                        w_pl, h_pl = 80.0, 32.0
                        available_w = float(line_end_x - line_start_x)
                        start_x_f = float(line_start_x) + max(0.0, (available_w - w_pl)) / 2.0
                        try:
                            cx = float(int(round(start_x_f)))
                        except Exception:
                            cx = start_x_f
                        rect_item = scene.addRect(QRectF(cx, cursor_y, w_pl, h_pl), QPen(QColor(140, 140, 140)), QBrush(QColor(90, 90, 90)))
                        from pathlib import Path as _P
                        label = scene.addSimpleText((_P(path).name if path else "<image>"))
                        label.setBrush(QColor(210, 210, 210))
                        label.setPos(QPointF(cx + 6, cursor_y + 6))
                        try:
                            label.setZValue(20000)
                        except Exception:
                            pass
                        try:
                            if getattr(elem, 'bolt', None) or getattr(elem, 'bbolt', None):
                                b_item = QGraphicsSimpleTextItem("B")
                                b_item.setBrush(QColor(255, 200, 0))
                                bx = cx + max(0.0, w_pl - b_item.boundingRect().width() - 3.0)
                                by = cursor_y + 2.0
                                b_item.setPos(QPointF(bx, by))
                                try:
                                    b_item.setZValue(20000)
                                except Exception:
                                    pass
                                scene.addItem(b_item)
                        except Exception:
                            pass
                        draw_rect = rect_item.mapRectToScene(rect_item.rect())
                        self.element_rects[elem.segment_index] = draw_rect
                        line_height = max(line_height, h_pl)
                        new_line()
                else:
                    try:
                        from .m32lib import qpixmap_from_m32_file
                        from PySide6.QtGui import QPixmap
                        pm: QPixmap | None = None
                        resolved = self._resolve_image_path(path) if path else None
                        if resolved and resolved.lower().endswith(".m32"):
                            pm = qpixmap_from_m32_file(resolved)
                        elif resolved:
                            pm = QPixmap(resolved)
                        if pm and not pm.isNull():
                            pm_item = scene.addPixmap(pm)
                            # Measure
                            iw = pm.width()
                            ih = pm.height()
                            # Position based on mode
                            if mode == "right":
                                x = cursor_x - iw
                            else:
                                x = cursor_x
                            pm_item.setOffset(QPointF(x, cursor_y))
                            try:
                                pm_item.setTransformationMode(Qt.TransformationMode.FastTransformation)
                            except Exception:
                                pass
                            draw_rect = pm_item.mapRectToScene(pm_item.boundingRect())
                            # Draw overlay text if specified
                            if getattr(elem, 'overlay_text', None) and draw_rect is not None:
                                ox = (elem.overlay_xoff or 0)
                                oy = (elem.overlay_yoff or 0)
                                t_item = scene.addSimpleText(elem.overlay_text)
                                t_item.setBrush(QColor(210, 210, 210))
                                t_item.setPos(QPointF(x + ox, cursor_y + oy))
                                try:
                                    t_item.setZValue(20000)
                                except Exception:
                                    pass
                            # Bolt marker 'B' if bolt attribute present
                            try:
                                if getattr(elem, 'bolt', None) or getattr(elem, 'bbolt', None):
                                    b_item = QGraphicsSimpleTextItem("B")
                                    b_item.setBrush(QColor(255, 200, 0))
                                    bx = x + max(0.0, float(iw) - b_item.boundingRect().width() - 3.0)
                                    by = cursor_y + 2.0
                                    b_item.setPos(QPointF(bx, by))
                                    try:
                                        b_item.setZValue(20000)
                                    except Exception:
                                        pass
                                    scene.addItem(b_item)
                            except Exception:
                                pass
                    except Exception as _img_e:
                        # Keep placeholder flow
                        pm_item = None
                    if pm_item is None:
                        # Fallback placeholder
                        w, h = 80.0, 32.0
                        # Position placeholder based on current mode
                        if mode == "right":
                            px = cursor_x - w
                        else:
                            px = cursor_x
                        rect_item = scene.addRect(QRectF(px, cursor_y, w, h), QPen(QColor(140, 140, 140)), QBrush(QColor(90, 90, 90)))
                        # Only show filename to reduce clutter
                        from pathlib import Path as _P
                        label = scene.addSimpleText((_P(path).name if path else "<image>"))
                        label.setBrush(QColor(210, 210, 210))
                        label.setPos(QPointF(px + 6, cursor_y + 6))
                        try:
                            label.setZValue(20000)
                        except Exception:
                            pass
                        # Bolt marker for placeholder
                        try:
                            if getattr(elem, 'bolt', None) or getattr(elem, 'bbolt', None):
                                b_item = QGraphicsSimpleTextItem("B")
                                b_item.setBrush(QColor(255, 200, 0))
                                bx = px + max(0.0, w - b_item.boundingRect().width() - 3.0)
                                by = cursor_y + 2.0
                                b_item.setPos(QPointF(bx, by))
                                try:
                                    b_item.setZValue(20000)
                                except Exception:
                                    pass
                                scene.addItem(b_item)
                        except Exception:
                            pass
                        draw_rect = rect_item.mapRectToScene(rect_item.rect())
                        # Advance based on mode
                        if mode in {"normal", "left"}:
                            cursor_x = px + w + spacing
                        elif mode == "right":
                            cursor_x = px - spacing
                    else:
                        # Advance based on mode
                        if mode in {"normal", "left"}:
                            cursor_x = (draw_rect.right() if draw_rect else cursor_x) + spacing
                        elif mode == "right":
                            cursor_x = (draw_rect.left() if draw_rect else cursor_x) - spacing
                    if draw_rect is not None:
                        self.element_rects[elem.segment_index] = draw_rect
                        try:
                            if draw_rect is not None:
                                self.element_rects_by_doc.setdefault(dk, {})[int(elem.segment_index)] = draw_rect
                        except Exception:
                            pass
        # After processing all elements, if still in center mode with pending items, flush them
        if mode == "center" and center_row_items:
            flush_center_row()

    def _color_from_token(self, token: str) -> QColor:
        t = token.strip()
        # Treat common 'clear' value as fully transparent
        if t.lower() == "clear":
            return QColor(0, 0, 0, 0)
        # Accept forms: 0xAARRGGBB or #AARRGGBB
        try:
            if t.startswith("0x") or t.startswith("0X"):
                val = int(t, 16)
                a = (val >> 24) & 0xFF
                r = (val >> 16) & 0xFF
                g = (val >> 8) & 0xFF
                b = (val >> 0) & 0xFF
                return QColor(r, g, b, a)
            if t.startswith("#") and len(t) == 9:
                val = int(t[1:], 16)
                a = (val >> 24) & 0xFF
                r = (val >> 16) & 0xFF
                g = (val >> 8) & 0xFF
                b = (val >> 0) & 0xFF
                return QColor(r, g, b, a)
        except ValueError:
            pass
        # Fallback to a named color or default gray
        c = QColor(t)
        if not c.isValid():
            c = QColor(32, 32, 32)
        return c

    def _draw_backdrop_image(self, scene: QGraphicsScene, rect: QRectF, path: str, mode: str | None) -> None:
        from PySide6.QtGui import QPixmap
        pm = None
        try:
            from .m32lib import qpixmap_from_m32_file
            if path.lower().endswith(".m32"):
                pm = qpixmap_from_m32_file(path)
        except Exception as _bd_e:
            pm = None
        if pm is None:
            # Attempt resource/menu root resolution for relative paths (with .m32 default)
            resolved = self._resolve_image_path(path) or path
            from PySide6.QtGui import QPixmap
            pm = QPixmap(resolved)
        if pm.isNull():
            # Fallback: indicate missing image with hatched box
            hatch = QBrush(QColor(60, 60, 60))
            box = scene.addRect(rect, QPen(QColor(90, 90, 90)), hatch)
            box.setZValue(-50)
            return
        mode = (mode or "center").lower()
        if mode == "tile":
            brush = QBrush(pm)
            brush.setStyle(Qt.BrushStyle.TexturePattern)
            item = scene.addRect(rect, QPen(Qt.NoPen), brush)
            item.setZValue(-50)
        elif mode == "stretch":
            scaled = pm.scaled(int(rect.width()), int(rect.height()), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            item = scene.addPixmap(scaled)
            item.setOffset(rect.x(), rect.y())
            item.setZValue(-50)
        elif mode == "left":
            item = scene.addPixmap(pm)
            item.setOffset(rect.x(), rect.y() + (rect.height() - pm.height()) / 2)
            try:
                item.setTransformationMode(Qt.TransformationMode.FastTransformation)
            except Exception:
                pass
            item.setZValue(-50)
        elif mode == "right":
            item = scene.addPixmap(pm)
            item.setOffset(rect.x() + rect.width() - pm.width(), rect.y() + (rect.height() - pm.height()) / 2)
            try:
                item.setTransformationMode(Qt.TransformationMode.FastTransformation)
            except Exception:
                pass
            item.setZValue(-50)
        else:  # center
            item = scene.addPixmap(pm)
            item.setOffset(rect.x() + (rect.width() - pm.width()) / 2, rect.y() + (rect.height() - pm.height()) / 2)
            try:
                item.setTransformationMode(Qt.TransformationMode.FastTransformation)
            except Exception:
                pass
            item.setZValue(-50)

    def selection_rect_for(self, payload, doc: RfmDocument) -> QRectF | None:
        # Late import to avoid circular types in signatures
        from .rfm_model import RfmFrame, RfmElement
        dk = self._doc_key_of(doc)
        if isinstance(payload, RfmFrame):
            # Resolve rect within the current document context first
            base = None
            try:
                base = self.frame_rects_by_doc.get(dk, {}).get(payload.name)
            except Exception:
                base = None
            if base is None:
                base = self.frame_rects.get(payload.name)
            if base is None:
                return None
            inner = QRectF(base)
            # Shrink by frame border width to move inside the decorative border region
            bw = payload.border_width or 0
            if bw > 0 and inner.width() > bw * 2 and inner.height() > bw * 2:
                inner = inner.adjusted(bw, bw, -bw, -bw)
            # Fallback if degenerate
            if inner.width() <= 0 or inner.height() <= 0:
                return base
            return inner
        if isinstance(payload, RfmElement):
            try:
                rect = self.element_rects_by_doc.get(dk, {}).get(int(payload.segment_index))
            except Exception:
                rect = None
            if rect is not None:
                return rect
            return self.element_rects.get(payload.segment_index)
        if isinstance(payload, tuple) and payload and payload[0] == "backdrop":
            return self.content_rect
        return None

    def _inner_rect_of(self, rect: QRectF, f: RfmFrame) -> QRectF:
        bw = float(getattr(f, 'border_width', 0) or 0)
        inner = rect.adjusted(bw, bw, -bw, -bw)
        if inner.width() <= 0 or inner.height() <= 0:
            return QRectF(rect)
        return inner

    def _render_document_into(
        self,
        doc: RfmDocument,
        scene: QGraphicsScene,
        container: QRectF,
        visited: Optional[set[str]] = None,
    ) -> None:
        """Render a document into a given container rect, optionally recursing into its sub-pages.

        - Skips backdrop for nested renders
        - Offsets all geometry to the container's top-left
        - Temporarily adapts max_screen_width/height to the container size
        """
        try:
            # Save/override screen profile
            orig_w = self.max_screen_width
            orig_h = self.max_screen_height
            self.max_screen_width = max(1, int(container.width()))
            self.max_screen_height = max(1, int(container.height()))

            # Build frame layout for the sub-document
            all_frames = list(doc.frames.values())
            frames_by_name = {f.name: f for f in all_frames}
            children_by_parent: dict[str, list[RfmFrame]] = {}
            top_level_frames: list[RfmFrame] = []
            for f in all_frames:
                parent = getattr(f, 'cut_from', None)
                if parent and parent in frames_by_name:
                    children_by_parent.setdefault(parent, []).append(f)
                else:
                    top_level_frames.append(f)

            def clamp_dims_for_border(w: float, h: float, f: RfmFrame) -> tuple[float, float]:
                bw = float(f.border_width or 0)
                if bw * 2 >= w:
                    w = max(1.0, w)
                if bw * 2 >= h:
                    h = max(1.0, h)
                return w, h

            def layout_children(parent: RfmFrame, parent_rect: QRectF, out: list[tuple[QRectF, RfmFrame]]) -> None:
                # Left-to-right placement within parent's inner rect
                container_rect = self._inner_rect_of(parent_rect, parent)
                x_cursor = float(container_rect.left())
                for ch in children_by_parent.get(parent.name, []) or []:
                    available_w = float(container_rect.right() - x_cursor)
                    if available_w <= 0:
                        break
                    w_target = available_w if int(getattr(ch, 'width', 0) or 0) == 0 else float(min(int(ch.width), available_w))
                    h_target = float(container_rect.height()) if int(getattr(ch, 'height', 0) or 0) == 0 else float(min(int(ch.height), container_rect.height()))
                    w_val, h_val = clamp_dims_for_border(max(1.0, w_target), max(1.0, h_target), ch)
                    rect = QRectF(x_cursor, float(container_rect.top()), w_val, h_val)
                    ch.preview_pos = (int(rect.x()), int(rect.y()))
                    out.append((rect, ch))
                    layout_children(ch, rect, out)
                    x_cursor = float(rect.right())

            y_cursor = 0.0
            frame_rects: list[tuple[QRectF, RfmFrame]] = []
            for f in top_level_frames:
                w_val = float(self.max_screen_width if f.width == 0 else min(f.width, self.max_screen_width))
                h_val = float(self.max_screen_height if f.height == 0 else min(f.height, self.max_screen_height))
                w_val, h_val = clamp_dims_for_border(w_val, h_val, f)
                rect = QRectF(0.0, y_cursor, w_val, h_val)
                f.preview_pos = (int(rect.x()), int(rect.y()))
                frame_rects.append((rect, f))
                y_cursor += rect.height()

            # Draw frames and elements offset into the container
            for rect, frame in frame_rects:
                off_rect = rect.translated(container.left(), container.top())
                self._draw_frame(scene, off_rect, frame)
                # Cache absolute rects for selection support
                self.frame_rects[frame.name] = off_rect
                try:
                    dk2 = self._doc_key_of(doc)
                    self.frame_rects_by_doc.setdefault(dk2, {})[frame.name] = off_rect
                except Exception:
                    pass

            # Elements: draw within the first frame rect (same simplified behavior as top-level)
            host_rect = frame_rects[0][0] if frame_rects else QRectF(0, 0, float(self.max_screen_width), float(self.max_screen_height))
            self._draw_elements(scene, doc, host_rect.translated(container.left(), container.top()))

            # Recurse into sub-pages if enabled
            if self.subframe_rendering_enabled and self.page_resolver:
                base_key: Optional[str] = getattr(doc, 'file_path', None)
                vset: set[str] = set(visited) if visited else set()
                if isinstance(base_key, str) and base_key:
                    vset.add(base_key)
                for rect, frame in frame_rects:
                    page_name = getattr(frame, 'page', None)
                    if not page_name:
                        continue
                    try:
                        subdoc = self.page_resolver(page_name, base_key)
                    except Exception:
                        subdoc = None
                    if not subdoc:
                        continue
                    sub_key = getattr(subdoc, 'file_path', None)
                    if isinstance(sub_key, str) and sub_key in vset:
                        continue
                    inner = self._inner_rect_of(rect.translated(container.left(), container.top()), frame)
                    if inner.width() <= 0 or inner.height() <= 0:
                        continue
                    self._render_document_into(subdoc, scene, inner, vset | ({sub_key} if isinstance(sub_key, str) and sub_key else set()))
        except Exception:
            pass
        finally:
            # Restore screen profile regardless of failures
            try:
                self.max_screen_width = orig_w  # type: ignore[name-defined]
                self.max_screen_height = orig_h  # type: ignore[name-defined]
            except Exception:
                pass


