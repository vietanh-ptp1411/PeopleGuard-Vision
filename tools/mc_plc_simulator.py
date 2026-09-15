"""Mitsubishi MC Protocol 3E PLC emulator (Binary + ASCII) for testing without hardware.

Usage:
    python tools/mc_plc_simulator.py --port 5000 [--ascii] [--verbose]

Then point the app at 127.0.0.1:5000 with "Simulate PLC" OFF to exercise the real
MC driver end-to-end. Supports batch read/write (0401/1401, bit & word units) and
random write bits (1402). Memory is a simple dict; every write is printed.
"""
from __future__ import annotations

import argparse
import socket
import socketserver
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from visionguard.plc.device_address import DEVICE_TABLE  # noqa: E402

CODE_TO_DEVICE = {v[0]: k for k, v in DEVICE_TABLE.items()}
ASCII_TO_DEVICE = {v[3]: k for k, v in DEVICE_TABLE.items()}


class PlcMemory:
    def __init__(self) -> None:
        self.bits: Dict[Tuple[str, int], bool] = {}
        self.words: Dict[Tuple[str, int], int] = {}
        self.lock = threading.Lock()
        self.write_log = []

    def read_bits(self, dev: str, start: int, n: int):
        with self.lock:
            return [self.bits.get((dev, start + i), False) for i in range(n)]

    def read_words(self, dev: str, start: int, n: int):
        with self.lock:
            if DEVICE_TABLE[dev][1]:  # bit device in word units
                out = []
                for w in range(n):
                    v = 0
                    for b in range(16):
                        if self.bits.get((dev, start + w * 16 + b), False):
                            v |= 1 << b
                    out.append(v)
                return out
            return [self.words.get((dev, start + i), 0) for i in range(n)]

    def write_bits(self, dev: str, start: int, values):
        with self.lock:
            for i, v in enumerate(values):
                self.bits[(dev, start + i)] = bool(v)
                self.write_log.append((f"{dev}{start + i}", int(bool(v))))

    def write_words(self, dev: str, start: int, values):
        with self.lock:
            for i, v in enumerate(values):
                if DEVICE_TABLE[dev][1]:
                    for b in range(16):
                        self.bits[(dev, start + i * 16 + b)] = bool((v >> b) & 1)
                else:
                    self.words[(dev, start + i)] = v & 0xFFFF
                self.write_log.append((f"{dev}{start + i}", v & 0xFFFF))


class McHandler(socketserver.BaseRequestHandler):
    server: "McPlcSimulator"

    def handle(self) -> None:
        sock: socket.socket = self.request
        sock.settimeout(60)
        self.server.register(sock)
        buf = b""
        try:
            while True:
                try:
                    chunk = sock.recv(4096)
                except (socket.timeout, OSError):
                    break
                if not chunk:
                    break
                buf += chunk
                while True:
                    frame, buf = self._split(buf)
                    if frame is None:
                        break
                    try:
                        resp = self._process(frame)
                    except Exception as exc:  # malformed request -> end code C059
                        self.server.log(f"error: {exc}")
                        resp = self._error(0xC059, frame)
                    try:
                        sock.sendall(resp)
                    except OSError:
                        return
        finally:
            self.server.unregister(sock)

    # ------------------------------------------------------------------ framing
    def _split(self, buf: bytes) -> Tuple[Optional[bytes], bytes]:
        if self.server.ascii:
            if len(buf) < 18:
                return None, buf
            length = int(buf[14:18], 16)
            total = 18 + length
        else:
            if len(buf) < 9:
                return None, buf
            length = struct.unpack_from("<H", buf, 7)[0]
            total = 9 + length
        if len(buf) < total:
            return None, buf
        return buf[:total], buf[total:]

    def _reply(self, request: bytes, end_code: int, data: bytes | str) -> bytes:
        if self.server.ascii:
            text = request.decode("ascii")
            head = "D000" + text[4:14]
            payload = f"{end_code:04X}" + (data if isinstance(data, str) else data.decode())
            return (head + f"{len(payload):04X}" + payload).encode("ascii")
        head = b"\xD0\x00" + request[2:7]
        payload = struct.pack("<H", end_code) + (data if isinstance(data, bytes) else data.encode())
        return head + struct.pack("<H", len(payload)) + payload

    def _error(self, end_code: int, request: bytes) -> bytes:
        return self._reply(request, end_code, b"" if not self.server.ascii else "")

    # ------------------------------------------------------------------ commands
    def _process(self, frame: bytes) -> bytes:
        mem = self.server.memory
        if self.server.ascii:
            t = frame.decode("ascii")
            cmd, sub = int(t[22:26], 16), int(t[26:30], 16)
            body = t[30:]
            if cmd == 0x0401:
                dev, start = self._dev_ascii(body[:8])
                n = int(body[8:12], 16)
                if sub == 1:
                    vals = mem.read_bits(dev, start, n)
                    return self._reply(frame, 0, "".join("1" if v else "0" for v in vals))
                vals = mem.read_words(dev, start, n)
                return self._reply(frame, 0, "".join(f"{v:04X}" for v in vals))
            if cmd == 0x1401:
                dev, start = self._dev_ascii(body[:8])
                n = int(body[8:12], 16)
                if sub == 1:
                    vals = [c == "1" for c in body[12 : 12 + n]]
                    mem.write_bits(dev, start, vals)
                else:
                    vals = [int(body[12 + i * 4 : 16 + i * 4], 16) for i in range(n)]
                    mem.write_words(dev, start, vals)
                self._log_writes()
                return self._reply(frame, 0, "")
            if cmd == 0x1402 and sub == 1:
                n = int(body[:2], 16)
                pos = 2
                for _ in range(n):
                    dev, start = self._dev_ascii(body[pos : pos + 8])
                    val = int(body[pos + 8 : pos + 10], 16)
                    mem.write_bits(dev, start, [bool(val)])
                    pos += 10
                self._log_writes()
                return self._reply(frame, 0, "")
            return self._error(0xC059, frame)

        cmd, sub = struct.unpack_from("<HH", frame, 11)
        body = frame[15:]
        if cmd == 0x0401:
            dev, start = self._dev_bin(body[:4])
            n = struct.unpack_from("<H", body, 4)[0]
            if sub == 1:
                vals = mem.read_bits(dev, start, n)
                packed = bytearray((n + 1) // 2)
                for i, v in enumerate(vals):
                    if v:
                        packed[i // 2] |= 0x10 if i % 2 == 0 else 0x01
                return self._reply(frame, 0, bytes(packed))
            vals = mem.read_words(dev, start, n)
            return self._reply(frame, 0, b"".join(struct.pack("<H", v) for v in vals))
        if cmd == 0x1401:
            dev, start = self._dev_bin(body[:4])
            n = struct.unpack_from("<H", body, 4)[0]
            data = body[6:]
            if sub == 1:
                vals = []
                for i in range(n):
                    byte = data[i // 2]
                    vals.append(bool((byte >> 4) & 1) if i % 2 == 0 else bool(byte & 1))
                mem.write_bits(dev, start, vals)
            else:
                vals = list(struct.unpack_from(f"<{n}H", data, 0))
                mem.write_words(dev, start, vals)
            self._log_writes()
            return self._reply(frame, 0, b"")
        if cmd == 0x1402 and sub == 1:
            n = body[0]
            pos = 1
            for _ in range(n):
                dev, start = self._dev_bin(body[pos : pos + 4])
                mem.write_bits(dev, start, [bool(body[pos + 4])])
                pos += 5
            self._log_writes()
            return self._reply(frame, 0, b"")
        return self._error(0xC059, frame)

    @staticmethod
    def _dev_bin(spec: bytes) -> Tuple[str, int]:
        number = struct.unpack("<I", spec[:3] + b"\x00")[0]
        dev = CODE_TO_DEVICE[spec[3]]
        return dev, number

    @staticmethod
    def _dev_ascii(spec: str) -> Tuple[str, int]:
        dev = ASCII_TO_DEVICE[spec[:2]]
        radix = DEVICE_TABLE[dev][2]
        return dev, int(spec[2:8], radix)

    def _log_writes(self) -> None:
        mem = self.server.memory
        with mem.lock:
            entries, mem.write_log = mem.write_log, []
        for dev, val in entries:
            self.server.log(f"WRITE {dev} = {val}")


class McPlcSimulator(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host: str = "127.0.0.1", port: int = 5000, ascii: bool = False, verbose: bool = True) -> None:
        super().__init__((host, port), McHandler)
        self.memory = PlcMemory()
        self.ascii = ascii
        self.verbose = verbose
        self._thread: Optional[threading.Thread] = None
        self._clients: set = set()
        self._clients_lock = threading.Lock()

    def log(self, msg: str) -> None:
        if self.verbose:
            print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    def register(self, sock: socket.socket) -> None:
        with self._clients_lock:
            self._clients.add(sock)
        self.log(f"client connected {sock.getpeername()}")

    def unregister(self, sock: socket.socket) -> None:
        with self._clients_lock:
            self._clients.discard(sock)

    def start(self) -> "McPlcSimulator":
        self._thread = threading.Thread(target=self.serve_forever, name="mc-sim", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop listening AND drop every client connection (emulates PLC power-off / cable pull)."""
        self.shutdown()
        self.server_close()
        with self._clients_lock:
            clients = list(self._clients)
        for s in clients:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass

    @property
    def port(self) -> int:
        return self.server_address[1]


def main() -> None:
    ap = argparse.ArgumentParser(description="MC Protocol 3E PLC emulator")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--ascii", action="store_true", help="ASCII frames instead of binary")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    sim = McPlcSimulator(args.host, args.port, ascii=args.ascii, verbose=not args.quiet)
    print(f"MC Protocol 3E {'ASCII' if args.ascii else 'Binary'} simulator listening on {args.host}:{args.port} (Ctrl+C to stop)")
    try:
        sim.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        sim.server_close()


if __name__ == "__main__":
    main()
