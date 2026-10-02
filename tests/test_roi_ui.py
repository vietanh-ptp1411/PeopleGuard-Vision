"""ROI editor validation and PLC display, using Qt without hardware or a screen."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QTableWidget

from visionguard.app.controllers.system_controller import SystemController
from visionguard.app.widgets.roi_panel import RoiPanel
from visionguard.app.widgets.plc_config_widget import PlcConfigWidget
from visionguard.app.main_window import MainWindow, TAB_PLC, TAB_ROI
from visionguard.config.config_manager import ConfigManager
from visionguard.config.schemas import CameraConfig, DetectionMode, PlcConfig
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
        self.plc = PlcConfigWidget(PlcConfig())
        self.panel = self.plc.roi_mapping
        self.zones = RoiPanel()
        self.panel.set_cameras(["Camera 1"])
        self.panel.set_rois(self.manager.all())
        self.applied = []
        self.panel.fields_changed.connect(lambda *args: self.applied.append(args))

    def tearDown(self):
        self.plc.close()
        self.plc.deleteLater()
        self.zones.close()
        self.zones.deleteLater()
        self.app.processEvents()

    def test_only_one_roi_creation_action_and_new_roi_selected(self):
        self.assertEqual([b.text() for b in self.zones.findChildren(QPushButton) if b.text().startswith("+")], ["+ ROI"])
        self.assertIn(self.second.id, self.panel.lbl_id.text())
        self.assertFalse(self.zones.findChildren(QTableWidget))
        self.assertTrue(self.plc.devices_page.isAncestorOf(self.panel.table))
        self.assertTrue(self.plc.devices_page.isAncestorOf(self.panel.edt_plc))
        self.assertFalse(self.plc.devices_page.isAncestorOf(self.plc.edt_person))

    def test_devices_has_its_own_roi_save_action(self):
        self.plc.show_devices()
        self.assertTrue(self.plc.btn_apply.isHidden())
        self.assertTrue(self.plc.chk_advanced.isHidden())
        saved = []
        self.panel.save_requested.connect(lambda: saved.append(True))
        self.panel.btn_save.click()
        self.assertEqual(saved, [True])
        self.plc.tabs.setCurrentIndex(0)
        self.assertFalse(self.plc.btn_apply.isHidden())

    def test_click_highlighted_row_restores_fields_and_deletes_unassigned_roi(self):
        roi = self.manager.create(RoiType.INCLUDE, POINTS)
        self.panel.set_rois([roi])
        self.panel.select("")
        # Reproduce a list refreshed with signals blocked: the row is highlighted
        # while SELECTED ROI is empty. Clicking that row does not change selection.
        with QSignalBlocker(self.panel.table):
            self.panel.table.selectRow(0)
        self.plc.show_devices()
        self.plc.resize(500, 850)
        self.plc.show()
        self.app.processEvents()
        removed = []
        self.panel.delete_requested.connect(removed.append)
        self.panel.delete_requested.connect(self.manager.delete)
        item = self.panel.table.item(0, 0)
        QTest.mouseClick(self.panel.table.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.panel.table.visualItemRect(item).center())
        self.assertIn(roi.id, self.panel.lbl_id.text())
        self.assertTrue(self.panel.edt_plc.isEnabled())
        self.assertEqual(self.panel.edt_plc.text(), "")
        self.panel.btn_delete.click()
        self.assertEqual(removed, [roi.id])
        self.assertIsNone(self.manager.get(roi.id))

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

    def test_full_window_draw_select_map_save_and_edit_across_tabs(self):
        with tempfile.TemporaryDirectory() as folder:
            cm = ConfigManager(Path(folder) / "config")
            cfg = cm.settings
            cfg.camera.detection_mode = DetectionMode.PC_YOLO.value
            cfg.camera.camera_type = "video"
            cfg.camera.set_count(2)
            cfg.app.autostart = False
            cfg.ai.detector.auto_load_on_start = False
            cfg.app.events_db_path = str(Path(folder) / "events.db")
            cfg.app.snapshot.enabled = cfg.app.clip.enabled = False
            cfg.plc.simulation_mode = True
            cm.save_all()
            with patch("visionguard.app.controllers.system_controller.housekeeping.run_in_background"):
                window = MainWindow(cm)
                try:
                    window.ctrl.add_roi("include", POINTS, camera=0)
                    window.ctrl.add_roi("include", POINTS, camera=1)
                    first, second = window.ctrl.roi_manager.all()
                    window.show()
                    window.roi_panel.btn_devices.click()
                    self.app.processEvents()
                    self.assertEqual(window.tabs.currentIndex(), TAB_PLC)
                    self.assertIs(window.plc_cfg.tabs.currentWidget(), window.plc_cfg.devices_page)
                    devices = window.plc_cfg.roi_mapping
                    def click_row(row, column=0):
                        item = devices.table.item(row, column)
                        devices.table.scrollToItem(item)
                        self.app.processEvents()
                        QTest.mouseClick(devices.table.viewport(), Qt.MouseButton.LeftButton,
                                         pos=devices.table.visualItemRect(item).center())

                    click_row(0)
                    self.assertEqual(window.video.maximized, 0)
                    devices.edt_plc.setText("M200")
                    devices.btn_apply.click()
                    click_row(1, 3)
                    self.assertEqual(window.video.maximized, 1)
                    self.assertEqual(devices.cmb_camera.currentIndex(), 1)
                    self.assertEqual(window.video.selected_roi_id, second.id)
                    devices.edt_plc.setText("M201")
                    devices.btn_apply.click()
                    devices.btn_save.click()
                    saved = json.loads(cm.roi_file.read_text(encoding="utf-8"))["rois"]
                    self.assertEqual({r["id"]: r["plc_device"] for r in saved},
                                     {first.id: "M200", second.id: "M201"})
                    self.assertFalse(window.ctrl.roi_manager.dirty)
                    self.assertNotIn("*", window.roi_panel.btn_save.text())
                    self.assertNotIn("*", devices.btn_save.text())
                    devices.btn_edit.click()
                    self.assertEqual(window.tabs.currentIndex(), TAB_ROI)
                    self.assertTrue(window.roi_panel.btn_edit.isChecked())
                    self.assertIn(second.id, window.roi_panel.lbl_selected.text())
                    window.roi_panel.btn_devices.click()
                    click_row(0)
                    self.assertIn(first.id, devices.lbl_id.text())
                    devices.btn_delete.click()
                    self.assertIsNone(window.ctrl.roi_manager.get(first.id))
                    self.assertEqual(devices.table.rowCount(), 1)
                finally:
                    window.close()
                    window.deleteLater()
                    self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
