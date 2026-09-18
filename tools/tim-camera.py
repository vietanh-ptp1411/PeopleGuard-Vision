"""Find an IP camera on the cable, even when it is on a different subnet.

A factory Hikvision sits on 192.168.1.64 whatever the laptop's address is. If the
laptop is on 192.168.63.x the two cannot exchange a single IP packet, and every
ping, every browser and every RTSP player will say the camera is not there. It is.

So this asks twice, in the order that actually finds things:

    SADP    a UDP probe to 239.255.255.250:37020, which is what Hikvision's own SADP
            tool sends. The camera answers at layer 2, so it replies across a subnet
            mismatch - and the reply says whether it is still factory-INACTIVE, which
            no amount of scanning would tell you.

    scan    a TCP sweep of one /24 on the camera ports, for anything that ignores
            SADP: other brands, or a device that has been given a static address.
            ICMP is often blocked in firmware; an open TCP port is not.

    python tools\\tim-camera.py                  # SADP, tat ca card mang
    python tools\\tim-camera.py --quet 192.168.1 # quet dai 192.168.1.1-254
    python tools\\tim-camera.py --cho 15         # nghe SADP lau hon

Console output is written without diacritics on purpose: this runs in cmd.exe on a
customer machine, where the code page is usually 437 or 1252 and an accented
character either turns to mojibake or kills the script with a UnicodeEncodeError.
The same reason tools\\check.bat and the pre-flight report read the way they do.
"""
from __future__ import annotations

import argparse
import re
import socket
import struct
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

GROUP = "239.255.255.250"
SADP_PORT = 37020
CAMERA_PORTS = (80, 554, 8000, 8080, 443)

PROBE = ('<?xml version="1.0" encoding="utf-8"?>'
         "<Probe><Uuid>{uuid}</Uuid><Types>inquiry</Types></Probe>")

FIELDS = ("IPv4Address", "IPv4SubnetMask", "IPv4Gateway", "MAC", "DeviceDescription",
          "DeviceType", "DeviceSN", "HttpPort", "CommandPort", "SoftwareVersion",
          "DHCP", "Activated")


def tag(xml: str, name: str) -> str:
    m = re.search(rf"<{name}>(.*?)</{name}>", xml, re.S | re.I)
    return m.group(1).strip() if m else ""


def local_addresses() -> list:
    """Every IPv4 this PC holds - the LAN port is usually the odd one out."""
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    return sorted(found)


def sadp(listen_s: float) -> dict:
    print(f"[1] SADP - hoi tat ca thiet bi Hikvision tren day mang ({listen_s:.0f}s)")
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        rx.bind(("0.0.0.0", SADP_PORT))
    except OSError as exc:
        print(f"    Khong bind duoc cong {SADP_PORT}: {exc}")
        print("    Neu SADP Tool cua Hikvision dang mo thi tat no di roi chay lai.")
        return {}

    for addr in ["0.0.0.0"] + local_addresses():
        try:
            rx.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                          struct.pack("4s4s", socket.inet_aton(GROUP),
                                      socket.inet_aton(addr)))
        except OSError:
            pass                      # one card refusing the group is not fatal
    rx.settimeout(0.5)

    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    payload = PROBE.format(uuid=str(uuid.uuid4()).upper()).encode()
    for target in ((GROUP, SADP_PORT), ("255.255.255.255", SADP_PORT)):
        try:
            tx.sendto(payload, target)
        except OSError as exc:
            print(f"    (khong gui duoc toi {target[0]}: {exc})")

    seen: dict = {}
    end = time.time() + listen_s
    while time.time() < end:
        try:
            data, addr = rx.recvfrom(8192)
        except socket.timeout:
            continue
        xml = data.decode("utf-8", "replace")
        if "<Types>inquiry</Types>" in xml:
            continue                              # our own probe echoed back
        key = tag(xml, "MAC") or addr[0]
        if key in seen:
            continue
        seen[key] = xml
        print(f"\n    --- thiet bi tra loi tu {addr[0]} ---")
        for f in FIELDS:
            value = tag(xml, f)
            if value:
                print(f"        {f:<18} {value}")
        if tag(xml, "Activated").lower() == "false":
            print("        >>> CAMERA CHUA KICH HOAT <<<")
            print("        Mo trinh duyet vao IP o tren va dat mat khau admin truoc.")
            print("        Chua kich hoat thi RTSP va moi thu khac deu bi tu choi.")
    if not seen:
        print("    Khong thiet bi Hikvision nao tra loi.")
    else:
        print(f"\n    Tong cong {len(seen)} thiet bi tra loi SADP.")
    return seen


def probe_host(ip: str):
    open_ports = []
    for port in CAMERA_PORTS:
        s = socket.socket()
        s.settimeout(0.6)
        try:
            if s.connect_ex((ip, port)) == 0:
                open_ports.append(port)
        except OSError:
            pass
        finally:
            s.close()
    return (ip, open_ports) if open_ports else None


def fingerprint(ip: str, port: int) -> str:
    """Ask for the device page: a camera identifies itself in the headers even
    before it will accept a password."""
    try:
        s = socket.create_connection((ip, port), timeout=2.0)
        s.sendall(f"GET /ISAPI/System/deviceInfo HTTP/1.1\r\nHost: {ip}\r\n"
                  f"Connection: close\r\n\r\n".encode())
        data = b""
        while len(data) < 4096:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        s.close()
    except OSError as exc:
        return f"(khong doc duoc: {exc})"
    text = data.decode("utf-8", "replace")
    head = text.split("\r\n\r\n", 1)[0]
    bits = [line.strip() for line in head.splitlines()
            if line.lower().startswith(("http/", "server:", "www-authenticate:"))]
    if "hikvision" in text.lower() or "App-webs" in head:
        bits.append(">>> RAT GIONG HIKVISION <<<")
    return " | ".join(bits) if bits else head[:200]


def scan(base: str) -> None:
    print(f"\n[2] Quet TCP {base}.1-254 tren cong {CAMERA_PORTS}")
    hosts = [f"{base}.{i}" for i in range(1, 255)]
    with ThreadPoolExecutor(max_workers=128) as pool:
        found = [r for r in pool.map(probe_host, hosts) if r]
    if not found:
        print("    Khong thiet bi nao mo cong nao trong dai nay.")
        return
    print(f"    Tim thay {len(found)} thiet bi:\n")
    for ip, ports in found:
        print(f"    {ip:<16} cong mo: {ports}")
        if 554 in ports and (80 in ports or 8000 in ports):
            print("                     ^^ co 554 (RTSP) + web -> gan nhu chac chan la CAMERA")
        for web in (80, 8080, 443):
            if web in ports:
                print(f"                     {web}: {fingerprint(ip, web)}")
                break


def main() -> int:
    ap = argparse.ArgumentParser(description="Tim IP camera tren mang")
    ap.add_argument("--quet", metavar="192.168.1",
                    help="quet them mot dai /24 bang TCP, vi du --quet 192.168.1")
    ap.add_argument("--cho", type=float, default=8.0,
                    help="nghe SADP bao nhieu giay (mac dinh 8)")
    args = ap.parse_args()

    addrs = local_addresses()
    print("=" * 66)
    print("  Tim camera")
    print("=" * 66)
    print(f"  IP cua may nay: {', '.join(addrs) if addrs else 'khong doc duoc'}\n")

    found = sadp(args.cho)
    if args.quet:
        scan(args.quet.rstrip("."))

    print()
    if not found and not args.quet:
        print("Khong thay gi. Thu theo thu tu sau:")
        print("  - Kiem tra den tren cong LAN cua laptop co sang khong")
        print("  - Tat Windows Firewall cho mang hien tai roi chay lai")
        print("    (camera tra loi bang UDP 37020 vao, firewall hay chan)")
        print("  - Chay lai voi --quet theo dai cua chinh may nay, vi du:")
        for a in addrs:
            if not a.startswith("127."):
                print(f"        python tools\\tim-camera.py --quet {a.rsplit('.', 1)[0]}")
        print("  - Va thu ca dai mac dinh cua Hikvision:")
        print("        python tools\\tim-camera.py --quet 192.168.1")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
