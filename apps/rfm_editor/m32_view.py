from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap, QAction
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGraphicsScene,
    QGraphicsView,
    QGraphicsPixmapItem,
    QMainWindow,
    QStatusBar,
)

from .m32lib import qpixmap_from_m32_file
from .rfm_renderer import RfmRenderer


class M32Viewer(QMainWindow):
    def __init__(self, path_arg: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle("M32 Image Viewer")
        self.resize(800, 600)
        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene, self)
        self.setCentralWidget(self.view)
        self.setStatusBar(QStatusBar(self))

        # Use the same settings keys as the editor
        from PySide6.QtCore import QSettings

        settings = QSettings("dynamic_sof_apps", "rfm_editor")
        menu_root = settings.value("menu_root_dir", "")
        resource_root = settings.value("resource_root_dir", "")

        self.renderer = RfmRenderer()
        self.renderer.menu_root = menu_root if isinstance(menu_root, str) and menu_root else None
        self.renderer.resource_root = resource_root if isinstance(resource_root, str) and resource_root else None

        # Menu action to open file
        open_act = QAction("Open…", self)
        open_act.setShortcut("Ctrl+O")
        open_act.triggered.connect(self.open_dialog)
        self.menuBar().addAction(open_act)

        # Load initial path if provided
        if path_arg:
            self.load_path(path_arg)

    def open_dialog(self) -> None:
        start_dir = self.renderer.resource_root or self.renderer.menu_root or str(Path.cwd())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Image",
            start_dir,
            "Images (*.m32 *.png *.jpg *.jpeg *.bmp);;All Files (*)",
        )
        if path:
            self.load_path(path)

    def load_path(self, raw: str) -> None:
        self.scene.clear()
        # Resolve using the renderer's logic
        resolved = self.renderer._resolve_image_path(raw)
        pm: QPixmap | None = None
        if resolved:
            if resolved.lower().endswith(".m32"):
                pm = qpixmap_from_m32_file(resolved)
            else:
                pm = QPixmap(resolved)
        if pm is None or pm.isNull():
            # Last resort: try raw path directly
            if raw.lower().endswith(".m32"):
                pm = qpixmap_from_m32_file(raw)
            else:
                pm = QPixmap(raw)

        if pm is None or pm.isNull():
            self.statusBar().showMessage(f"Failed to load: {raw} (resolved: {resolved or 'n/a'})", 60000)
            return

        item: QGraphicsPixmapItem = self.scene.addPixmap(pm)
        try:
            item.setTransformationMode(Qt.TransformationMode.FastTransformation)
        except Exception:
            pass
        item.setOffset(0, 0)
        # Fit window to image, but cap to a reasonable size
        w = pm.width()
        h = pm.height()
        self.scene.setSceneRect(0, 0, w, h)
        self.view.resetTransform()
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setMinimumSize(QSize(min(1024, max(160, w)), min(768, max(120, h))))
        self.setWindowTitle(f"M32 Image Viewer — {Path(resolved or raw).name}  {w}x{h}")
        self.statusBar().showMessage(f"Loaded: {resolved or raw}", 10000)


def main() -> None:
    app = QApplication(sys.argv)
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    win = M32Viewer(arg)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()


