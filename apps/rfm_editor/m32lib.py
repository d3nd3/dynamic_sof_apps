from __future__ import annotations

import struct
from pathlib import Path
from typing import Optional

from PySide6.QtGui import QImage, QPixmap

MIPLEVELS = 16


def _get_type_size(type_name: str) -> int:
    if type_name == "int":
        return 4
    if type_name == "uint":
        return 4
    if type_name == "string":
        return 0
    if type_name == "float":
        return 4
    return 0


class _Field:
    @staticmethod
    def _mem_to_string(mem: memoryview, type_name: str, size: int = 128) -> str:
        raw = mem.tobytes()
        if type_name == "int":
            return str(struct.unpack("<i", raw)[0])
        if type_name == "uint":
            return str(struct.unpack("<I", raw)[0])
        if type_name == "string":
            s = struct.unpack(f"{size}s", raw)[0]
            s = s.split(b"\0", 1)[0]
            return s.decode(errors="ignore")
        if type_name == "float":
            return str(struct.unpack("<f", raw)[0])
        return ""

    def __init__(self, name: str, size: int, type_name: str, view: Optional[memoryview] = None) -> None:
        self.name = name
        self.size = size
        self.type_name = type_name
        self.memview: Optional[memoryview] = None if view is None else view[:size]

    def read(self):  # str | list[str]
        if self.type_name.startswith("list_"):
            return self._to_list()
        return self._to_string()

    def _to_string(self) -> str:
        return _Field._mem_to_string(self.memview, self.type_name) if self.memview is not None else ""

    def _to_list(self) -> list[str]:
        assert self.memview is not None
        t = self.type_name[5:]
        ts = _get_type_size(t)
        return [_Field._mem_to_string(self.memview[i : i + ts], t) for i in range(0, self.size, ts)]


class MipHeader:
    fields_spec = [
        _Field("version", 4, "int"),
        _Field("name", 128, "string"),
        _Field("altname", 128, "string"),
        _Field("animname", 128, "string"),
        _Field("damagename", 128, "string"),
        _Field("width", MIPLEVELS * 4, "list_uint"),
        _Field("height", MIPLEVELS * 4, "list_uint"),
        _Field("offsets", MIPLEVELS * 4, "list_uint"),
        _Field("flags", 4, "int"),
        _Field("contents", 4, "int"),
        _Field("value", 4, "int"),
        _Field("scale_x", 4, "float"),
        _Field("scale_y", 4, "float"),
        _Field("mip_scale", 4, "int"),
        _Field("dt_name", 128, "string"),
        _Field("dt_scale_x", 4, "float"),
        _Field("dt_scale_y", 4, "float"),
        _Field("dt_u", 4, "float"),
        _Field("dt_v", 4, "float"),
        _Field("dt_alpha", 4, "float"),
        _Field("dt_src_blend_mode", 4, "int"),
        _Field("dt_dst_blend_mode", 4, "int"),
        _Field("flags2", 4, "int"),
        _Field("damage_health", 4, "float"),
        _Field("unused", 18 * 4, "list_int"),
    ]

    def __init__(self, filename: str = "", infile_data: Optional[bytes] = None) -> None:
        self.filename = filename
        if infile_data is None:
            raise ValueError("MipHeader requires infile_data bytes")
        self._file = bytearray(infile_data)
        mview = memoryview(self._file)
        for field in MipHeader.fields_spec:
            f = _Field(field.name, field.size, field.type_name, mview[:])
            setattr(self, field.name, f)
            mview = mview[field.size :]

    def __len__(self) -> int:
        # Header length is fixed by spec
        return 968

    def header(self) -> memoryview:
        return memoryview(self._file)[: len(self)]

    def imgdata_view(self) -> memoryview:
        # Use top mip level width/height with header-provided offset when available
        w = int(self.width.read()[0])
        h = int(self.height.read()[0])
        total = w * h * 4
        # Offsets list may indicate start of top-level data
        try:
            offs_list = self.offsets.read()
            off0 = int(offs_list[0]) if offs_list else 0
        except Exception:
            off0 = 0
        start = off0 if 0 < off0 < len(self._file) else len(self)
        end = min(len(self._file), start + total)
        return memoryview(self._file)[start:end]

    def dimensions(self) -> tuple[int, int]:
        w = int(self.width.read()[0])
        h = int(self.height.read()[0])
        return w, h


def qimage_from_m32_bytes(m32_bytes: bytes) -> Optional[QImage]:
    try:
        mip = MipHeader("<bytes>", m32_bytes)
        w, h = mip.dimensions()
        buf = mip.imgdata_view()
        if w <= 0 or h <= 0 or len(buf) < w * h * 4:
            return None
        # Prepare RGBA bytes; if alpha appears fully 0, force opaque as a safety heuristic
        data = bytearray(buf)
        if all(data[i] == 0 for i in range(3, len(data), 4)):
            for i in range(3, len(data), 4):
                data[i] = 255
        # Create QImage with RGBA8888, then copy to own the data
        img = QImage(bytes(data), w, h, w * 4, QImage.Format_RGBA8888)
        return img.copy()
    except Exception:
        return None


def qpixmap_from_m32_file(path: str | Path) -> Optional[QPixmap]:
    p = Path(path)
    try:
        data = p.read_bytes()
    except Exception:
        return None
    img = qimage_from_m32_bytes(data)
    if img is None or img.isNull():
        return None
    return QPixmap.fromImage(img)
