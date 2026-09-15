import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from mc_plc_simulator import McPlcSimulator  # noqa: E402

from visionguard.config.schemas import PlcConnectionConfig  # noqa: E402
from visionguard.plc.base_plc import PlcError  # noqa: E402
from visionguard.plc.device_address import DeviceAddressError, parse_device  # noqa: E402
from visionguard.plc.mitsubishi.mc_driver import MitsubishiMCDriver  # noqa: E402
from visionguard.plc.mitsubishi.mc_protocol import McFrameConfig, McProtocol3E, McProtocolError  # noqa: E402


def test_parse_device():
    assert parse_device("M100") == parse_device(" m 100 ")
    assert parse_device("D100").is_word and not parse_device("D100").is_bit
    assert parse_device("X1F").number == 0x1F
    assert parse_device("X17", xy_octal=True).number == 0o17
    assert parse_device("SM400").device == "SM" and parse_device("SM400").number == 400
    assert str(parse_device("W1A")) == "W1A"
    for bad in ("", "100", "Q1", "M-1", "MX"):
        with pytest.raises(DeviceAddressError):
            parse_device(bad)


def test_binary_batch_read_frame_layout():
    p = McProtocol3E(McFrameConfig(monitoring_timer=4))
    req = p.build_batch_read(parse_device("M100"), 8, bit_units=True)
    # 50 00 | net 00 | pc FF | io FF 03 | st 00 | len 0C 00 | timer 04 00 | cmd 01 04 | sub 01 00 | dev 64 00 00 90 | pts 08 00
    assert req == bytes.fromhex("5000 00 FF FF03 00 0C00 0400 0104 0100 640000 90 0800".replace(" ", ""))
    req_w = p.build_batch_read(parse_device("D100"), 2, bit_units=False)
    assert req_w[13:15] == b"\x00\x00" and req_w[18] == 0xA8


def test_binary_bit_packing_roundtrip():
    p = McProtocol3E(McFrameConfig())
    values = [True, False, True, True, False, False, True]
    req = p.build_batch_write_bits(parse_device("M0"), values)
    data = req[-4:]
    assert data == bytes([0x10, 0x11, 0x00, 0x10])
    assert p.decode_bits(data, 7) == values


def test_ascii_frame_layout():
    p = McProtocol3E(McFrameConfig(monitoring_timer=4, ascii=True))
    req = p.build_batch_read(parse_device("M100"), 8, bit_units=True).decode()
    assert req == "500000FF03FF00" + "0018" + "0004" + "0401" + "0001" + "M*000100" + "0008"
    resp = ("D00000FF03FF00" + "0006" + "0000" + "10").encode()
    assert p.decode_bits(p.parse_response(resp), 2) == [True, False]


def test_end_code_error():
    p = McProtocol3E(McFrameConfig())
    resp = b"\xD0\x00\x00\xFF\xFF\x03\x00" + struct.pack("<H", 2) + struct.pack("<H", 0xC059)
    with pytest.raises(McProtocolError) as ei:
        p.parse_response(resp)
    assert ei.value.end_code == 0xC059


@pytest.mark.parametrize("ascii_mode", [False, True])
def test_driver_against_simulator(ascii_mode):
    sim = McPlcSimulator("127.0.0.1", 0, ascii=ascii_mode, verbose=False).start()
    try:
        cfg = PlcConnectionConfig(ip="127.0.0.1", port=sim.port, frame_format="ascii" if ascii_mode else "binary",
                                  timeout_s=1.0, retry_count=1)
        drv = MitsubishiMCDriver(cfg)
        assert drv.connect(), drv.last_error
        drv.write_bit("M100", True)
        drv.write_bit("M101", False)
        drv.write_word("D100", 1)
        drv.write_bits("M200", [True, False, True])
        drv.write_bits_random([("M300", True), ("M310", True)])
        drv.write_words("D200", [7, 0xFFFF])
        assert drv.read_bit("M100") is True
        assert drv.read_bit("M101") is False
        assert drv.read_word("D100") == 1
        assert drv.read_bits("M200", 3) == [True, False, True]
        assert drv.read_bits("M300", 11)[0] and drv.read_bits("M300", 11)[10]
        assert drv.read_words("D200", 2) == [7, 0xFFFF]
        assert drv.read_word("M200") == 0b101
        with pytest.raises(PlcError):
            drv.read_bits("D100", 1)
        assert drv.last_latency_ms >= 0.0
        ok, msg = drv.test_connection("M100")
        assert ok and "M100 = 1" in msg
        drv.disconnect()
        assert not drv.is_connected()
    finally:
        sim.stop()


def test_driver_connect_failure_is_graceful():
    cfg = PlcConnectionConfig(ip="127.0.0.1", port=1, timeout_s=0.3, retry_count=0)
    drv = MitsubishiMCDriver(cfg)
    assert drv.connect() is False
    assert "Cannot connect" in drv.last_error
    with pytest.raises(PlcError):
        drv.write_bit("M100", True)
