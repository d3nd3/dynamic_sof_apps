from __future__ import annotations

import struct

from apps.rfm_editor.m32lib import qimage_from_m32_bytes


def make_fake_m32_bytes(width: int = 2, height: int = 2) -> bytes:
    # Build a minimal .m32-like blob that our parser understands
    header_size = 968
    header = bytearray(header_size)
    # Field offsets based on apps/rfm_editor/m32lib.MipHeader.fields_spec layout
    # version(4) + name(128) + altname(128) + animname(128) + damagename(128)
    width_off = 4 + 128 + 128 + 128 + 128  # 516
    height_off = width_off + 16 * 4  # 580
    offsets_off = height_off + 16 * 4  # 644
    # Fill version (optional)
    struct.pack_into("<i", header, 0, 1)
    # Top mip level width/height
    struct.pack_into("<I", header, width_off, width)
    struct.pack_into("<I", header, height_off, height)
    # Offsets[0] -> pixel data start
    struct.pack_into("<I", header, offsets_off, header_size)

    # RGBA pixel data for width x height
    data = bytearray()
    # 2x2 pattern: red, green, blue, white (fully opaque)
    px = [
        (255, 0, 0, 255),
        (0, 255, 0, 255),
        (0, 0, 255, 255),
        (255, 255, 255, 255),
    ]
    for (r, g, b, a) in px[: width * height]:
        data.extend((r, g, b, a))
    return bytes(header) + bytes(data)


def main() -> None:
    blob = make_fake_m32_bytes(2, 2)
    img = qimage_from_m32_bytes(blob)
    ok = bool(img and not img.isNull())
    w = img.width() if img else -1
    h = img.height() if img else -1
    print({"loaded": ok, "width": w, "height": h})


if __name__ == "__main__":
    main()


