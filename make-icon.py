#!/usr/bin/env python3
"""Generate trawl.ico for the desktop shortcut. Standard library only.

Deliberately not a dependency: Pillow would do this in five lines, but the whole app is
stdlib-only and adding a build-time-only dependency for one 15KB file isn't worth it.

The mark matches the inline SVG favicon in ui/index.html — three ascending signal bars on a
dark rounded tile — so the desktop shortcut, the window and the taskbar all agree.

    python make-icon.py
"""

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).parent / "trawl.ico"

BG = (0x12, 0x0D, 0x0B)
BARS = [  # x, y, w, h as fractions of the tile, plus colour — ascending, brightening
    (0.203, 0.547, 0.141, 0.250, (0xC8, 0x34, 0x1F)),
    (0.430, 0.391, 0.141, 0.406, (0xE8, 0x56, 0x2F)),
    (0.656, 0.203, 0.141, 0.594, (0xFF, 0x79, 0x60)),
]
# Near-square, to match the broadsheet UI. Bars are hard-edged for the same reason.
TILE_RADIUS = 0.070
BAR_RADIUS = 0.008

MASTER = 512          # render once here, box-downscale to each target size for free AA
SIZES = [256, 64, 48, 32, 16]


def _rounded(px, py, x0, y0, w, h, r):
    """Is point (px, py) inside the rounded rect? Coordinates in pixels."""
    if not (x0 <= px <= x0 + w and y0 <= py <= y0 + h):
        return False
    cx = min(max(px, x0 + r), x0 + w - r)
    cy = min(max(py, y0 + r), y0 + h - r)
    return (px - cx) ** 2 + (py - cy) ** 2 <= r * r


def render(n):
    """RGBA bytes for an n x n tile."""
    buf = bytearray(n * n * 4)
    tile_r = TILE_RADIUS * n
    bars = [(bx * n, by * n, bw * n, bh * n, col) for bx, by, bw, bh, col in BARS]
    bar_r = BAR_RADIUS * n

    for y in range(n):
        py = y + 0.5
        for x in range(n):
            px = x + 0.5
            i = (y * n + x) * 4
            if not _rounded(px, py, 0, 0, n - 1, n - 1, tile_r):
                continue                      # outside the tile stays transparent
            colour = BG
            for bx, by, bw, bh, col in bars:
                if _rounded(px, py, bx, by, bw, bh, bar_r):
                    colour = col
                    break
            buf[i:i + 4] = bytes(colour) + b"\xff"
    return bytes(buf)


def downscale(src, n, m):
    """Box-filter n x n RGBA down to m x m. This is where the anti-aliasing comes from."""
    if n == m:
        return src
    out = bytearray(m * m * 4)
    step = n / m
    for oy in range(m):
        y0, y1 = int(oy * step), max(int((oy + 1) * step), int(oy * step) + 1)
        for ox in range(m):
            x0, x1 = int(ox * step), max(int((ox + 1) * step), int(ox * step) + 1)
            r = g = b = a = cnt = 0
            for sy in range(y0, y1):
                base = sy * n * 4
                for sx in range(x0, x1):
                    i = base + sx * 4
                    sa = src[i + 3]
                    # premultiply, or transparent pixels drag the edges toward black
                    r += src[i] * sa
                    g += src[i + 1] * sa
                    b += src[i + 2] * sa
                    a += sa
                    cnt += 1
            o = (oy * m + ox) * 4
            if a:
                out[o:o + 4] = bytes((r // a, g // a, b // a, a // cnt))
            else:
                out[o:o + 4] = b"\x00\x00\x00\x00"
    return bytes(out)


def png(size, rgba):
    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def ico(images):
    """images: [(size, png_bytes)]. PNG-in-ICO is supported from Vista onward."""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, blob in images:
        dim = 0 if size >= 256 else size          # 0 means 256 in the ICO header
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        blobs += blob
    return header + entries + blobs


def main():
    print(f"rendering master at {MASTER}x{MASTER} ...")
    master = render(MASTER)
    images = []
    for s in SIZES:
        print(f"  -> {s}px")
        images.append((s, png(s, downscale(master, MASTER, s))))
    OUT.write_bytes(ico(images))
    print(f"\nwrote {OUT}  ({OUT.stat().st_size / 1024:.1f} KB, {len(SIZES)} sizes)")


if __name__ == "__main__":
    main()
