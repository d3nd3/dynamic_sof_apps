from __future__ import annotations

from typing import Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsLineItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem

from .rfm_model import RfmDocument, RfmElement, RfmFrame


class RfmRenderer:
    def __init__(self) -> None:
        self.frame_pen = QPen(QColor(80, 160, 255, 255))
        self.frame_brush = QBrush(QColor(80, 160, 255, 40))
        self.content_pen = QPen(QColor(240, 240, 240, 255))
        self.frame_rects: dict[str, QRectF] = {}
        self.element_rects: dict[int, QRectF] = {}
        self.content_rect: QRectF = QRectF(0, 0, 0, 0)
        # Screen profile for auto-fill. Width remains 640 across profiles.
        self.max_screen_width: int = 640
        self.max_screen_height: int = 480

    def render_document(self, doc: RfmDocument, scene: QGraphicsScene) -> None:
        # Arrange frames in a simple vertical stack for preview
        y_cursor = 0.0
        margin = 20.0
        frame_order = list(doc.frames.values())

        frame_rects: list[tuple[QRectF, RfmFrame]] = []
        for frame in frame_order:
            # Auto-fill behavior: 0 width/height means fill the screen dimension
            # Clamp to maximum screen bounds for preview
            w_val = self.max_screen_width if frame.width == 0 else min(frame.width, self.max_screen_width)
            h_val = self.max_screen_height if frame.height == 0 else min(frame.height, self.max_screen_height)
            # Account for frame.border_width: the width/height include border region in engine terms.
            # To prevent any drawing outside the screen, we shrink the inner rect by border width.
            bw = frame.border_width or 0
            if bw * 2 < w_val and bw * 2 < h_val:
                width = float(w_val)
                height = float(h_val)
            else:
                # Extreme border sizes: clamp to at least 1 px inner
                width = float(max(1, w_val))
                height = float(max(1, h_val))
            rect = QRectF(0, y_cursor, width, height)
            frame.preview_pos = (int(rect.x()), int(rect.y()))
            frame_rects.append((rect, frame))
            y_cursor += rect.height() + margin

        # Fixed screen content area: always exactly one screen per selected ratio
        screen_rect = QRectF(0, 0, float(self.max_screen_width), float(self.max_screen_height))
        self.content_rect = screen_rect

        # Draw backdrop behind frames
        if doc.backdrop_bgcolor:
            bg = self._color_from_token(doc.backdrop_bgcolor)
            bg_item = scene.addRect(screen_rect, QPen(Qt.NoPen), QBrush(bg))
            bg_item.setZValue(-100)
        if doc.backdrop_image:
            self._draw_backdrop_image(scene, screen_rect, doc.backdrop_image, doc.backdrop_mode)

        # Now draw frames on top
        self.frame_rects.clear()
        for rect, frame in frame_rects:
            # Draw full frame rect; items outside the screen are naturally clipped by the scene rect
            self._draw_frame(scene, rect, frame)
            self.frame_rects[frame.name] = rect

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

        # Simple content renderer: lay out elements top-down in the first frame if one exists
        # Use full screen rect for simplified content rendering area
        host_rect = screen_rect

        self._draw_elements(scene, doc, host_rect)

    def _draw_frame(self, scene: QGraphicsScene, rect: QRectF, frame: RfmFrame) -> None:
        # Outer frame outline (light)
        outer_pen = QPen(QColor(120, 160, 220, 180))
        outer_pen.setWidth(1)
        outer_pen.setCosmetic(True)
        # Draw strictly inside the frame rect to avoid bleeding outside content/screen
        outline_inset = outer_pen.widthF() / 2.0
        outline_rect = rect.adjusted(outline_inset, outline_inset, -outline_inset, -outline_inset)
        if outline_rect.width() > 0 and outline_rect.height() > 0:
            scene.addRect(outline_rect, outer_pen)

        # Border/backfill
        if frame.border_width is not None and frame.border_line_width is not None and frame.border_line_color is not None:
            inner = rect.adjusted(frame.border_width, frame.border_width, -frame.border_width, -frame.border_width)
            if inner.width() > 0 and inner.height() > 0:
                # Backfill only if border is present (engine quirk)
                if frame.backfill_color:
                    fill_color = self._color_from_token(frame.backfill_color)
                    scene.addRect(inner, QPen(Qt.NoPen), QBrush(fill_color))
                # Border line
                line_pen = QPen(self._color_from_token(frame.border_line_color))
                line_pen.setWidth(max(1, frame.border_line_width))
                line_pen.setCosmetic(True)
                # Inset to keep the stroke entirely within the outer frame rect
                half = line_pen.widthF() / 2.0
                line_rect = inner.adjusted(half, half, -half, -half)
                if line_rect.width() > 0 and line_rect.height() > 0:
                    scene.addRect(line_rect, line_pen)

        label = scene.addSimpleText(f"frame {frame.name}  {frame.width}x{frame.height}")
        # Place label slightly inside to avoid overlap with selection stroke
        label.setPos(QPointF(rect.x() + 4, rect.y() + 2))

    def _draw_elements(self, scene: QGraphicsScene, doc: RfmDocument, host_rect: QRectF) -> None:
        x = host_rect.x() + 12
        y = host_rect.y() + 28
        line_height = 18.0
        spacing = 8.0

        # Removed preview header text for cleaner WYSIWYG view

        self.element_rects.clear()
        for elem in doc.elements:
            if elem.name == "text" and elem.text_content:
                item: QGraphicsSimpleTextItem = scene.addSimpleText(elem.text_content)
                item.setBrush(QColor(240, 240, 240))
                item.setPos(QPointF(x, y))
                rect = item.mapRectToScene(item.boundingRect())
                self.element_rects[elem.segment_index] = rect
                y += line_height
            elif elem.name == "hr":
                width = host_rect.width() - 24
                pen = QPen(QColor(180, 180, 180, 200))
                pen.setWidth(1)
                line: QGraphicsLineItem = scene.addLine(x, y + 4, x + max(10.0, width), y + 4, pen)
                rect = line.mapRectToScene(line.boundingRect())
                self.element_rects[elem.segment_index] = rect
                y += spacing
            elif elem.name == "blank":
                # reserve height if known
                reserve_h = line_height
                if hasattr(elem, "height") and elem.height:
                    reserve_h = float(elem.height)
                self.element_rects[elem.segment_index] = QRectF(x, y, 10.0, reserve_h)
                y += reserve_h
            elif elem.name == "image":
                # placeholder rectangle for image
                w, h = 80.0, 32.0
                rect_item = scene.addRect(QRectF(x, y, w, h), QPen(QColor(140, 140, 140)), QBrush(QColor(90, 90, 90)))
                label = scene.addSimpleText(elem.image_path or "<image>")
                label.setBrush(QColor(210, 210, 210))
                label.setPos(QPointF(x + 6, y + 6))
                rect = rect_item.mapRectToScene(rect_item.rect())
                self.element_rects[elem.segment_index] = rect
                y += h + spacing

    def _color_from_token(self, token: str) -> QColor:
        t = token.strip()
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
        pm = QPixmap(path)
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
            item.setZValue(-50)
        elif mode == "right":
            item = scene.addPixmap(pm)
            item.setOffset(rect.x() + rect.width() - pm.width(), rect.y() + (rect.height() - pm.height()) / 2)
            item.setZValue(-50)
        else:  # center
            item = scene.addPixmap(pm)
            item.setOffset(rect.x() + (rect.width() - pm.width()) / 2, rect.y() + (rect.height() - pm.height()) / 2)
            item.setZValue(-50)

    def selection_rect_for(self, payload, doc: RfmDocument) -> QRectF | None:
        # Late import to avoid circular types in signatures
        from .rfm_model import RfmFrame, RfmElement
        if isinstance(payload, RfmFrame):
            base = self.frame_rects.get(payload.name)
            if base is None:
                return None
            inner = QRectF(base)
            # Shrink by frame border width to move inside the decorative border region
            bw = payload.border_width or 0
            if bw > 0 and inner.width() > bw * 2 and inner.height() > bw * 2:
                inner = inner.adjusted(bw, bw, -bw, -bw)
            # Also shrink by the border line thickness to be inside the line stroke
            blw = payload.border_line_width or 0
            if blw > 0 and inner.width() > blw * 2 and inner.height() > blw * 2:
                inner = inner.adjusted(blw, blw, -blw, -blw)
            # Fallback if degenerate
            if inner.width() <= 0 or inner.height() <= 0:
                return base
            return inner
        if isinstance(payload, RfmElement):
            return self.element_rects.get(payload.segment_index)
        if isinstance(payload, tuple) and payload and payload[0] == "backdrop":
            return self.content_rect
        return None


