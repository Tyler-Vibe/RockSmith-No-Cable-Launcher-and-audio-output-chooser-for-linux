"""Minimal Rocksmith PSARC reader/writer (AES-256-CFB TOC + zlib blocks)."""

from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# Public Rocksmith 2014 PSARC TOC key (Rocksmith Toolkit / rs-utils).
PSARC_KEY = bytes(
    (
        0xC5, 0x3D, 0xB2, 0x38, 0x70, 0xA1, 0xA2, 0xF7,
        0x1C, 0xAE, 0x64, 0x06, 0x1F, 0xDD, 0x0E, 0x11,
        0x57, 0x30, 0x9D, 0xC8, 0x52, 0x04, 0xD4, 0xC5,
        0xBF, 0xDF, 0x25, 0x09, 0x0D, 0xF2, 0x57, 0x2C,
    )
)
PSARC_IV = bytes(
    (
        0xE9, 0x15, 0xAA, 0x01, 0x8F, 0xEF, 0x71, 0xFC,
        0x50, 0x81, 0x32, 0xE4, 0xBB, 0x4C, 0xEB, 0x42,
    )
)


def _aes(data: bytes, encrypt: bool) -> bytes:
    cipher = Cipher(algorithms.AES(PSARC_KEY), modes.CFB(PSARC_IV))
    engine = cipher.encryptor() if encrypt else cipher.decryptor()
    return engine.update(data) + engine.finalize()


def _be32(buf: bytes, off: int) -> int:
    return struct.unpack_from(">I", buf, off)[0]


def _be16(buf: bytes, off: int) -> int:
    return struct.unpack_from(">H", buf, off)[0]


def _be40(buf: bytes, off: int) -> int:
    return int.from_bytes(buf[off : off + 5], "big")


def _put_be32(value: int) -> bytes:
    return struct.pack(">I", value)


def _put_be16(value: int) -> bytes:
    return struct.pack(">H", value)


def _put_be40(value: int) -> bytes:
    return value.to_bytes(5, "big")


def extract(psarc_path: Path, dest_dir: Path) -> list[str]:
    data = Path(psarc_path).read_bytes()
    if data[:4] != b"PSAR":
        raise ValueError(f"Not a PSARC: {psarc_path}")
    toc_size = _be32(data, 12)
    entry_size = _be32(data, 16)
    num_entries = _be32(data, 20)
    block_size = _be32(data, 24)
    flags = _be32(data, 28)
    toc_raw = data[32:toc_size]
    toc = _aes(toc_raw, encrypt=False) if flags & 4 else toc_raw

    entries = []
    pos = 0
    for _ in range(num_entries):
        entries.append(
            {
                "zindex": _be32(toc, pos + 16),
                "length": _be40(toc, pos + 20),
                "offset": _be40(toc, pos + 25),
            }
        )
        pos += entry_size
    block_sizes = []
    while pos + 2 <= len(toc):
        block_sizes.append(_be16(toc, pos))
        pos += 2

    def decompress(entry: dict) -> bytes:
        out = bytearray()
        cursor = entry["offset"]
        zidx = entry["zindex"]
        while len(out) < entry["length"] and zidx < len(block_sizes):
            zlen = block_sizes[zidx]
            if zlen == 0:
                take = min(block_size, len(data) - cursor)
                out.extend(data[cursor : cursor + take])
                cursor += block_size
            elif data[cursor] != 0x78:
                out.extend(data[cursor : cursor + zlen])
                cursor += zlen
            else:
                out.extend(zlib.decompress(data[cursor : cursor + zlen]))
                cursor += zlen
            zidx += 1
        return bytes(out[: entry["length"]])

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    manifest = decompress(entries[0]).decode("utf-8", "replace")
    paths = [line for line in manifest.replace("\r", "").split("\n") if line]
    written = []
    for index, path in enumerate(paths, start=1):
        if index >= len(entries):
            break
        content = decompress(entries[index])
        out_path = dest / path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(content)
        written.append(path)
    return written


def repack(source_dir: Path, psarc_path: Path) -> None:
    source = Path(source_dir)
    files = sorted((p for p in source.rglob("*") if p.is_file()), reverse=True)
    rels = [p.relative_to(source).as_posix() for p in files]
    manifest = "".join(rel + "\n" for rel in rels)
    payloads = [manifest.encode("utf-8")]
    payloads.extend(p.read_bytes() for p in files)

    block_size = 65536
    entry_size = 30
    blocks_per_entry: list[list[bytes]] = []
    block_sizes: list[int] = []
    z_indices: list[int] = []
    stored_sizes: list[int] = []
    zidx = 0
    for payload in payloads:
        z_indices.append(zidx)
        stored = 0
        entry_blocks: list[bytes] = []
        if not payload:
            entry_blocks.append(b"")
            block_sizes.append(0)
            zidx += 1
        else:
            for start in range(0, len(payload), block_size):
                chunk = payload[start : start + block_size]
                compressed = zlib.compress(chunk, 9)
                if len(compressed) < len(chunk) and len(compressed) <= 0xFFFF:
                    entry_blocks.append(compressed)
                    block_sizes.append(len(compressed))
                    stored += len(compressed)
                else:
                    entry_blocks.append(chunk)
                    block_sizes.append(len(chunk) % block_size)
                    stored += len(chunk)
                zidx += 1
        blocks_per_entry.append(entry_blocks)
        stored_sizes.append(stored)

    toc_size = 32 + len(payloads) * entry_size + len(block_sizes) * 2
    offsets = []
    cursor = toc_size
    for size in stored_sizes:
        offsets.append(cursor)
        cursor += size

    toc_entries = bytearray()
    for i, payload in enumerate(payloads):
        if i == 0:
            md5 = bytes(16)
        else:
            md5 = hashlib.md5(rels[i - 1].lower().encode("utf-8")).digest()
        toc_entries += md5
        toc_entries += _put_be32(z_indices[i])
        toc_entries += _put_be40(len(payload))
        toc_entries += _put_be40(offsets[i])

    block_table = b"".join(_put_be16(v) for v in block_sizes)
    header = (
        b"PSAR"
        + _put_be32(0x00010004)
        + b"zlib"
        + _put_be32(toc_size)
        + _put_be32(entry_size)
        + _put_be32(len(payloads))
        + _put_be32(block_size)
        + _put_be32(4)
    )
    encrypted = _aes(bytes(toc_entries) + block_table, encrypt=True)
    out = Path(psarc_path)
    with out.open("wb") as fh:
        fh.write(header)
        fh.write(encrypted)
        for entry_blocks in blocks_per_entry:
            for block in entry_blocks:
                fh.write(block)
