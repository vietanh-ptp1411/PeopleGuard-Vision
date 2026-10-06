"""Binary delta for update packages: ship what changed inside an exe, not the whole exe.

A PyInstaller exe is a 64 MB archive of thousands of separately zlib-compressed modules.
A release that touches a few `visionguard` modules changes a few hundred kB of it, but the
contents after the change shift, so a whole-file comparison sees a different file.

The new file is cut at content-defined boundaries - every place a zlib stream header
(78 9C / 78 DA) occurs - so an insertion only disturbs the pieces around it. Pieces found
in the old file become COPY instructions, the rest is sent literally:

    file    = MAGIC  op*
    COPY    = 0x01  offset:int64-le  length:int32-le      bytes from the old file
    LITERAL = 0x02  length:int32-le  data[length]         bytes carried in the patch

update.ps1 applies it with the same two instructions and accepts the result only if its
SHA-256 equals the manifest's, so a wrong base or a damaged patch can never be installed.
"""
from __future__ import annotations

import struct
from typing import Dict, Iterator, List, Tuple

MAGIC = b"VGDELTA1"
COPY, LITERAL = 1, 2
MARKERS = (b"\x78\x9c", b"\x78\xda")
MIN_COPY = 32          # shorter matches cost more as an instruction than as literal bytes


def _cuts(data: bytes) -> List[int]:
    found = set()
    for marker in MARKERS:
        at = data.find(marker)
        while at != -1:
            found.add(at)
            at = data.find(marker, at + 1)
    return [0, *sorted(found - {0}), len(data)]


def _pieces(data: bytes) -> Iterator[Tuple[int, int]]:
    cuts = _cuts(data)
    for start, end in zip(cuts, cuts[1:]):
        if end > start:
            yield start, end


def _common_prefix(a: memoryview, b: memoryview) -> int:
    n = min(len(a), len(b))
    lo, hi = 0, n
    while lo < hi:                                   # binary search on equal prefixes
        mid = (lo + hi + 1) // 2
        if a[:mid] == b[:mid]:
            lo = mid
        else:
            hi = mid - 1
    return lo


def make_delta(old: bytes, new: bytes) -> bytes:
    index: Dict[bytes, int] = {}
    for start, end in _pieces(old):
        index.setdefault(old[start:end], start)
    ops: List[list] = []                              # ["C", old_off, length] | ["L", new_start, new_end]
    for start, end in _pieces(new):
        at = index.get(new[start:end])
        if at is not None and end - start >= MIN_COPY:
            last = ops[-1] if ops else None
            if last and last[0] == "C" and last[1] + last[2] == at:
                last[2] += end - start
            else:
                ops.append(["C", at, end - start])
        else:
            last = ops[-1] if ops else None
            if last and last[0] == "L" and last[2] == start:
                last[2] = end
            else:
                ops.append(["L", start, end])
    # A literal run usually still starts like the old bytes that followed the previous
    # copy (an edited module keeps its head); claim that common prefix as a copy too.
    old_view, new_view = memoryview(old), memoryview(new)
    refined: List[list] = []
    for i, op in enumerate(ops):
        if op[0] == "L" and refined and refined[-1][0] == "C":
            prev = refined[-1]
            src = prev[1] + prev[2]
            same = _common_prefix(old_view[src:src + (op[2] - op[1])], new_view[op[1]:op[2]])
            if same >= MIN_COPY:
                prev[2] += same
                op = ["L", op[1] + same, op[2]]
                if op[1] == op[2]:
                    continue
        refined.append(op)
    out = bytearray(MAGIC)
    for op in refined:
        if op[0] == "C":
            out += struct.pack("<BqI", COPY, op[1], op[2])
        else:
            out += struct.pack("<BI", LITERAL, op[2] - op[1]) + new[op[1]:op[2]]
    return bytes(out)


def apply_delta(old: bytes, patch: bytes) -> bytes:
    """Reference implementation; update.ps1 does the same in PowerShell."""
    if patch[:len(MAGIC)] != MAGIC:
        raise ValueError("not a VGDELTA1 patch")
    out = bytearray()
    pos = len(MAGIC)
    while pos < len(patch):
        kind = patch[pos]
        if kind == COPY:
            if pos + 13 > len(patch):
                raise ValueError("truncated copy instruction")
            offset, length = struct.unpack_from("<qI", patch, pos + 1)
            if offset < 0 or offset + length > len(old):
                raise ValueError("copy outside the old file")
            out += old[offset:offset + length]
            pos += 13
        elif kind == LITERAL:
            if pos + 5 > len(patch):
                raise ValueError("truncated literal instruction")
            (length,) = struct.unpack_from("<I", patch, pos + 1)
            if pos + 5 + length > len(patch):
                raise ValueError("truncated literal content")
            out += patch[pos + 5:pos + 5 + length]
            pos += 5 + length
        else:
            raise ValueError(f"bad instruction {kind} at {pos}")
    return bytes(out)
