"""ROI editor validation and PLC display, using Qt without hardware or a screen."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace
import unittest

from PySide6.QtWidgets import QApplication, QPushButton

from visionguard.app.controllers.system_controller import SystemController
from visionguard.app.widgets.roi_panel import RoiPanel
from visionguard.app.widgets.plc_config_widget import PlcConfigWidget
from visionguard.config.schemas import CameraConfig, PlcConfig
from visionguard.roi.roi_manager import RoiManager
from visionguard.roi.roi_model import RoiType

POINTS = [(0., 0.), (1., 0.), (1., 1.), (0., 1.)]


class RoiUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.manager = RoiManager()
        self.first = self.manager.create(RoiType.INCLUDE, POINTS, plc_device="M200")
        self.second = self.manager.create(RoiType.INCLUDE, POINTS, plc_device="M201")
        self.panel = RoiPanel()
        self.panel.set_cameras(["Camera 1"])
        self.panel.set_rois(self.manager.all())
        self.applied = []
        self.panel.fields_changed.connect(lambda *args: self.applied.append(args))

    def tearDown(self):
        self.panel.close()
        self.panel.deleteLater()
        self.app.processEvents()

    def test_only_one_roi_creation_action_and_new_roi_selected(self):
        self.assertEqual([b.text() for b in self.panel.findChildren(QPushButton) if b.text().startswith("+")], ["+ ROI"])
        self.assertIn(self.second.id, self.panel.lbl_id.text())

    def test_apply_valid_bit_keeps_id_and_camera(self):
        self.panel.edt_plc.setText("m 0202")
        self.panel.btn_apply.click()
        self.assertEqual(self.applied[0], (self.second.id, self.second.name, "M202", True, 0))

    def test_reject_duplicate_word_and_missing_bit(self):
        for address in ("M0200", "D100", "M110", ""):
            with self.subTest(address=address):
                self.panel.edt_plc.setText(address)
                self.panel.btn_apply.click()
                self.assertFalse(self.applied)

    def test_plc_memory_identifies_roi_and_manual_access_includes_roi(self):
        widget = PlcConfigWidget(PlcConfig())
        widget.set_rois(self.manager.all())
        widget.set_memory({"M200": 1, "M201": 0})
        self.assertIn(self.first.id, widget.tbl_mem.item(0, 2).text())
        self.assertIn("M201", widget.io_test._roi_devices)
        widget.close()
        widget.deleteLater()

    def test_controller_rejects_conflict_even_if_ui_is_bypassed(self):
        messages = []
        ctx = SimpleNamespace(roi_manager=self.manager, settings=SimpleNamespace(plc=PlcConfig()),
                              message=SimpleNamespace(emit=messages.append))
        SystemController.update_roi_fields(ctx, self.second.id, "second", "M0200", True)
        self.assertEqual(self.manager.get(self.second.id).plc_device, "M201")
        self.assertTrue(messages)

    def test_configuration_fault_for_unassigned_roi_and_disabled_is_allowed(self):
        roi = self.manager.create(RoiType.INCLUDE, POINTS)
        ctx = SimpleNamespace(ai_camera_mode=False, roi_manager=self.manager,
                              settings=SimpleNamespace(plc=PlcConfig(), camera=CameraConfig()))
        self.assertIn(roi.id, SystemController.roi_mapping_error(ctx))
        self.manager.set_enabled(roi.id, False)
        self.assertEqual(SystemController.roi_mapping_error(ctx), "")

    def test_disabling_all_rois_allows_their_outputs_to_clear(self):
        for roi in self.manager.all():
            self.manager.set_enabled(roi.id, False)
        ctx = SimpleNamespace(ai_camera_mode=False, roi_manager=self.manager,
                              settings=SimpleNamespace(plc=PlcConfig(), camera=CameraConfig()))
        self.assertEqual(SystemController.roi_mapping_error(ctx), "")


if __name__ == "__main__":
    unittest.main()
