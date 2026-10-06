"""PlcWorker field faults: heartbeat gate, refused writes, config errors, stale bits.

The worker loop is driven by hand (_handle / _periodic) against the simulated PLC, so no
thread, socket or hardware is involved.
"""
import unittest

from visionguard.config.schemas import PlcConfig
from visionguard.plc.mitsubishi.mc_protocol import McProtocolError
from visionguard.plc.plc_manager import PlcManager, PlcOutputState
from visionguard.workers.plc_worker import PlcWorker


def config(heartbeat="M12000"):
    cfg = PlcConfig()
    cfg.simulation_mode = True
    cfg.mapping.device_heartbeat = heartbeat
    return cfg


def running(occupied=False, device="M12010"):
    return PlcOutputState(running=True, roi_devices={"ROI_001": device}, roi_occupied={"ROI_001": occupied})


class PlcWorkerTests(unittest.TestCase):
    def setUp(self):
        self.worker = PlcWorker(config())
        self.connections = []
        self.worker.connection_changed.connect(lambda ok, msg: self.connections.append(ok))

    def memory(self):
        return self.worker._manager.driver.memory_snapshot()

    def tick(self):
        self.worker._manager._hb_last = float("-inf")    # heartbeat due now
        self.worker._periodic()

    def test_state_sent_before_connect_still_starts_heartbeat(self):
        # START (or autostart) while the PLC link is still coming up.
        self.worker.update_output(running())
        self.worker._handle(("apply", None))
        self.worker._handle(("connect", None))
        self.tick()
        self.assertEqual(self.memory().get("M12000"), 1)
        self.tick()
        self.assertEqual(self.memory().get("M12000"), 0)

    def test_refused_write_is_retried_and_heartbeat_resumes(self):
        self.worker._handle(("connect", None))
        driver = self.worker._manager.driver
        real_write = driver.write_bit
        refuse = [True]

        def write_bit(device, value):
            if refuse[0]:
                raise McProtocolError(0x0055)            # 'write during RUN' not enabled
            real_write(device, value)

        driver.write_bit = write_bit
        self.worker.update_output(running(occupied=True))
        self.worker._handle(("apply", None))
        self.assertTrue(self.worker.is_connected(), "a refusal must not drop the link")
        self.tick()
        self.assertNotIn("M12000", self.memory(), "heartbeat must stay frozen while writes are refused")

        refuse[0] = False                                 # commissioning fixed the PLC
        self.worker._retry_write_at = 1e-9               # retry is due
        self.tick()
        self.assertEqual(self.memory().get("M12010"), 1)
        self.assertEqual(self.memory().get("M12000"), 1)
        self.assertEqual(self.connections[-1], True)

    def test_config_error_reports_without_reconnect_loop(self):
        self.worker._handle(("connect", None))
        self.connections.clear()
        self.worker.update_output(running(device="M12000"))   # ROI bit == heartbeat bit
        self.worker._handle(("apply", None))
        self.assertTrue(self.worker.is_connected())
        self.assertEqual(self.worker._next_retry, 0.0, "no redial was scheduled")
        self.assertEqual(self.connections, [False])


class StaleBitTests(unittest.TestCase):
    def test_moved_heartbeat_and_roi_bits_are_released_on_the_same_plc(self):
        mgr = PlcManager(config(heartbeat="M110"))
        mgr.connect()
        mgr.apply_state(running(occupied=True, device="M100"))
        mgr.heartbeat_tick(0.0)
        self.assertEqual(mgr.memory_snapshot(), {"M100": 1, "M110": 1})

        mgr.reconfigure(config(heartbeat="M12000"))
        mgr.connect()
        written = mgr.apply_state(running(occupied=False, device="M12010"))
        self.assertIn(("M110", 0), written)
        self.assertIn(("M100", 0), written)
        self.assertIn(("M12010", 0), written)

    def test_other_plc_never_gets_the_old_addresses(self):
        mgr = PlcManager(config(heartbeat="M110"))
        mgr.connect()
        mgr.heartbeat_tick(0.0)
        other = config(heartbeat="M12000")
        other.connection.ip = "192.168.0.200"
        mgr.reconfigure(other)
        mgr.connect()
        written = mgr.apply_state(running(device="M12010"))
        self.assertNotIn("M110", [dev for dev, _ in written])


if __name__ == "__main__":
    unittest.main()
