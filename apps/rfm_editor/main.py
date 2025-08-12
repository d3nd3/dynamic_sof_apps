import os
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QSize, QSettings
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QPainter, QColor, QBrush, QPalette, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QGraphicsScene,
    QGraphicsView,
    QFrame,
    QHBoxLayout,
    QMenu,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QSplitterHandle,
    QStatusBar,
    QHeaderView,
    QAbstractItemView,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
    QSizePolicy,
    QWidget,
)

from .rfm_model import RfmDocument, RfmElement
from .rfm_parser import parse_rfm_content
from .rfm_renderer import RfmRenderer
from .rfm_serializer import serialize_rfm


class _NoVScrollGraphicsView(QGraphicsView):
    def wheelEvent(self, event):  # type: ignore[override]
        # Block all scrolling (vertical and horizontal). View is scaled, not scrolled.
        try:
            dy = 0
            dx = 0
            delta = event.angleDelta()
            try:
                dy = delta.y()
                dx = delta.x()
            except Exception:
                pass
            if dy != 0 or dx != 0:
                event.accept()
                return
        except Exception:
            pass
        super().wheelEvent(event)


class _OutlineTree(QTreeWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._dnd_line: Optional[QFrame] = None

    def _item_doc_key(self, item: QTreeWidgetItem) -> Optional[str]:
        # Ascend to the doc-root and read its key
        try:
            it = item
            while it.parent() is not None:
                it = it.parent()
            payload = it.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(payload, tuple) and payload and payload[0] == 'doc-root' and len(payload) >= 2:
                return str(payload[1])
        except Exception:
            return None
        return None

    def _find_group_for_pos(self, pos) -> tuple[Optional[str], Optional[str], Optional[QTreeWidgetItem]]:
        # Returns (group_name, doc_key, target_item) where group_name is 'Frames' or 'Elements' if pointer is within that group subtree
        try:
            pt = pos.toPoint() if hasattr(pos, 'toPoint') else pos
        except Exception:
            pt = pos
        item = self.itemAt(pt)
        if item is None:
            return (None, None, None)
        try:
            # Walk up until we hit a group header (Frames/Elements)
            it = item
            while it is not None:
                text0 = it.text(0)
                if text0 in ('Frames', 'Elements'):
                    return (text0, self._item_doc_key(it), item)
                it = it.parent()
        except Exception:
            pass
        return (None, self._item_doc_key(item), item)

    def _current_drag_kind_and_doc(self) -> tuple[Optional[str], Optional[str]]:
        # Determine what is being dragged based on current selection
        try:
            sel = self.selectedItems()
            if not sel:
                return (None, None)
            item = sel[0]
            payload = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(payload, tuple):
                tag = payload[0]
                if tag == 'element' and len(payload) >= 3:
                    return ('element', str(payload[1]))
                if tag == 'frame' and len(payload) >= 3:
                    return ('frame', str(payload[1]))
            # Disallow dragging of non-move items
            return (None, None)
        except Exception:
            return (None, None)

    def _is_valid_drop(self, event) -> bool:
        try:
            kind, src_doc = self._current_drag_kind_and_doc()
            if not kind:
                return False
            group, dest_doc, target_item = self._find_group_for_pos(event.position() if hasattr(event, 'position') else event.pos())
            # If dropping directly ON an item, only allow if that item is the category node
            try:
                indicator = self.dropIndicatorPosition()
            except Exception:
                indicator = QAbstractItemView.DropIndicatorPosition.OnViewport  # type: ignore[attr-defined]
            if kind == 'element':
                # Always allow element drops; we'll normalize to the source document's Elements group in dropEvent
                return src_doc is not None
            if kind == 'frame':
                # Frames may be reordered within any document's Frames group (including cross-doc)
                return group == 'Frames' and dest_doc is not None
            return False
        except Exception:
            return False

    def dragEnterEvent(self, event):  # type: ignore[override]
        try:
            kind, _ = self._current_drag_kind_and_doc()
            if kind in ('element', 'frame'):
                event.acceptProposedAction()
            else:
                event.ignore()
        except Exception:
            event.ignore()

    def dragMoveEvent(self, event):  # type: ignore[override]
        try:
            kind, _ = self._current_drag_kind_and_doc()
            if kind in ('element', 'frame'):
                event.acceptProposedAction()
                # Draw custom insertion indicator for element drags
                if kind == 'element':
                    try:
                        indicator = self.dropIndicatorPosition()
                    except Exception:
                        indicator = QAbstractItemView.DropIndicatorPosition.OnViewport  # type: ignore[attr-defined]
                    group, _doc, target_item = self._find_group_for_pos(event.position() if hasattr(event, 'position') else event.pos())
                    # Find the 'Elements' group node to compute end-of-list position if needed
                    elements_group = None
                    if target_item is not None:
                        it = target_item
                        while it is not None and it.text(0) != 'Elements':
                            it = it.parent()
                        elements_group = it
                    # Compute Y position for the indicator
                    y_pos = None
                    if indicator in (
                        QAbstractItemView.DropIndicatorPosition.AboveItem,
                        QAbstractItemView.DropIndicatorPosition.BelowItem,
                        QAbstractItemView.DropIndicatorPosition.OnItem,
                    ) and target_item is not None:
                        rect = self.visualItemRect(target_item)
                        if indicator == QAbstractItemView.DropIndicatorPosition.AboveItem:
                            y_pos = rect.top()
                        elif indicator == QAbstractItemView.DropIndicatorPosition.BelowItem:
                            y_pos = rect.bottom()
                        else:  # OnItem
                            if target_item.text(0) != 'Elements':
                                # On an element: show line below it
                                y_pos = rect.bottom()
                            else:
                                # On 'Elements' header → indicate insertion at top (just below header)
                                y_pos = rect.bottom()
                    else:
                        # OnViewport or unknown → append to end of elements list of the source doc
                        # We cannot know source here reliably, so use visible Elements group under cursor if any
                        if elements_group is not None:
                            count = elements_group.childCount()
                            if count > 0:
                                last_rect = self.visualItemRect(elements_group.child(count - 1))
                                y_pos = last_rect.bottom()
                            else:
                                y_pos = self.visualItemRect(elements_group).bottom()
                    self._show_dnd_line(y_pos)
                else:
                    self._hide_dnd_line()
            else:
                self._hide_dnd_line()
                event.ignore()
        except Exception:
            self._hide_dnd_line()
            event.ignore()
    
    def _find_elements_group_node(self, doc_key: str) -> Optional[QTreeWidgetItem]:
        try:
            for i in range(self.topLevelItemCount()):
                root = self.topLevelItem(i)
                payload = root.data(0, Qt.ItemDataRole.UserRole)
                if not (isinstance(payload, tuple) and payload[0] == 'doc-root' and payload[1] == doc_key):
                    continue
                for j in range(root.childCount()):
                    g = root.child(j)
                    if g.text(0) == 'Elements':
                        return g
        except Exception:
            pass
        return None

    def dropEvent(self, event):  # type: ignore[override]
        # Only allow drops that match our constraints; otherwise ignore to avoid accidental deletion
        try:
            if not self._is_valid_drop(event):
                # Accept but do nothing to avoid source-side deletion by Qt
                event.accept()
                self._hide_dnd_line()
                return
        except Exception:
            event.accept()
            self._hide_dnd_line()
            return
        # Distinguish element vs frame drops: handle element reorders manually to avoid Qt removing items
        kind, src_doc = self._current_drag_kind_and_doc()
        group, dest_doc, target_item = self._find_group_for_pos(event.position() if hasattr(event, 'position') else event.pos())
        try:
            indicator = self.dropIndicatorPosition()
        except Exception:
            indicator = QAbstractItemView.DropIndicatorPosition.OnViewport  # type: ignore[attr-defined]

        # Manual element reordering
        if kind == 'element' and src_doc:
            try:
                self._hide_dnd_line()
                # Build current order of element indices in source doc
                def gather_group(gitem: QTreeWidgetItem) -> list[int]:
                    order: list[int] = []
                    for i in range(gitem.childCount()):
                        it = gitem.child(i)
                        p = it.data(0, Qt.ItemDataRole.UserRole)
                        if isinstance(p, tuple) and p[0] == 'element' and len(p) >= 3:
                            try:
                                order.append(int(p[2]))
                            except Exception:
                                pass
                    return order

                # Find the actual 'Elements' category node and current order
                root = target_item
                while root and root.text(0) != 'Elements':
                    root = root.parent()
                if not root or root.text(0) != 'Elements':
                    # If dropping outside items, use the Elements group for the source document
                    root = self._find_elements_group_node(src_doc)
                    if not root:
                        event.accept()
                        return
                current_order = gather_group(root)
                sel = self.selectedItems()
                if not sel:
                    event.accept()
                    return
                dragged_payload = sel[0].data(0, Qt.ItemDataRole.UserRole)
                if not (isinstance(dragged_payload, tuple) and dragged_payload[0] == 'element' and len(dragged_payload) >= 3):
                    event.accept()
                    return
                dragged_idx = int(dragged_payload[2])
                # Remove dragged from list if present
                current_order = [x for x in current_order if x != dragged_idx]
                # Compute insertion row based on indicator and target item
                if indicator == QAbstractItemView.DropIndicatorPosition.AboveItem and target_item:
                    # Insert before the target element item
                    if target_item.text(0) != 'Elements':
                        tpay = target_item.data(0, Qt.ItemDataRole.UserRole)
                        if isinstance(tpay, tuple) and tpay[0] == 'element' and len(tpay) >= 3:
                            target_seg = int(tpay[2])
                            try:
                                insert_pos = current_order.index(target_seg)
                            except ValueError:
                                insert_pos = 0
                        else:
                            insert_pos = 0
                    else:
                        insert_pos = 0
                elif indicator == QAbstractItemView.DropIndicatorPosition.BelowItem and target_item:
                    if target_item.text(0) != 'Elements':
                        tpay = target_item.data(0, Qt.ItemDataRole.UserRole)
                        if isinstance(tpay, tuple) and tpay[0] == 'element' and len(tpay) >= 3:
                            target_seg = int(tpay[2])
                            try:
                                insert_pos = current_order.index(target_seg) + 1
                            except ValueError:
                                insert_pos = len(current_order)
                        else:
                            insert_pos = len(current_order)
                    else:
                        insert_pos = len(current_order)
                else:
                    # OnItem: element → BelowItem behavior; on 'Elements' header → insert at top; OnViewport → append to end
                    if indicator == QAbstractItemView.DropIndicatorPosition.OnItem and target_item:
                        if target_item.text(0) == 'Elements':
                            insert_pos = 0
                        else:
                            tpay = target_item.data(0, Qt.ItemDataRole.UserRole)
                            if isinstance(tpay, tuple) and tpay[0] == 'element' and len(tpay) >= 3:
                                target_seg = int(tpay[2])
                                try:
                                    insert_pos = current_order.index(target_seg) + 1
                                except ValueError:
                                    insert_pos = len(current_order)
                            else:
                                insert_pos = len(current_order)
                    else:
                        insert_pos = len(current_order)
                if insert_pos < 0:
                    insert_pos = 0
                if insert_pos > len(current_order):
                    insert_pos = len(current_order)
                new_order = list(current_order)
                new_order.insert(insert_pos, dragged_idx)

                # Remember the dragged tag content to locate it after reparse for reselection
                try:
                    dragged_tag_str = None
                    wnd_doc = wnd.documents_by_key.get(dest_doc) if hasattr(wnd, 'documents_by_key') else None
                    if wnd_doc and 0 <= dragged_idx < len(wnd_doc.segments):
                        seg_kind, seg_val = wnd_doc.segments[dragged_idx]
                        if seg_kind == 'tag':
                            dragged_tag_str = seg_val
                except Exception:
                    dragged_tag_str = None

                # Apply to model via host window API, then refresh UI
                wnd = self.window()
                if hasattr(wnd, '_reorder_elements_by_segment_indices_for_doc'):
                    # Use the source doc for element moves
                    changed = wnd._reorder_elements_by_segment_indices_for_doc(src_doc, new_order)
                    if changed:
                        try:
                            from .rfm_serializer import serialize_rfm
                            from .rfm_parser import parse_rfm_content
                            doc = wnd.documents_by_key.get(src_doc)
                            if doc is not None:
                                text = serialize_rfm(doc)
                                wnd.documents_by_key[src_doc] = parse_rfm_content(text, file_path=doc.file_path)
                        except Exception:
                            pass
                    # Ensure active document pointer is updated if needed
                    try:
                        if getattr(wnd, 'active_doc_key', None) == src_doc:
                            wnd.document = wnd.documents_by_key.get(src_doc, wnd.document)
                    except Exception:
                        pass
                # Rebuild outline/scene to reflect changes and ensure nothing disappears
                try:
                    wnd.dirty = True
                    wnd.refresh_outline()
                    wnd.refresh_scene()
                    # Try to reselect the moved element by matching its tag string
                    if dragged_tag_str is not None:
                        try:
                            new_doc = wnd.documents_by_key.get(src_doc)
                            if new_doc is not None:
                                new_index = None
                                for i, (k, v) in enumerate(new_doc.segments):
                                    if k == 'tag' and v == dragged_tag_str:
                                        new_index = i
                                        break
                                if new_index is not None and hasattr(wnd, '_select_element_item'):
                                    wnd._select_element_item(src_doc, new_index)
                        except Exception:
                            pass
                except Exception:
                    pass
                event.acceptProposedAction()
                return
            except Exception:
                event.acceptProposedAction()
                # Fallback: let default handler run and then rebuild
                super().dropEvent(event)
                try:
                    wnd = self.window()
                    if hasattr(wnd, 'on_outline_reordered'):
                        wnd.on_outline_reordered()
                except Exception:
                    pass
                return

        # Default path (frames and other valid cases): let Qt move the items, then sync the model
        super().dropEvent(event)
        try:
            wnd = self.window()
            if hasattr(wnd, 'on_outline_reordered'):
                wnd.on_outline_reordered()
        except Exception:
            pass

    def dragLeaveEvent(self, event):  # type: ignore[override]
        try:
            self._hide_dnd_line()
        except Exception:
            pass
        super().dragLeaveEvent(event)

    def _ensure_dnd_line(self) -> QFrame:
        if self._dnd_line is None:
            self._dnd_line = QFrame(self.viewport())
            self._dnd_line.setFrameShape(QFrame.HLine)
            self._dnd_line.setFrameShadow(QFrame.Plain)
            # High-contrast line for clarity
            self._dnd_line.setStyleSheet("background-color: #4285F4; border: 0px; height: 2px;")
            self._dnd_line.hide()
        return self._dnd_line

    def _show_dnd_line(self, y: Optional[int]) -> None:
        try:
            if y is None:
                self._hide_dnd_line()
                return
            line = self._ensure_dnd_line()
            y_clamped = max(0, min(int(y), self.viewport().height() - 1))
            line.setGeometry(0, y_clamped, self.viewport().width(), 2)
            if not line.isVisible():
                line.show()
        except Exception:
            pass

    def _hide_dnd_line(self) -> None:
        try:
            if self._dnd_line is not None and self._dnd_line.isVisible():
                self._dnd_line.hide()
        except Exception:
            pass


class _LockedSplitterHandle(QSplitterHandle):
    def __init__(self, orientation, parent):  # type: ignore[no-redef]
        super().__init__(orientation, parent)

    def mousePressEvent(self, event):  # type: ignore[override]
        event.ignore()

    def mouseMoveEvent(self, event):  # type: ignore[override]
        event.ignore()

    def mouseReleaseEvent(self, event):  # type: ignore[override]
        event.ignore()


class _LockedSplitter(QSplitter):
    def createHandle(self) -> QSplitterHandle:  # type: ignore[override]
        return _LockedSplitterHandle(self.orientation(), self)

class _OutlineItemDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # type: ignore[no-redef]
        # Extract payload for row-level decision
        view = option.widget
        if hasattr(view, 'model'):
            model = index.model()
            try:
                payload = model.index(index.row(), 0, index.parent()).data(Qt.ItemDataRole.UserRole)
            except Exception:
                payload = None
        else:
            payload = None

        # Prepare a style option we can adjust
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget is not None else QApplication.style()

        # Custom draw column 0 text to bold the filename after a hyphen
        if index.column() == 0:
            # Draw base item without text
            saved_text = opt.text
            opt.text = ""
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
            opt.text = saved_text

            text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget)
            full_text = opt.text
            font_normal: QFont = opt.font
            font_bold = QFont(font_normal)
            font_bold.setBold(True)
            pen = painter.pen()
            pen.setColor(opt.palette.color(QPalette.ColorRole.Text))
            painter.setPen(pen)
            x = text_rect.x()
            y_rect = text_rect
            # Find last ' - ' and bold the part after it
            sep_idx = full_text.rfind(" - ")
            if sep_idx != -1:
                prefix = full_text[:sep_idx + 3]  # include ' - '
                tail = full_text[sep_idx + 3:]
                # Draw prefix
                painter.setFont(font_normal)
                fm = QFontMetrics(font_normal)
                painter.drawText(y_rect.adjusted(x - y_rect.x(), 0, 0, 0), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), prefix)
                x += fm.horizontalAdvance(prefix)
                # Draw tail bold
                painter.setFont(font_bold)
                fm_b = QFontMetrics(font_bold)
                painter.drawText(y_rect.adjusted(x - y_rect.x(), 0, 0, 0), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), tail)
            else:
                painter.setFont(font_normal)
                painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), full_text)
        else:
            # Default paint for other columns
            super().paint(painter, option, index)

        # Determine whether this row represents the active document (roots only)
        is_active_doc = False
        try:
            wnd = option.widget.window()
            active_doc_key = getattr(wnd, 'active_doc_key', None)
            if isinstance(payload, tuple):
                tag = payload[0]
                if tag == 'doc-root' and len(payload) >= 2:
                    is_active_doc = (payload[1] == active_doc_key)
        except Exception:
            pass

        rect = option.rect
        # Draw blue overlay for active document root regardless of selection
        if is_active_doc:
            painter.save()
            painter.setCompositionMode(QPainter.CompositionMode_Screen)
            painter.fillRect(rect, QBrush(QColor(66, 133, 244, 140)))
            painter.restore()

        # Then draw selection overlay (yellow) if selected, independent of active state
        if bool(option.state & QStyle.StateFlag.State_Selected):
            painter.save()
            painter.setCompositionMode(QPainter.CompositionMode_Screen)
            painter.fillRect(rect, QBrush(QColor(255, 235, 59, 110)))
            painter.restore()


class RfmEditorMainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("RFM Viewer & WYSIWYG Editor (beta)")
        self.resize(1200, 800)

        self.current_path: Optional[Path] = None
        self.document: Optional[RfmDocument] = None
        self.documents_by_key: dict[str, RfmDocument] = {}
        self.doc_display_names: dict[str, str] = {}
        self.main_doc_key: Optional[str] = None
        # Track active document and last-selected frame for persistent outline highlights
        self.active_doc_key: Optional[str] = None
        self.active_frame_doc_key: Optional[str] = None
        self.active_frame_name: Optional[str] = None
        self.dirty: bool = False
        self.renderer = RfmRenderer()
        # Persistent settings for menu directory
        self.settings = QSettings("dynamic_sof_apps", "rfm_editor")
        mrd = self.settings.value("menu_root_dir", "")
        self.menu_root: Optional[Path] = Path(mrd) if isinstance(mrd, str) and mrd else None

        # UI
        self._init_menu()
        self._init_central()
        self._init_statusbar()
        # Start in limited state until a document is created or opened
        try:
            self._set_editing_enabled(False)
        except Exception:
            pass

    def _init_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")

        self.new_action = QAction("New", self)
        self.new_action.setShortcut(QKeySequence("Ctrl+N"))
        self.new_action.triggered.connect(self.on_new)
        file_menu.addAction(self.new_action)

        self.open_action = QAction("Open .rmf...", self)
        self.open_action.triggered.connect(self.on_open)
        file_menu.addAction(self.open_action)

        self.save_action = QAction("Save", self)
        self.save_action.triggered.connect(self.on_save)
        file_menu.addAction(self.save_action)

        self.save_as_action = QAction("Save As...", self)
        self.save_as_action.triggered.connect(self.on_save_as)
        file_menu.addAction(self.save_as_action)

        # Open Recent submenu
        self.recent_menu = file_menu.addMenu("Open Recent")
        self.max_recent = 12
        self._load_recent_files()
        self._rebuild_recent_menu()

        self.export_cfg_action = QAction("Export to .cfg...", self)
        self.export_cfg_action.triggered.connect(self.on_export_cfg)
        file_menu.addAction(self.export_cfg_action)

        close_action = QAction("Close", self)
        try:
            close_action.setShortcut(QKeySequence.StandardKey.Close)  # type: ignore[attr-defined]
        except Exception:
            close_action.setShortcut(QKeySequence("Ctrl+W"))
        close_action.triggered.connect(self.on_close_document)
        file_menu.addAction(close_action)

        file_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        edit_menu = self.menuBar().addMenu("Edit")
        self.del_action = QAction("Delete Selected", self)
        self.del_action.setShortcut(QKeySequence(Qt.Key.Key_Delete))
        self.del_action.triggered.connect(self.on_delete_selected)
        edit_menu.addAction(self.del_action)

        insert_menu = self.menuBar().addMenu("Insert")
        self.ins_frame_action = QAction("Frame…", self)
        self.ins_frame_action.triggered.connect(self.on_insert_frame)
        insert_menu.addAction(self.ins_frame_action)

        self.ins_text_action = QAction("Text…", self)
        self.ins_text_action.triggered.connect(self.on_insert_text)
        insert_menu.addAction(self.ins_text_action)

        self.ins_image_action = QAction("Image…", self)
        self.ins_image_action.triggered.connect(self.on_insert_image)
        insert_menu.addAction(self.ins_image_action)

        insert_menu.addSeparator()
        self.ins_hr_action = QAction("Horizontal Rule", self)
        self.ins_hr_action.triggered.connect(self.on_insert_hr)
        insert_menu.addAction(self.ins_hr_action)

        insert_menu.addSeparator()
        self.ins_backdrop_action = QAction("Backdrop…", self)
        self.ins_backdrop_action.triggered.connect(self.on_insert_backdrop)
        insert_menu.addAction(self.ins_backdrop_action)

        settings_menu = self.menuBar().addMenu("Settings")
        set_dir = QAction("Set Menu Directory...", self)
        set_dir.triggered.connect(self.on_set_menu_dir)
        settings_menu.addAction(set_dir)

        # Screen Ratio submenu
        ratio_menu = settings_menu.addMenu("Screen Ratio")
        self.ratio_actions: dict[str, QAction] = {}
        ratio_group = QActionGroup(self)
        ratio_group.setExclusive(True)
        for label in ("4:3", "16:9", "16:10"):
            act = QAction(label, self)
            act.setCheckable(True)
            act.triggered.connect(lambda checked=False, l=label: self.on_set_screen_ratio(l))
            ratio_group.addAction(act)
            ratio_menu.addAction(act)
            self.ratio_actions[label] = act

        # Load persisted ratio and apply
        try:
            saved_ratio = self.settings.value("screen_ratio", "4:3")
            if not isinstance(saved_ratio, str) or saved_ratio not in self.ratio_actions:
                saved_ratio = "4:3"
        except Exception:
            saved_ratio = "4:3"
        self.on_set_screen_ratio(saved_ratio, initializing=True)

        # Style menu bar and menus to distinguish from app background
        try:
            self.menuBar().setStyleSheet(
                """
                QMenuBar {
                    background-color: #343A46; /* distinct from dark content */
                    color: #E6E6E6;
                }
                QMenuBar::item {
                    background: transparent;
                    padding: 4px 10px;
                }
                QMenuBar::item:selected {
                    background: #4A5668;
                }
                QMenu {
                    background-color: #2C323C;
                    color: #E6E6E6;
                    border: 1px solid #505A66;
                }
                QMenu::item:selected {
                    background: #4A5668;
                }
                """
            )
        except Exception:
            pass

    def _init_central(self) -> None:
        container = QWidget(self)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = _LockedSplitter(Qt.Orientation.Horizontal, container)
        layout.addWidget(splitter)

        # Left: Hierarchy / outline tree
        self.outline = _OutlineTree(splitter)
        self.outline.setHeaderLabels(["Element", "Summary"]) 
        # Make Element column auto-size to fit content, Summary stretches
        header: QHeaderView = self.outline.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        self.outline.itemSelectionChanged.connect(self.on_outline_selection)
        self.outline.itemExpanded.connect(lambda *_: None)
        self.outline.itemCollapsed.connect(lambda *_: None)
        self.outline.setMinimumWidth(64)
        # Strong visual selection in yellow across the full row
        self.outline.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Ensure the selection is visible even when the widget loses focus; we draw our own overlay.
        # Also keep foreground text color unchanged for readability.
        self.outline.setStyleSheet(
            "QTreeWidget::item:selected:active{background: transparent; color: palette(text);} "
            "QTreeWidget::item:selected:!active{background: transparent; color: palette(text);} "
            "QTreeWidget::item{color: palette(text);}"
        )
        self.outline.setItemDelegate(_OutlineItemDelegate(self.outline))
        # Enable context menu for delete
        self.outline.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.outline.customContextMenuRequested.connect(self._on_outline_context_menu)
        # Enable drag/drop reordering
        self.outline.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.outline.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.outline.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.outline.setDragEnabled(True)
        self.outline.setAcceptDrops(True)
        self.outline.setDropIndicatorShown(True)

        # Center: Graphics view
        self.scene = QGraphicsScene(self)
        self.view = _NoVScrollGraphicsView(self.scene, splitter)
        self.view.setRenderHints(self.view.renderHints())
        # Never show scrollbars; we scale to height and fix width accordingly
        try:
            self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            # Center horizontally when there is extra space; keep top-aligned vertically
            self.view.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            # Remove widget frame to avoid 1px visual borders
            self.view.setFrameShape(QFrame.NoFrame)
            # Enforce maximum size for the view area: 640 x maxY
            self.view.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            # Set a placeholder; will be set precisely based on renderer profile below
            self.view.setMinimumSize(QSize(640, 480))
            self.view.setMaximumSize(QSize(640, 480))
        except Exception:
            pass
        self.selection_overlay = None  # QGraphicsRectItem

        # Right: Placeholder for property editor (future expansion)
        self.props = QTreeWidget(splitter)
        self.props.setHeaderLabels(["Property", "Value"]) 
        self.props.setMinimumWidth(64)
        self.props.itemChanged.connect(self.on_prop_item_changed)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)

        self.setCentralWidget(container)
        # Apply fixed-size profile to match current screen ratio
        try:
            self._apply_fixed_view_profile()
        except Exception:
            pass

    def _init_statusbar(self) -> None:
        sb = QStatusBar(self)
        self.setStatusBar(sb)

    def on_set_screen_ratio(self, label: str, initializing: bool = False) -> None:
        # Map ratio label to max Y
        ratio_to_max_y = {
            "4:3": 480,
            "16:9": 360,
            "16:10": 400,
        }
        max_y = ratio_to_max_y.get(label, 480)
        # Update renderer screen profile
        try:
            self.renderer.max_screen_width = 640
            self.renderer.max_screen_height = max_y
        except Exception:
            pass
        # Update view max size to reflect current profile
        try:
            self.view.setMaximumSize(QSize(640, max_y))
        except Exception:
            pass
        # Persist selection
        try:
            self.settings.setValue("screen_ratio", label)
            self.settings.sync()
        except Exception:
            pass
        # Update menu check state
        try:
            if hasattr(self, 'ratio_actions') and label in self.ratio_actions:
                for k, act in self.ratio_actions.items():
                    act.setChecked(k == label)
        except Exception:
            pass
        # Re-render scene to reflect new profile
        try:
            self.refresh_scene()
            if not initializing:
                self.statusBar().showMessage(f"Screen ratio set to {label} (max Y = {max_y})", 4000)
        except Exception:
            pass

    # Actions
    def on_open(self) -> None:
        start_dir = str(self.menu_root or (self.current_path.parent if self.current_path else Path.cwd()))
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Open RFM file",
            start_dir,
            "Raven Menu Format (*.rmf);;All Files (*)",
        )
        if not path_str:
            return
        if not self._maybe_save_changes():
            return
        # Reset workspace so this open starts from a clean slate
        self._reset_workspace()
        self.load_file(Path(path_str))

    def load_file(self, path: Path) -> None:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Error", f"Failed to read file:\n{e}")
            return
        try:
            self.document = parse_rfm_content(text, file_path=str(path))
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Parse Error", f"Failed to parse:\n{e}")
            return

        self.current_path = path
        self.setWindowTitle(f"RFM Viewer & WYSIWYG Editor — {path.name}")
        self.statusBar().showMessage(f"Loaded {path}", 5000)
        if self.document and self.document.file_path:
            self.documents_by_key[self.document.file_path] = self.document
            if not self.main_doc_key:
                self.main_doc_key = self.document.file_path
            # Auto-preload referenced pages for this document
            try:
                self._autopreload_pages(self.document.file_path)
            except Exception:
                pass
            # Add to recent
            try:
                resolved = str(Path(self.document.file_path).resolve())
            except Exception:
                resolved = self.document.file_path
            self._add_to_recent(resolved)
            # Set last-startup file so the next launch can reopen this file
            try:
                self.settings.setValue("last_startup_file", resolved)
                self.settings.sync()
            except Exception:
                pass
        else:
            # Opened a transient/untitled doc; clear last_startup_file
            try:
                self.settings.setValue("last_startup_file", "")
                self.settings.sync()
            except Exception:
                pass
        # File open puts editor into editable state, and records it as the active doc
        self._set_editing_enabled(True)
        # Activate the loaded document so the blue highlight applies to its root
        if self.document and self.document.file_path:
            self._set_active_document(self.document.file_path)
        else:
            # Fallback to current_path when file_path not set
            try:
                self._set_active_document(str(self.current_path))
            except Exception:
                self.refresh_outline()
                self.refresh_scene()

    def _reset_workspace(self) -> None:
        # Clear all open docs and UI state for a fresh start
        self.documents_by_key.clear()
        self.doc_display_names.clear()
        self.main_doc_key = None
        self.active_doc_key = None
        self.active_frame_doc_key = None
        self.active_frame_name = None
        self.document = None
        self.current_path = None
        self.dirty = False
        try:
            self.outline.clear()
            self.scene.clear()
            self._clear_selection_overlay()
        except Exception:
            pass
        # Disable editing/inserting until a new document is created/opened
        self._set_editing_enabled(False)

    def _ensure_default_document(self) -> str:
        """Ensure there is at least one document in the workspace. Returns its key."""
        if self.documents_by_key:
            # Keep the current main doc
            return self.main_doc_key or next(iter(self.documents_by_key.keys()))
        # Create an unsaved, untitled document and register it
        if not self.document:
            self.document = RfmDocument()
        untitled_key = "Untitled.rmf"
        self.documents_by_key[untitled_key] = self.document
        self.main_doc_key = untitled_key
        self.active_doc_key = untitled_key
        # Do not set file_path or recents; render and outline will still work
        return untitled_key

    def _set_editing_enabled(self, enabled: bool) -> None:
        # Enable/disable actions that modify the current document
        for act in [
            getattr(self, 'save_action', None),
            getattr(self, 'save_as_action', None),
            getattr(self, 'export_cfg_action', None),
            getattr(self, 'del_action', None),
            getattr(self, 'ins_frame_action', None),
            getattr(self, 'ins_text_action', None),
            getattr(self, 'ins_image_action', None),
            getattr(self, 'ins_hr_action', None),
            getattr(self, 'ins_backdrop_action', None),
        ]:
            try:
                if act is not None:
                    act.setEnabled(enabled)
            except Exception:
                pass

    def on_new(self) -> None:
        if not self._maybe_save_changes():
            return
        # Reset and start a blank document
        self._reset_workspace()
        key = self._ensure_default_document()
        # Re-enable editing for the fresh document
        self._set_editing_enabled(True)
        # Set title against the display name
        self.setWindowTitle("RFM Viewer & WYSIWYG Editor — Untitled")
        # Show in UI
        self._set_active_document(key)
        self.statusBar().showMessage("New document", 3000)

    def on_close_document(self) -> None:
        if not self._maybe_save_changes():
            return
        # Close current document and clear the workspace without exiting
        self._reset_workspace()
        # Enter limited state: do not create a default doc; keep actions disabled until New/Open
        # Clear last-startup file so next run opens nothing
        try:
            self.settings.setValue("last_startup_file", "")
            self.settings.sync()
        except Exception:
            pass
        self.setWindowTitle("RFM Viewer & WYSIWYG Editor (beta)")
        self.statusBar().showMessage("Closed document", 3000)

    def _maybe_save_changes(self) -> bool:
        """Ask to save if there are unsaved changes. Returns True to proceed, False to cancel."""
        try:
            if not getattr(self, "dirty", False):
                return True
            ret = QMessageBox.question(
                self,
                "Unsaved Changes",
                "You have unsaved changes. Save before continuing?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes,
            )
            if ret == QMessageBox.Cancel:
                return False
            if ret == QMessageBox.Yes:
                # Attempt to save; if user cancels Save As, keep operation cancelled
                self.on_save()
                if getattr(self, "dirty", False):
                    return False
            return True
        except Exception:
            return True

    def refresh_outline(self) -> None:
        # Snapshot current expansion state
        expanded_keys = self._snapshot_expanded_keys()
        self.outline.clear()
        if not self.documents_by_key:
            return
        # Multiple roots: main first
        keys = list(self.documents_by_key.keys())
        if hasattr(self, 'main_doc_key') and self.main_doc_key in keys:
            keys.remove(self.main_doc_key)
            ordered = [self.main_doc_key] + sorted(keys)
        else:
            ordered = sorted(keys)

        for key in ordered:
            doc = self.documents_by_key[key]
            if getattr(self, 'main_doc_key', None) == key:
                title = f"Entry - {Path(key).name}"
            else:
                # Default label if no frame label exists
                default_label = f"Document — {Path(key).name}"
                # Use frame-based label if present
                # Format: Frame - <frameName> - <filename.rmf>
                title = self.doc_display_names.get(key, default_label)
            root = QTreeWidgetItem([title, "<stm>…</stm>"])
            root.setData(0, Qt.ItemDataRole.UserRole, ("doc-root", key))
            # Root: not draggable, not droppable
            try:
                flags = root.flags()
                flags &= ~Qt.ItemFlag.ItemIsDragEnabled
                flags &= ~Qt.ItemFlag.ItemIsDropEnabled
                root.setFlags(flags)
            except Exception:
                pass
            self.outline.addTopLevelItem(root)

            # Frames
            frames = QTreeWidgetItem(["Frames", str(len(doc.frames))])
            frames.setData(0, Qt.ItemDataRole.UserRole, ("doc-category", key, "frames"))
            try:
                flags = frames.flags()
                # Accept drops (for frames), but don't allow dragging the category itself
                flags |= Qt.ItemFlag.ItemIsDropEnabled
                flags &= ~Qt.ItemFlag.ItemIsDragEnabled
                frames.setFlags(flags)
            except Exception:
                pass
            root.addChild(frames)
            for frame in doc.frames.values():
                item = QTreeWidgetItem([f"frame {frame.name}", f"{frame.width}x{frame.height}"])
                item.setData(0, Qt.ItemDataRole.UserRole, ("frame", key, frame.name))
                try:
                    flags = item.flags()
                    flags |= Qt.ItemFlag.ItemIsDragEnabled
                    flags &= ~Qt.ItemFlag.ItemIsDropEnabled
                    # Reordering above/below is handled by view; child not a drop target as a child
                    item.setFlags(flags)
                except Exception:
                    pass
                frames.addChild(item)
                if getattr(frame, "page", None):
                    page_node = QTreeWidgetItem([f"page {frame.page}", ""]) 
                    page_node.setData(0, Qt.ItemDataRole.UserRole, ("doc-page", key, frame.page, frame.name))
                    try:
                        flags = page_node.flags()
                        flags &= ~Qt.ItemFlag.ItemIsDragEnabled
                        flags &= ~Qt.ItemFlag.ItemIsDropEnabled
                        page_node.setFlags(flags)
                    except Exception:
                        pass
                    item.addChild(page_node)

            # Backdrop
            if doc.backdrop_segment_index is not None:
                bd = QTreeWidgetItem(["backdrop", (doc.backdrop_mode or "") + (f" {doc.backdrop_bgcolor}" if doc.backdrop_bgcolor else "")])
                bd.setData(0, Qt.ItemDataRole.UserRole, ("doc-backdrop", key))
                try:
                    flags = bd.flags()
                    flags &= ~Qt.ItemFlag.ItemIsDragEnabled
                    flags &= ~Qt.ItemFlag.ItemIsDropEnabled
                    bd.setFlags(flags)
                except Exception:
                    pass
                root.addChild(bd)

            # Elements
            elems_parent = QTreeWidgetItem(["Elements", str(len(doc.elements))])
            elems_parent.setData(0, Qt.ItemDataRole.UserRole, ("doc-category", key, "elements"))
            try:
                flags = elems_parent.flags()
                flags |= Qt.ItemFlag.ItemIsDropEnabled
                flags &= ~Qt.ItemFlag.ItemIsDragEnabled
                elems_parent.setFlags(flags)
            except Exception:
                pass
            root.addChild(elems_parent)
            for elem in doc.elements:
                label = f"<{elem.name}>"
                summary = elem.summary()
                item = QTreeWidgetItem([label, summary])
                item.setData(0, Qt.ItemDataRole.UserRole, ("element", key, elem.segment_index))
                try:
                    flags = item.flags()
                    flags |= Qt.ItemFlag.ItemIsDragEnabled
                    flags &= ~Qt.ItemFlag.ItemIsDropEnabled
                    # Not enabling drop on element item itself keeps reorder clean
                    item.setFlags(flags)
                except Exception:
                    pass
                elems_parent.addChild(item)

            # Defaults: expanded; will restore explicit states next
            root.setExpanded(True)
            frames.setExpanded(True)
            elems_parent.setExpanded(True)
        # Auto-resize Element column to fit content and ensure tree min-width keeps it readable
        try:
            self.outline.resizeColumnToContents(0)
            minw = self.outline.sizeHintForColumn(0) + 60
            if minw > self.outline.minimumWidth():
                self.outline.setMinimumWidth(minw)
        except Exception:
            pass
        # Restore previous expansion state
        self._restore_expanded_keys(expanded_keys)

    def refresh_scene(self) -> None:
        # Remove selection overlay first to avoid removing a deleted item after scene.clear()
        self._clear_selection_overlay()
        self.scene.clear()
        if not self.document:
            return
        self.renderer.render_document(self.document, self.scene)
        # Fixed-size view: ensure 1:1 pixels and apply fixed profile
        try:
            self.view.resetTransform()
            self.scene.setSceneRect(getattr(self.renderer, 'content_rect', self.scene.itemsBoundingRect()))
            self._apply_fixed_view_profile()
        except Exception:
            pass

    def _apply_fixed_view_profile(self) -> None:
        # Fix the view to exactly 640 x maxY (based on current ratio in renderer), no scaling
        width = getattr(self.renderer, 'max_screen_width', 640) or 640
        height = getattr(self.renderer, 'max_screen_height', 480) or 480
        try:
            self.view.setMinimumSize(QSize(width, height))
            self.view.setMaximumSize(QSize(width, height))
            self.view.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        except Exception:
            pass
        # Do not impose a window minimum width here; only the view is fixed-size.

    def resizeEvent(self, event):  # type: ignore[override]
        try:
            super().resizeEvent(event)
        except Exception:
            pass
        # No scaling on resize; keep fixed-size view

    def _snapshot_expanded_keys(self) -> set[str]:
        keys: set[str] = set()
        def visit(item: QTreeWidgetItem) -> None:
            payload = item.data(0, Qt.ItemDataRole.UserRole)
            if payload is not None and item.isExpanded():
                try:
                    keys.add(str(payload))
                except Exception:
                    pass
            for i in range(item.childCount()):
                visit(item.child(i))
        for i in range(self.outline.topLevelItemCount()):
            visit(self.outline.topLevelItem(i))
        return keys

    def _restore_expanded_keys(self, keys: set[str]) -> None:
        def visit(item: QTreeWidgetItem) -> None:
            payload = item.data(0, Qt.ItemDataRole.UserRole)
            if payload is not None and str(payload) in keys:
                item.setExpanded(True)
            for i in range(item.childCount()):
                visit(item.child(i))
        for i in range(self.outline.topLevelItemCount()):
            visit(self.outline.topLevelItem(i))

    def on_outline_selection(self) -> None:
        items = self.outline.selectedItems()
        if not items:
            self._clear_selection_overlay()
            return
        payload = items[0].data(0, Qt.ItemDataRole.UserRole)
        # Multi-document aware selection
        if isinstance(payload, tuple):
            tag = payload[0]
            if tag == "doc-root":
                _, doc_key = payload
                self._set_active_document(doc_key)
                # Ensure the selected root remains selected after refresh
                self._select_doc_root_item(doc_key)
                # Clear frame highlight when selecting a document root
                self.active_frame_doc_key = None
                self.active_frame_name = None
                return
            if tag == "doc-category":
                # Switch active document when selecting category nodes like Frames/Elements
                _, doc_key, _cat = payload
                self._set_active_document(doc_key)
                return
            if tag == "doc-page":
                if len(payload) >= 4:
                    _, base_key, page_name, frame_name = payload
                else:
                    _, base_key, page_name = payload
                    frame_name = None
                self._open_or_switch_page(page_name, base_key=base_key, frame_name=frame_name)
                return
            if tag == "doc-backdrop":
                _, doc_key = payload
                if not (self.document and self.document.file_path == doc_key):
                    self._set_active_document(doc_key)
                self.populate_props(("backdrop", None))
                self._highlight_payload(("backdrop", None))
                self._select_backdrop_item(doc_key)
                return
            if tag == "frame":
                _, doc_key, frame_name = payload
                if not (self.document and self.document.file_path == doc_key):
                    self._set_active_document(doc_key)
                # Persist last selected frame for blue highlight in outline
                self.active_frame_doc_key = doc_key
                self.active_frame_name = frame_name
                frame = self.documents_by_key[doc_key].frames.get(frame_name)
                if frame:
                    self.populate_props(frame)
                    self._highlight_payload(frame)
                    self._select_frame_item(doc_key, frame_name)
                return
            if tag == "element":
                _, doc_key, seg_index = payload
                if not (self.document and self.document.file_path == doc_key):
                    self._set_active_document(doc_key)
                # Find element by segment index
                doc = self.documents_by_key[doc_key]
                elem = next((e for e in doc.elements if e.segment_index == seg_index), None)
                if elem:
                    self.populate_props(elem)
                    self._highlight_payload(elem)
                    self._select_element_item(doc_key, seg_index)
                return
        # Fallback single-doc behavior
        self.populate_props(payload)
        self._highlight_payload(payload)
        # Try to select the corresponding outline row for visibility
        try:
            from .rfm_model import RfmFrame, RfmElement
            if isinstance(payload, RfmFrame) and self.document and self.document.file_path:
                self._select_frame_item(self.document.file_path, payload.name)
            elif isinstance(payload, RfmElement) and self.document and self.document.file_path:
                self._select_element_item(self.document.file_path, payload.segment_index)
        except Exception:
            pass
 

    def _open_or_switch_page(self, page_name: str, base_key: Optional[str] = None, frame_name: Optional[str] = None) -> None:
        # Resolve via helper considering configured menu dir
        candidate = self._resolve_page_candidate_from_base(page_name, base_key)
        try:
            key = str(candidate.resolve())
        except Exception:
            key = str(candidate)
        if key in self.documents_by_key:
            # Ensure label is set if coming from a named frame
            if frame_name and key not in self.doc_display_names:
                self.doc_display_names[key] = f"Frame {str(frame_name)} - {Path(candidate).name}"
            # switch with safe UI update
            self._set_active_document(key)
            self.statusBar().showMessage(f"Switched to {key}", 5000)
            return
        # If not exists, prompt to create new
        if not candidate.exists():
            ret = QMessageBox.question(
                self,
                "Create Page",
                f"Page not found on disk:\n{candidate}\nCreate a new file?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if ret != QMessageBox.Yes:
                return
            # Create a minimal document with stm wrapper and no content
            try:
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_text("<stm>\n\n</stm>\n", encoding='utf-8')
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(self, "Error", f"Failed to create file:\n{e}")
                return
        # Load existing
        try:
            text = candidate.read_text(encoding='utf-8')
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Error", f"Failed to read page:\n{e}")
            return
        doc = parse_rfm_content(text, file_path=str(candidate))
        self.documents_by_key[key] = doc
        # If we know which frame referenced this page, label the document root accordingly
        if frame_name:
            self.doc_display_names[key] = f"Frame {str(frame_name)} - {Path(candidate).name}"
        if not hasattr(self, 'main_doc_key') or not self.main_doc_key:
            self.main_doc_key = key
        self._set_active_document(key)
        # Add to recent
        self._add_to_recent(key)
        self.statusBar().showMessage(f"Opened {candidate}", 5000)

    def _set_active_document(self, key: str) -> None:
        if key not in self.documents_by_key:
            return
        self.document = self.documents_by_key[key]
        self.active_doc_key = key
        # Reset active frame marker when switching documents unless it points to this doc
        if self.active_frame_doc_key and self.active_frame_doc_key != key:
            self.active_frame_doc_key = None
            self.active_frame_name = None
        self.current_path = Path(key)
        self.setWindowTitle(f"RFM Viewer & WYSIWYG Editor — {Path(key).name}")
        # Prevent selection-change recursion while rebuilding
        self.outline.blockSignals(True)
        try:
            self.refresh_outline()
        finally:
            self.outline.blockSignals(False)
        self.refresh_scene()

    def _select_doc_root_item(self, key: str) -> None:
        try:
            self.outline.blockSignals(True)
            try:
                for i in range(self.outline.topLevelItemCount()):
                    item = self.outline.topLevelItem(i)
                    payload = item.data(0, Qt.ItemDataRole.UserRole)
                    if isinstance(payload, tuple) and len(payload) >= 2 and payload[0] == "doc-root" and payload[1] == key:
                        self.outline.setCurrentItem(item)
                        item.setSelected(True)
                        break
            finally:
                self.outline.blockSignals(False)
        except Exception:
            pass

    def _select_frame_item(self, doc_key: str, frame_name: str) -> None:
        try:
            self.outline.blockSignals(True)
            try:
                for i in range(self.outline.topLevelItemCount()):
                    root = self.outline.topLevelItem(i)
                    payload = root.data(0, Qt.ItemDataRole.UserRole)
                    if not (isinstance(payload, tuple) and payload[0] == "doc-root" and payload[1] == doc_key):
                        continue
                    # find "Frames" child
                    for j in range(root.childCount()):
                        group = root.child(j)
                        if group.text(0) != "Frames":
                            continue
                        for k in range(group.childCount()):
                            item = group.child(k)
                            p2 = item.data(0, Qt.ItemDataRole.UserRole)
                            if isinstance(p2, tuple) and p2[0] == "frame" and p2[2] == frame_name:
                                self.outline.setCurrentItem(item)
                                item.setSelected(True)
                                return
            finally:
                self.outline.blockSignals(False)
        except Exception:
            pass

    def _select_element_item(self, doc_key: str, seg_index: int) -> None:
        try:
            self.outline.blockSignals(True)
            try:
                for i in range(self.outline.topLevelItemCount()):
                    root = self.outline.topLevelItem(i)
                    payload = root.data(0, Qt.ItemDataRole.UserRole)
                    if not (isinstance(payload, tuple) and payload[0] == "doc-root" and payload[1] == doc_key):
                        continue
                    # find "Elements" child
                    for j in range(root.childCount()):
                        group = root.child(j)
                        if group.text(0) != "Elements":
                            continue
                        for k in range(group.childCount()):
                            item = group.child(k)
                            p2 = item.data(0, Qt.ItemDataRole.UserRole)
                            if isinstance(p2, tuple) and p2[0] == "element" and p2[2] == seg_index:
                                self.outline.setCurrentItem(item)
                                item.setSelected(True)
                                return
            finally:
                self.outline.blockSignals(False)
        except Exception:
            pass

    def _select_backdrop_item(self, doc_key: str) -> None:
        try:
            self.outline.blockSignals(True)
            try:
                for i in range(self.outline.topLevelItemCount()):
                    root = self.outline.topLevelItem(i)
                    payload = root.data(0, Qt.ItemDataRole.UserRole)
                    if not (isinstance(payload, tuple) and payload[0] == "doc-root" and payload[1] == doc_key):
                        continue
                    for j in range(root.childCount()):
                        item = root.child(j)
                        p2 = item.data(0, Qt.ItemDataRole.UserRole)
                        if isinstance(p2, tuple) and p2[0] == "doc-backdrop":
                            self.outline.setCurrentItem(item)
                            item.setSelected(True)
                            return
            finally:
                self.outline.blockSignals(False)
        except Exception:
            pass

    def _resolve_page_candidate(self, page_name: str) -> Path:
        p = Path(page_name)
        if not p.suffix:
            p = p.with_suffix('.rmf')
        if p.is_absolute():
            return p
        # Prefer current document directory
        if self.document and self.document.file_path:
            cand = Path(self.document.file_path).parent / p
            if cand.exists():
                return cand
        # Then configured menu root
        if self.menu_root:
            return self.menu_root / p
        # Then directory of currently opened file
        if self.current_path:
            return self.current_path.parent / p
        return Path.cwd() / p

    def _resolve_page_candidate_from_base(self, page_name: str, base_key: Optional[str]) -> Path:
        # Resolve relative to a specific base document (its directory), falling back to standard resolution
        if base_key and base_key in self.documents_by_key:
            base_doc = self.documents_by_key[base_key]
            if base_doc.file_path:
                p = Path(page_name)
                if not p.suffix:
                    p = p.with_suffix('.rmf')
                if p.is_absolute():
                    return p
                cand = Path(base_doc.file_path).parent / p
                if cand.exists():
                    return cand
        return self._resolve_page_candidate(page_name)

    def _autopreload_pages(self, base_key: str, visited: Optional[set[str]] = None) -> None:
        # Recursively preload referenced page documents for the given base document key
        if visited is None:
            visited = set()
        if base_key in visited:
            return
        visited.add(base_key)
        base_doc = self.documents_by_key.get(base_key)
        if not base_doc:
            return
        for frame in base_doc.frames.values():
            page = getattr(frame, 'page', None)
            if not page:
                continue
            candidate = self._resolve_page_candidate_from_base(page, base_key)
            # Produce a stable key
            try:
                key = str(candidate.resolve())
            except Exception:
                key = str(candidate)
            if key in self.documents_by_key:
                # Ensure a friendly label is present for preloaded docs
                try:
                    if frame.name and key not in self.doc_display_names:
                        self.doc_display_names[key] = f"Frame {frame.name} - {Path(candidate).name}"
                except Exception:
                    pass
                # Recurse into it
                self._autopreload_pages(key, visited)
                continue
            # Create missing files on demand as empty <stm> shells
            if not candidate.exists():
                try:
                    candidate.parent.mkdir(parents=True, exist_ok=True)
                    candidate.write_text("<stm>\n\n</stm>\n", encoding='utf-8')
                except Exception:
                    continue
            # Load
            try:
                text = candidate.read_text(encoding='utf-8')
            except Exception:
                continue
            subdoc = parse_rfm_content(text, file_path=str(candidate))
            self.documents_by_key[key] = subdoc
            # Label as a named frame document
            try:
                if frame.name:
                    self.doc_display_names[key] = f"Frame {frame.name} - {Path(candidate).name}"
            except Exception:
                pass
            # Recurse
            self._autopreload_pages(key, visited)

    def populate_props(self, payload: object) -> None:
        self.props.blockSignals(True)
        try:
            self.props.clear()
            if payload is None:
                return
            from .rfm_model import RfmFrame, RfmElement

            if isinstance(payload, RfmFrame):
                name_item = QTreeWidgetItem(["name", payload.name])
                name_item.setFlags(name_item.flags() | Qt.ItemFlag.ItemIsEditable)
                name_item.setData(0, Qt.ItemDataRole.UserRole, ("frame", "name", payload.name))

                w_item = QTreeWidgetItem(["width", str(payload.width)])
                w_item.setFlags(w_item.flags() | Qt.ItemFlag.ItemIsEditable)
                w_item.setData(0, Qt.ItemDataRole.UserRole, ("frame", "width", payload.name))

                h_item = QTreeWidgetItem(["height", str(payload.height)])
                h_item.setFlags(h_item.flags() | Qt.ItemFlag.ItemIsEditable)
                h_item.setData(0, Qt.ItemDataRole.UserRole, ("frame", "height", payload.name))

                tail_item = QTreeWidgetItem(["tail", payload.raw_tail])
                tail_item.setFlags(tail_item.flags() | Qt.ItemFlag.ItemIsEditable)
                tail_item.setData(0, Qt.ItemDataRole.UserRole, ("frame", "tail", payload.name))

                self.props.addTopLevelItem(name_item)
                self.props.addTopLevelItem(w_item)
                self.props.addTopLevelItem(h_item)
                self.props.addTopLevelItem(tail_item)
            elif isinstance(payload, RfmElement):
                type_item = QTreeWidgetItem(["tag", payload.name])
                type_item.setFlags(type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.props.addTopLevelItem(type_item)
            elif isinstance(payload, tuple) and payload[0] == "backdrop":
                # Backdrop properties
                mode_val = self.document.backdrop_mode or ""
                mode_item = QTreeWidgetItem(["mode", mode_val])
                mode_item.setFlags(mode_item.flags() | Qt.ItemFlag.ItemIsEditable)
                mode_item.setData(0, Qt.ItemDataRole.UserRole, ("backdrop", "mode", None))

                img_val = self.document.backdrop_image or ""
                img_item = QTreeWidgetItem(["image", img_val])
                img_item.setFlags(img_item.flags() | Qt.ItemFlag.ItemIsEditable)
                img_item.setData(0, Qt.ItemDataRole.UserRole, ("backdrop", "image", None))

                col_val = self.document.backdrop_bgcolor or ""
                col_item = QTreeWidgetItem(["bgcolor", col_val])
                col_item.setFlags(col_item.flags() | Qt.ItemFlag.ItemIsEditable)
                col_item.setData(0, Qt.ItemDataRole.UserRole, ("backdrop", "bgcolor", None))

                self.props.addTopLevelItem(mode_item)
                self.props.addTopLevelItem(img_item)
                self.props.addTopLevelItem(col_item)
            # After populating, widen the props view to fit content
            try:
                self._autosize_props_panel()
            except Exception:
                pass
        finally:
            self.props.blockSignals(False)

    def _update_text_tag(self, raw_tag: str, new_text: str) -> str:
        # Replace first argument of <text ...> with quoted new_text
        import re as _re
        inner = raw_tag[1:-1]
        m = _re.match(r"\s*text(\s+)(\"[^\"]*\"|[^>\s]+)?(.*)$", inner, flags=_re.IGNORECASE)
        if not m:
            return raw_tag
        space, first, rest = m.groups()
        first = f'"{new_text}"'
        rebuilt = f"<text{space}{first}{rest}>"
        return rebuilt

    def _update_image_tag(self, raw_tag: str, new_path: str) -> str:
        import re as _re
        inner = raw_tag[1:-1]
        m = _re.match(r"\s*image(\s+)(\"[^\"]*\"|[^>\s]+)?(.*)$", inner, flags=_re.IGNORECASE)
        if not m:
            return raw_tag
        space, first, rest = m.groups()
        first = new_path if new_path and not any(ch in new_path for ch in ' \"') else f'"{new_path}"'
        rebuilt = f"<image{space}{first}{rest}>"
        return rebuilt

    def on_prop_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 1:
            return
        payload = item.data(0, Qt.ItemDataRole.UserRole)
        if not payload or not self.document:
            return
        kind, key, ident = payload
        new_val = item.text(1)

        if kind == "frame":
            frame = self.document.frames.get(str(ident))
            if not frame:
                return
            if key == "name":
                # Rename: update dict keys and segment mapping
                if new_val and new_val != frame.name:
                    old_name = frame.name
                    frame.name = new_val
                    self.document.frames[new_val] = frame
                    del self.document.frames[old_name]
                    seg_idx = self.document.frame_segment_indices.pop(old_name)
                    self.document.frame_segment_indices[new_val] = seg_idx
            elif key == "width":
                try:
                    # Allow 0 to mean auto-fill screen width
                    frame.width = max(0, int(new_val))
                except ValueError:
                    return
            elif key == "height":
                try:
                    # Allow 0 to mean auto-fill screen height
                    frame.height = max(0, int(new_val))
                except ValueError:
                    return
            elif key == "tail":
                frame.raw_tail = new_val
            self.dirty = True
            self.refresh_outline()
            self.refresh_scene()
            self._highlight_payload(frame)
        elif kind == "element":
            seg_idx = int(ident)
            seg_kind, seg_value = self.document.segments[seg_idx]
            if seg_kind != "tag":
                return
            if key == "text":
                new_tag = self._update_text_tag(seg_value, new_val)
            elif key == "image":
                new_tag = self._update_image_tag(seg_value, new_val)
            else:
                return
            # commit
            self.document.segments[seg_idx] = ("tag", new_tag)
            # also update element in memory
            for el in self.document.elements:
                if el.segment_index == seg_idx:
                    el.raw_tag = new_tag
                    if key == "text":
                        el.text_content = new_val
                    elif key == "image":
                        el.image_path = new_val
                    break
            self.dirty = True
            self.refresh_outline()
            self.refresh_scene()
            # Reselect the same element by index if possible
            class _Dummy: pass
            _d = _Dummy()
            setattr(_d, 'segment_index', seg_idx)
            from .rfm_model import RfmElement
            self._highlight_payload(RfmElement(name="", raw_tag="", segment_index=seg_idx))
            try:
                self._autosize_props_panel()
            except Exception:
                pass
        elif kind == "backdrop":
            # Update model fields and underlying segment
            if key == "mode":
                new_mode = new_val.strip() or None
                if new_mode and new_mode not in {"tile", "stretch", "center", "left", "right"}:
                    return
                self.document.backdrop_mode = new_mode
            elif key == "image":
                self.document.backdrop_image = new_val.strip() or None
            elif key == "bgcolor":
                self.document.backdrop_bgcolor = new_val.strip() or None
            # Rebuild segment if present
            if self.document.backdrop_segment_index is not None:
                seg_idx = self.document.backdrop_segment_index
                # Trigger a serialize-reparse style rebuild for consistency
                from .rfm_serializer import serialize_rfm
                from .rfm_parser import parse_rfm_content
                text = serialize_rfm(self.document)
                self.document = parse_rfm_content(text)
            self.dirty = True
            self.refresh_outline()
            self.refresh_scene()
            self._highlight_payload(("backdrop", None))
            try:
                self._autosize_props_panel()
            except Exception:
                pass

    def _autosize_props_panel(self, min_floor: int = 240) -> None:
        # Compute needed width to fit "Property" and "Value" columns nicely, and
        # allow shrinking back down to a reasonable floor when content is smaller.
        try:
            header = self.props.header()
            # Resize columns to fit content first
            self.props.resizeColumnToContents(0)
            self.props.resizeColumnToContents(1)
            prop_w = self.props.sizeHintForColumn(0)
            val_w = self.props.sizeHintForColumn(1)
            # Consider header padding and vertical scrollbar width margin
            padding = 28
            needed = max(min_floor, prop_w + val_w + padding)
            if needed != self.props.minimumWidth():
                self.props.setMinimumWidth(needed)
                # Nudge layout to apply new min width
                self.props.updateGeometry()
        except Exception:
            pass

    def _clear_selection_overlay(self) -> None:
        overlay = self.selection_overlay
        if overlay is None:
            return
        try:
            # Only remove if still attached to a scene; guard against deleted C++ object
            try:
                attached = overlay.scene() is not None
            except Exception:
                attached = False
            if attached:
                self.scene.removeItem(overlay)
        except Exception:
            pass
        self.selection_overlay = None

    def _highlight_payload(self, payload: object) -> None:
        self._clear_selection_overlay()
        if not self.document:
            return
        rect = self.renderer.selection_rect_for(payload, self.document)
        if rect is None:
            return
        from PySide6.QtGui import QPen
        from PySide6.QtCore import Qt
        pen = QPen(Qt.GlobalColor.yellow)
        pen.setWidth(2)
        pen.setCosmetic(True)
        # Ensure the selection stroke is strictly inside the content bounds.
        try:
            half = pen.widthF() / 2.0
            # Only inset if it will not collapse the rectangle
            if rect.width() > pen.widthF() and rect.height() > pen.widthF():
                inner = rect.adjusted(half, half, -half, -half)
            else:
                inner = rect
            # Clamp to content area (screen) minus a 1px safety margin to avoid any bleed from AA
            try:
                from PySide6.QtCore import QRectF as _QRectF
                screen = getattr(self.renderer, 'content_rect', None)
                if screen is not None and isinstance(screen, _QRectF):
                    safe = screen.adjusted(1.0, 1.0, -1.0, -1.0)
                    inter = inner.intersected(safe)
                    if inter.width() > 0 and inter.height() > 0:
                        inner = inter
            except Exception:
                pass
        except Exception:
            inner = rect
        self.selection_overlay = self.scene.addRect(inner, pen)
        self.selection_overlay.setZValue(9999)

    def on_save(self) -> None:
        if not self.document:
            return
        target = Path(self.document.file_path) if self.document.file_path else self.current_path
        if not target:
            return self.on_save_as()
        try:
            text = serialize_rfm(self.document)
            Path(target).write_text(text, encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Save Error", f"Failed to save:\n{e}")
            return
        self.statusBar().showMessage(f"Saved {target}", 5000)
        self.dirty = False
        # Update window title with saved filename if needed
        if self.document and self.document.file_path:
            self.setWindowTitle(f"RFM Viewer & WYSIWYG Editor — {Path(self.document.file_path).name}")

    def on_save_as(self) -> None:
        if not self.document:
            return
        start_dir = str(self.current_path.parent) if self.current_path else str(
            Path.cwd()
        )
        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save RFM as",
            start_dir,
            "Raven Menu Format (*.rmf);;All Files (*)",
        )
        if not out_path:
            return
        self.current_path = Path(out_path)
        if self.document:
            self.document.file_path = str(self.current_path)
            self.documents_by_key[self.document.file_path] = self.document
            self._add_to_recent(str(self.current_path))
        self.on_save()

    def on_export_cfg(self) -> None:
        if not self.document:
            return
        # Late import to avoid cyc dependency and GUI import cost for CLI tool
        try:
            from parsers.rfm_parser.rfm_parser import RmfParser  # type: ignore
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Export Error", f"Could not import RmfParser:\n{e}")
            return

        text = serialize_rfm(self.document)
        seed_label = self.current_path.name if self.current_path else "untitled.rmf"
        parser = RmfParser()
        try:
            cvars = parser.parse_and_pack(text, seed_label)
            cfg_text = parser.generate_cfg_output(cvars)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Export Error", f"Failed to export to cfg:\n{e}")
            return

        start_dir = str(self.current_path.parent) if self.current_path else str(Path.cwd())
        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export .cfg",
            str(Path(start_dir) / (self.current_path.stem + ".cfg") if self.current_path else Path(start_dir) / "out.cfg"),
            "Config (*.cfg);;All Files (*)",
        )
        if not out_path:
            return
        try:
            Path(out_path).write_text(cfg_text, encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Export Error", f"Failed to write cfg:\n{e}")
            return
        self.statusBar().showMessage(f"Exported to {out_path}", 5000)

    # Insert actions
    def on_insert_frame(self) -> None:
        if not self.document:
            self.document = RfmDocument()
        name, ok = QInputDialog.getText(self, "New Frame", "Name:")
        if not ok or not name.strip():
            return
        width, ok = QInputDialog.getInt(self, "Frame Width", "Width:", 640, 0, 4096, 1)
        if not ok:
            return
        height, ok = QInputDialog.getInt(self, "Frame Height", "Height:", 480, 0, 4096, 1)
        if not ok:
            return
        from .rfm_model import RfmFrame
        frame = RfmFrame(name=name.strip(), width=width, height=height)
        # Append to segments
        tag = frame.to_tag_str()
        self.document.segments.append(("tag", tag))
        self.document.frames[frame.name] = frame
        self.document.frame_segment_indices[frame.name] = len(self.document.segments) - 1
        self.dirty = True
        self.refresh_outline()
        self.refresh_scene()

    # Recent files helpers
    def _load_recent_files(self) -> None:
        val = self.settings.value("recent_files", [], type=list)
        self.recent_files = list(val) if isinstance(val, list) else []

    def _save_recent_files(self) -> None:
        self.settings.setValue("recent_files", self.recent_files)

    def _rebuild_recent_menu(self) -> None:
        self.recent_menu.clear()
        if not getattr(self, 'recent_files', None):
            empty = QAction("No Recent Files", self)
            empty.setEnabled(False)
            self.recent_menu.addAction(empty)
            return
        for p in self.recent_files:
            act = QAction(p, self)
            act.triggered.connect(lambda checked=False, path=p: self._open_recent(path))
            self.recent_menu.addAction(act)
        self.recent_menu.addSeparator()
        clear_act = QAction("Clear Recent", self)
        clear_act.triggered.connect(self._clear_recent)
        self.recent_menu.addAction(clear_act)

    def _add_to_recent(self, path: str) -> None:
        if not hasattr(self, 'recent_files'):
            self.recent_files = []
        # Normalize and dedupe
        try:
            path = str(Path(path).resolve())
        except Exception:
            path = str(path)
        self.recent_files = [p for p in self.recent_files if p != path]
        self.recent_files.insert(0, path)
        if len(self.recent_files) > self.max_recent:
            self.recent_files = self.recent_files[: self.max_recent]
        self._save_recent_files()
        self._rebuild_recent_menu()

    def _open_recent(self, path: str) -> None:
        p = Path(path)
        if not p.exists():
            ret = QMessageBox.question(
                self,
                "Missing File",
                f"File no longer exists:\n{path}\nRemove from recent list?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if ret == QMessageBox.Yes:
                self.recent_files = [x for x in self.recent_files if x != path]
                self._save_recent_files()
                self._rebuild_recent_menu()
            return
        if not self._maybe_save_changes():
            return
        # Reset workspace so opening from recent starts clean
        self._reset_workspace()
        self.load_file(p)

    def _clear_recent(self) -> None:
        self.recent_files = []
        self._save_recent_files()
        self._rebuild_recent_menu()

    def on_insert_text(self) -> None:
        if not self.document:
            self.document = RfmDocument()
        text, ok = QInputDialog.getText(self, "New Text", "Text:")
        if not ok:
            return
        tag = f'<text "{text}">' if text and (" " in text or '"' in text) else f"<text {text}>"
        self.document.segments.append(("tag", tag))
        from .rfm_model import RfmElement
        self.document.elements.append(RfmElement(name="text", raw_tag=tag, segment_index=len(self.document.segments) - 1, text_content=text))
        self.dirty = True
        self.refresh_outline()
        self.refresh_scene()

    def on_insert_image(self) -> None:
        if not self.document:
            self.document = RfmDocument()
        img, _ = QFileDialog.getOpenFileName(self, "Choose Image", str(self.menu_root or (self.current_path.parent if self.current_path else Path.cwd())), "Images (*.png *.jpg *.jpeg *.bmp *.m32);;All Files (*)")
        if not img:
            return
        img_token = img if ' ' not in img else f'"{img}"'
        tag = f"<image {img_token}>"
        self.document.segments.append(("tag", tag))
        from .rfm_model import RfmElement
        self.document.elements.append(RfmElement(name="image", raw_tag=tag, segment_index=len(self.document.segments) - 1, image_path=img))
        self.dirty = True
        self.refresh_outline()
        self.refresh_scene()

    def on_insert_hr(self) -> None:
        if not self.document:
            self.document = RfmDocument()
        tag = "<hr>"
        self.document.segments.append(("tag", tag))
        from .rfm_model import RfmElement
        self.document.elements.append(RfmElement(name="hr", raw_tag=tag, segment_index=len(self.document.segments) - 1))
        self.dirty = True
        self.refresh_outline()
        self.refresh_scene()

    def on_insert_backdrop(self) -> None:
        if not self.document:
            self.document = RfmDocument()
        # Ask mode
        modes = ["tile", "stretch", "center", "left", "right", "(none)"]
        mode, ok = QInputDialog.getItem(self, "Backdrop Mode", "Mode:", modes, current=2, editable=False)
        if not ok:
            return
        if mode == "(none)":
            mode = None
        # Ask optional image
        img, _ = QFileDialog.getOpenFileName(self, "Optional Backdrop Image", str(self.menu_root or (self.current_path.parent if self.current_path else Path.cwd())), "Images (*.png *.jpg *.jpeg *.bmp *.m32);;All Files (*)")
        img_token = None
        if img:
            img_token = img if ' ' not in img else f'"{img}"'
        # Ask bgcolor
        color, ok = QInputDialog.getText(self, "Backdrop BG Color", "ARGB (e.g. 0xff800000 or #ff800000):", text="0xff202020")
        if not ok:
            return
        # Build tag
        parts = ["<backdrop"]
        if mode:
            parts.append(mode)
        if img_token:
            parts.append(img_token)
        if color:
            parts.extend(["bgcolor", color])
        parts.append(">")
        tag = " ".join(parts)
        self.document.segments.append(("tag", tag))
        self.document.backdrop_segment_index = len(self.document.segments) - 1
        self.document.backdrop_mode = mode
        self.document.backdrop_image = img if img else None
        self.document.backdrop_bgcolor = color
        self.dirty = True
        self.refresh_outline()
        self.refresh_scene()

    def on_set_menu_dir(self) -> None:
        start = str(self.menu_root or Path.cwd())
        chosen = QFileDialog.getExistingDirectory(self, "Choose SOF Menu Directory", start)
        if not chosen:
            return
        self.menu_root = Path(chosen)
        self.settings.setValue("menu_root_dir", str(self.menu_root))
        self.statusBar().showMessage(f"Menu directory set to {self.menu_root}", 5000)

    def on_delete_selected(self) -> None:
        items = self.outline.selectedItems()
        if not items:
            return
        payload = items[0].data(0, Qt.ItemDataRole.UserRole)
        seg_idx: Optional[int] = None
        doc_key: Optional[str] = None
        from .rfm_model import RfmFrame, RfmElement
        # Tuples from outline
        if isinstance(payload, tuple):
            tag = payload[0] if payload else None
            if tag == "element" and len(payload) >= 3:
                _, doc_key, seg_idx_val = payload
                doc_key = str(doc_key)
                seg_idx = int(seg_idx_val)
            elif tag == "frame" and len(payload) >= 3:
                _, doc_key, frame_name = payload
                d = self.documents_by_key.get(str(doc_key))
                if d:
                    seg_idx = d.frame_segment_indices.get(str(frame_name))
                doc_key = str(doc_key)
            elif tag == "doc-backdrop" and len(payload) >= 2:
                _, doc_key = payload
                d = self.documents_by_key.get(str(doc_key))
                if d:
                    seg_idx = d.backdrop_segment_index
                doc_key = str(doc_key)
        # Legacy/object payloads
        elif isinstance(payload, RfmElement):
            seg_idx = payload.segment_index
            doc_key = self.document.file_path if self.document else None
        elif isinstance(payload, RfmFrame):
            name = payload.name
            doc_key = self.document.file_path if self.document else None
            if self.document:
                seg_idx = self.document.frame_segment_indices.get(name)

        if seg_idx is None or doc_key is None:
            return
        # Operate on the correct document without forcibly switching active doc unless needed
        doc = self.documents_by_key.get(doc_key)
        if not doc:
            return
        try:
            if seg_idx < 0 or seg_idx >= len(doc.segments):
                return
            del doc.segments[seg_idx]
        except Exception:
            return
        # Re-serialize and re-parse to maintain indices
        from .rfm_serializer import serialize_rfm
        from .rfm_parser import parse_rfm_content
        try:
            text = serialize_rfm(doc)
            new_doc = parse_rfm_content(text, file_path=doc.file_path)
            self.documents_by_key[doc_key] = new_doc
            # If this was the active document, update pointer so preview updates
            if getattr(self, 'active_doc_key', None) == doc_key:
                self.document = new_doc
        except Exception:
            pass
        self.dirty = True
        self.props.clear()
        self.refresh_outline()
        self.refresh_scene()

    def _on_outline_context_menu(self, pos) -> None:
        try:
            item = self.outline.itemAt(pos)
            if item is None:
                return
            menu = QMenu(self)
            del_act = QAction("Delete", self)
            del_act.triggered.connect(self.on_delete_selected)
            menu.addAction(del_act)
            menu.popup(self.outline.viewport().mapToGlobal(pos))
        except Exception:
            pass

    def on_outline_reordered(self) -> None:
        # Handle both intra-doc reordering and cross-doc frame moves
        if not self.documents_by_key:
            return
        try:
            # 1) Gather desired element order per doc (by old segment indices)
            elements_order_by_doc: dict[str, list[int]] = {}
            frames_layout_by_doc: dict[str, list[tuple[str, str]]] = {}

            for i in range(self.outline.topLevelItemCount()):
                root = self.outline.topLevelItem(i)
                payload = root.data(0, Qt.ItemDataRole.UserRole)
                if not (isinstance(payload, tuple) and payload[0] == 'doc-root'):
                    continue
                doc_key = payload[1]
                # Initialize
                elements_order_by_doc[doc_key] = []
                frames_layout_by_doc[doc_key] = []
                for j in range(root.childCount()):
                    group = root.child(j)
                    if group.text(0) == 'Frames':
                        for k in range(group.childCount()):
                            it = group.child(k)
                            p2 = it.data(0, Qt.ItemDataRole.UserRole)
                            if isinstance(p2, tuple) and p2[0] == 'frame' and len(p2) >= 3:
                                src_key = p2[1]
                                frame_name = p2[2]
                                frames_layout_by_doc[doc_key].append((src_key, frame_name))
                    elif group.text(0) == 'Elements':
                        order: list[int] = []
                        for k in range(group.childCount()):
                            it = group.child(k)
                            p2 = it.data(0, Qt.ItemDataRole.UserRole)
                            if isinstance(p2, tuple) and p2[0] == 'element' and len(p2) >= 3:
                                order.append(int(p2[2]))
                        elements_order_by_doc[doc_key] = order

            # 2) Apply element reordering within each doc using current UI order
            changed_docs: set[str] = set()
            for doc_key, ordered_seg_indices in elements_order_by_doc.items():
                if not ordered_seg_indices:
                    continue
                if self._reorder_elements_by_segment_indices_for_doc(doc_key, ordered_seg_indices):
                    changed_docs.add(doc_key)

            # Reparse documents whose segments changed to keep indices and caches consistent
            if changed_docs:
                from .rfm_serializer import serialize_rfm
                from .rfm_parser import parse_rfm_content
                for dk in list(changed_docs):
                    doc = self.documents_by_key.get(dk)
                    if not doc:
                        continue
                    try:
                        text = serialize_rfm(doc)
                        self.documents_by_key[dk] = parse_rfm_content(text, file_path=doc.file_path)
                    except Exception:
                        pass
                # If the active document changed, update the pointer so the scene reflects the new order
                try:
                    if getattr(self, 'active_doc_key', None) in changed_docs:
                        ak = self.active_doc_key
                        if ak and ak in self.documents_by_key:
                            self.document = self.documents_by_key[ak]
                except Exception:
                    pass

            # 3) Apply cross-doc frame layout
            self._apply_crossdoc_frame_layout(frames_layout_by_doc)

            # Refresh UI
            self.dirty = True
            self.refresh_outline()
            self.refresh_scene()
        except Exception:
            pass

    def _reorder_elements_by_segment_indices_for_doc(self, doc_key: str, ordered_indices: list[int]) -> bool:
        """Reorder only element segments within a document to match ordered_indices exactly.
        Returns True if the document's segments changed.
        """
        doc = self.documents_by_key.get(doc_key)
        if not doc:
            return False
        try:
            segments = list(doc.segments)
            # Determine which indices correspond to elements in this document
            element_idx_set = {e.segment_index for e in getattr(doc, 'elements', [])}
            if not element_idx_set:
                return False
            # Build a full ordered list: UI order first, then any leftover element indices preserving original order
            seen = set(int(x) for x in ordered_indices if int(x) in element_idx_set)
            leftover = [i for i in sorted(element_idx_set) if i not in seen]
            full_order = [int(x) for x in ordered_indices if int(x) in element_idx_set] + leftover
            # Map indices to entries
            ordered_entries: list[tuple[str, str]] = []
            for idx in full_order:
                if 0 <= idx < len(segments):
                    ordered_entries.append(segments[idx])
            if not ordered_entries:
                return False
            # Rebuild segments: replace element slots in their original positions with entries in new order
            new_segments: list[tuple[str, str]] = []
            it = iter(ordered_entries)
            changed = False
            for i, entry in enumerate(segments):
                if i in element_idx_set:
                    replacement = next(it)
                    new_segments.append(replacement)
                    if replacement is not entry:
                        changed = True
                else:
                    new_segments.append(entry)
            if changed:
                doc.segments = new_segments
            return changed
        except Exception:
            return False

    def _apply_crossdoc_frame_layout(self, frames_layout_by_doc: dict[str, list[tuple[str, str]]]) -> None:
        if not frames_layout_by_doc:
            return
        from .rfm_model import RfmFrame
        try:
            # Precompute current docs
            docs = self.documents_by_key
            # Build new frame lists for each destination doc
            new_frames_for_doc: dict[str, list[RfmFrame]] = {}
            for dest_key, items in frames_layout_by_doc.items():
                dest_doc = docs.get(dest_key)
                if not dest_doc:
                    continue
                used_names = set(dest_doc.frames.keys())
                ordered_frames: list[RfmFrame] = []
                for src_key, frame_name in items:
                    src_doc = docs.get(src_key)
                    if not src_doc:
                        continue
                    src_frame = src_doc.frames.get(frame_name)
                    if not src_frame:
                        continue
                    # Clone frame to avoid aliasing between docs
                    fr = RfmFrame(
                        name=src_frame.name,
                        width=src_frame.width,
                        height=src_frame.height,
                        raw_tail=src_frame.raw_tail,
                        page=src_frame.page,
                        border_width=src_frame.border_width,
                        border_line_width=src_frame.border_line_width,
                        border_line_color=src_frame.border_line_color,
                        backfill_color=src_frame.backfill_color,
                        tail_extra=src_frame.tail_extra,
                        preview_pos=src_frame.preview_pos,
                    )
                    # Ensure unique name in destination
                    if dest_key != src_key and fr.name in used_names:
                        fr.name = self._unique_frame_name(dest_doc, fr.name)
                    used_names.add(fr.name)
                    ordered_frames.append(fr)
                new_frames_for_doc[dest_key] = ordered_frames

            # Rebuild segments for each destination doc based on new frame lists
            for dest_key, new_frames in new_frames_for_doc.items():
                dest_doc = docs.get(dest_key)
                if not dest_doc:
                    continue
                old_segments = list(dest_doc.segments)
                kept: list[tuple[str, str]] = []
                first_frame_insert_pos = None
                # Remove existing frame tags and remember earliest frame position
                for idx, (kind, val) in enumerate(old_segments):
                    if kind == 'tag':
                        inner = val[1:-1].strip().lower()
                        if inner.startswith('frame '):
                            if first_frame_insert_pos is None:
                                first_frame_insert_pos = len(kept)
                            continue
                    kept.append((kind, val))
                insert_pos = first_frame_insert_pos if first_frame_insert_pos is not None else len(kept)
                # Insert frames in order
                for offset, fr in enumerate(new_frames):
                    kept.insert(insert_pos + offset, ('tag', fr.to_tag_str()))
                # Commit and reparse to rebuild indices
                dest_doc.segments = kept
                from .rfm_serializer import serialize_rfm
                from .rfm_parser import parse_rfm_content
                text = serialize_rfm(dest_doc)
                docs[dest_key] = parse_rfm_content(text, file_path=dest_doc.file_path)

            # For source docs that lost frames but are not listed as dest, we must still purge frames moved out
            # Compute set of frames that remain per doc from layout
            remaining_by_doc: dict[str, set[str]] = {}
            for dkey, items in frames_layout_by_doc.items():
                for src_key, name in items:
                    remaining_by_doc.setdefault(dkey, set()).add(name)
            for src_key, src_doc in list(docs.items()):
                if src_key not in frames_layout_by_doc:
                    # if not in layout, skip
                    continue
                # Already handled in dest rebuild above
                pass
        except Exception:
            pass

    def _unique_frame_name(self, dest_doc: RfmDocument, base_name: str) -> str:  # type: ignore[name-defined]
        # Generate a unique frame name for the destination document
        name = base_name
        if name not in dest_doc.frames:
            return name
        counter = 1
        while True:
            candidate = f"{base_name}_{counter}"
            if candidate not in dest_doc.frames:
                return candidate
            counter += 1


def main() -> None:
    app = QApplication(sys.argv)
    win = RfmEditorMainWindow()
    win.show()

    # Auto-open last-startup file if set and exists; otherwise start empty
    try:
        settings = QSettings("dynamic_sof_apps", "rfm_editor")
        last = settings.value("last_startup_file", "")
        if isinstance(last, str) and last:
            p = Path(last)
            if p.exists():
                win.load_file(p)
            else:
                settings.setValue("last_startup_file", "")
                settings.sync()
    except Exception:
        pass

    sys.exit(app.exec())


if __name__ == "__main__":
    main()


