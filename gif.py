"""Minimal, dependency-free animated GIF encoder for pixel art (exact colors, 1-bit transparency)."""
import struct


def _quantize(frames):
    """Map RGBA frames to <=255 opaque colors + index 0 for transparent. Exact when possible."""
    def key(r, g, b, bits):
        if bits == 8:
            return (r, g, b)
        m = 0xFF & ~((1 << (8 - bits)) - 1)
        return (r & m, g & m, b & m)

    for bits in (8, 6, 5, 4, 3):
        colors = {}
        for data in frames:
            for i in range(0, len(data), 4):
                if data[i + 3] >= 128:
                    k = key(data[i], data[i + 1], data[i + 2], bits)
                    if k not in colors:
                        colors[k] = len(colors) + 1
                        if len(colors) > 255:
                            break
            if len(colors) > 255:
                break
        if len(colors) <= 255:
            break
    palette = [(0, 0, 0)] + list(colors.keys())
    indexed = []
    for data in frames:
        idx = bytearray(len(data) // 4)
        for p, i in enumerate(range(0, len(data), 4)):
            if data[i + 3] >= 128:
                idx[p] = colors[key(data[i], data[i + 1], data[i + 2], bits)]
        indexed.append(idx)
    return palette, indexed, bits == 8


def _lzw(indices, min_code_size):
    clear, eoi = 1 << min_code_size, (1 << min_code_size) + 1
    out = bytearray()
    bitbuf = 0
    nbits = 0

    def emit(code, size):
        nonlocal bitbuf, nbits
        bitbuf |= code << nbits
        nbits += size
        while nbits >= 8:
            out.append(bitbuf & 0xFF)
            bitbuf >>= 8
            nbits -= 8

    def reset():
        return {bytes([i]): i for i in range(clear)}, eoi + 1, min_code_size + 1

    table, next_code, size = reset()
    emit(clear, size)
    w = bytes([indices[0]])
    for k in indices[1:]:
        wk = w + bytes([k])
        if wk in table:
            w = wk
            continue
        emit(table[w], size)
        if next_code == (1 << size) and size < 12:
            size += 1
        if next_code < 4096:
            table[wk] = next_code
            next_code += 1
        else:
            emit(clear, size)
            table, next_code, size = reset()
        w = bytes([k])
    emit(table[w], size)
    emit(eoi, size)
    if nbits:
        out.append(bitbuf & 0xFF)
    blocks = bytearray()
    for i in range(0, len(out), 255):
        chunk = out[i:i + 255]
        blocks.append(len(chunk))
        blocks += chunk
    blocks.append(0)
    return bytes(blocks)


def encode_gif(w, h, frames, durations_ms, loop=0):
    """frames: list of RGBA bytearrays (w*h*4). Returns (gif_bytes, exact_colors: bool)."""
    palette, indexed, exact = _quantize(frames)
    size_bits = max(1, (len(palette) - 1).bit_length())
    table_len = 1 << size_bits
    pal = bytearray()
    for r, g, b in palette:
        pal += bytes((r, g, b))
    pal += bytes(3 * (table_len - len(palette)))
    min_code = max(2, size_bits)

    out = bytearray(b"GIF89a")
    out += struct.pack("<HHBBB", w, h, 0x80 | 0x70 | (size_bits - 1), 0, 0)
    out += pal
    out += b"\x21\xFF\x0BNETSCAPE2.0\x03\x01" + struct.pack("<H", loop) + b"\x00"
    for idx, ms in zip(indexed, durations_ms):
        delay = max(2, int(round(ms / 10.0)))
        # disposal 2 (restore to background) so transparent areas don't accumulate; index 0 transparent
        out += b"\x21\xF9\x04" + bytes([(2 << 2) | 1]) + struct.pack("<H", delay) + b"\x00\x00"
        out += b"\x2C" + struct.pack("<HHHHB", 0, 0, w, h, 0)
        out.append(min_code)
        out += _lzw(idx, min_code)
    out.append(0x3B)
    return bytes(out), exact
