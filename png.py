"""Minimal, dependency-free PNG encode/decode (RGBA8)."""
import struct
import zlib

_SIG = b"\x89PNG\r\n\x1a\n"


def _chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def encode_png(w, h, rgba):
    """rgba: bytes-like of length w*h*4."""
    stride = w * 4
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += rgba[y * stride:(y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return _SIG + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + _chunk(b"IEND", b"")


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def decode_png(data):
    """Return (w, h, bytearray RGBA). Supports non-interlaced PNGs of all common color types."""
    if data[:8] != _SIG:
        raise ValueError("not a PNG file")
    pos = 8
    idat = bytearray()
    plte = None
    trns = None
    w = h = depth = ctype = interlace = None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif tag == b"PLTE":
            plte = body
        elif tag == b"tRNS":
            trns = body
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
    if interlace:
        raise ValueError("interlaced PNGs are not supported; re-save without interlacing")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    bpp_bits = channels * depth
    bpp = max(1, bpp_bits // 8)
    stride = (w * bpp_bits + 7) // 8
    raw = zlib.decompress(bytes(idat))
    prev = bytearray(stride)
    rows = []
    i = 0
    for _ in range(h):
        f = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        if f == 1:
            for x in range(bpp, stride):
                line[x] = (line[x] + line[x - bpp]) & 255
        elif f == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 255
        elif f == 3:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 255
        elif f == 4:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                c = prev[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + _paeth(a, prev[x], c)) & 255
        rows.append(line)
        prev = line

    out = bytearray(w * h * 4)
    o = 0
    for line in rows:
        if depth < 8:
            mask = (1 << depth) - 1
            vals = []
            for byte in line:
                for s in range(8 - depth, -1, -depth):
                    vals.append((byte >> s) & mask)
            vals = vals[:w]
        elif depth == 16:
            vals = line[0::2]
        else:
            vals = line
        for x in range(w):
            if ctype == 6:
                r, g, b, a = vals[x * 4:x * 4 + 4]
            elif ctype == 2:
                r, g, b = vals[x * 3:x * 3 + 3]
                a = 255
                if trns and len(trns) >= 6 and depth == 8 and (r, g, b) == (trns[1], trns[3], trns[5]):
                    a = 0
            elif ctype == 3:
                idx = vals[x]
                r, g, b = plte[idx * 3:idx * 3 + 3]
                a = trns[idx] if trns and idx < len(trns) else 255
            elif ctype == 0:
                v = vals[x]
                if depth < 8:
                    v = v * 255 // ((1 << depth) - 1)
                r = g = b = v
                a = 255
            else:  # 4: gray + alpha
                r = g = b = vals[x * 2]
                a = vals[x * 2 + 1]
            out[o:o + 4] = bytes((r, g, b, a))
            o += 4
    return w, h, out
