"""AI Event tab: live camera event monitor, region mapping, simulation and raw payload debug."""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSizePolicy, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ...camera.events.camera_event import CameraEvent, CameraEventType
from ...config.region_mapping import RegionMapping
from ...logic.camera_event_state_machine import ZoneState
from ...plc.device_address import is_valid_device
from ..theme import COLOR_ERROR, COLOR_OK, COLOR_TEXT, COLOR_TEXT_DIM, COLOR_TEXT_MUTED, COLOR_WARN

MAX_ROWS = 300


class EventMonitorWidget(QWidget):
    """What the camera reported, what the software made of it, and what to do next."""

    simulate_requested = Signal(str, str)          # action, region id
    region_changed = Signal(str, str, str, bool)   # region id, name, plc device, enabled
    region_delete_requested = Signal(str)
    regions_save_requested = Signal()
    connect_requested = Signal()
    disconnect_requested = Signal()

    EVENT_COLS = ("Time", "Event", "Region", "Target", "Detail")
    REGION_COLS = ("Region", "Name", "PLC device", "On", "State")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._zone_states: Dict[str, ZoneState] = {}
        self._build()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        # ---------------------------------------------------------- channel header
        head = QHBoxLayout()
        self.lbl_channel = QLabel("AI EVENT CHANNEL: DISCONNECTED")
        self.lbl_channel.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-weight: 700;")
        self.lbl_channel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        head.addWidget(self.lbl_channel, 1)
        self.btn_connect = QPushButton("Connect")
        self.btn_connect.setProperty("size", "sm")
        self.btn_disconnect = QPushButton("Disconnect")
        self.btn_disconnect.setProperty("size", "sm")
        head.addWidget(self.btn_connect)
        head.addWidget(self.btn_disconnect)
        lay.addLayout(head)

        tabs = QTabWidget()
        tabs.addTab(self._build_monitor(), "Event Monitor")
        tabs.addTab(self._build_regions(), "Regions")
        tabs.addTab(self._build_raw(), "RAW EVENT")
        lay.addWidget(tabs, 1)

        # ---------------------------------------------------------- simulation
        g_sim = QGroupBox("SIMULATE CAMERA EVENTS (no camera needed)")
        gl = QGridLayout(g_sim)
        gl.addWidget(QLabel("Region"), 0, 0)
        self.edt_sim_region = QLineEdit("1")
        self.edt_sim_region.setMaximumWidth(70)
        gl.addWidget(self.edt_sim_region, 0, 1)
        buttons = [
            ("Person Enter", "enter", "success"),
            ("Person Exit", "exit", ""),
            ("Intrusion ON", "intrusion_on", "success"),
            ("Intrusion OFF", "intrusion_off", ""),
            ("Vehicle", "vehicle", ""),
            ("Disconnect", "disconnect", "danger"),
            ("Reconnect", "reconnect", ""),
        ]
        for i, (label, action, cls) in enumerate(buttons):
            btn = QPushButton(label)
            if cls:
                btn.setProperty("class", cls)
            btn.setProperty("size", "sm")
            btn.clicked.connect(lambda _=False, a=action: self.simulate_requested.emit(a, self._sim_region()))
            gl.addWidget(btn, 1 + i // 4, i % 4)
        self.lbl_sim_hint = QLabel("Switch the event provider to 'Simulated events' in the Camera tab "
                                   "to use these buttons.")
        self.lbl_sim_hint.setWordWrap(True)
        self.lbl_sim_hint.setProperty("class", "hint")
        gl.addWidget(self.lbl_sim_hint, 3, 0, 1, 4)
        lay.addWidget(g_sim)

        self.btn_connect.clicked.connect(self.connect_requested)
        self.btn_disconnect.clicked.connect(self.disconnect_requested)

    def _build_monitor(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(6, 6, 6, 6)
        self.table = QTableWidget(0, len(self.EVENT_COLS))
        self.table.setHorizontalHeaderLabels(self.EVENT_COLS)
        header = self.table.horizontalHeader()
        for col in range(len(self.EVENT_COLS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch if col == 4
                                        else QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.lbl_count = QLabel("0 event(s)")
        self.lbl_count.setProperty("class", "muted")
        row.addWidget(self.lbl_count)
        row.addStretch(1)
        btn_clear = QPushButton("Clear")
        btn_clear.setProperty("size", "sm")
        btn_clear.clicked.connect(self.clear_events)
        row.addWidget(btn_clear)
        lay.addLayout(row)
        return page

    def _build_regions(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(6, 6, 6, 6)
        info = QLabel("Detection zones live inside the camera. Map each camera region id to a name "
                      "and the PLC bit it should drive. New regions appear here automatically.")
        info.setWordWrap(True)
        info.setProperty("class", "hint")
        lay.addWidget(info)

        self.region_table = QTableWidget(0, len(self.REGION_COLS))
        self.region_table.setHorizontalHeaderLabels(self.REGION_COLS)
        header = self.region_table.horizontalHeader()
        for col in range(len(self.REGION_COLS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch if col == 1
                                        else QHeaderView.ResizeMode.ResizeToContents)
        self.region_table.verticalHeader().setVisible(False)
        self.region_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.region_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.region_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.region_table.itemSelectionChanged.connect(self._region_selected)
        lay.addWidget(self.region_table, 1)

        form = QGridLayout()
        form.addWidget(QLabel("Region id"), 0, 0)
        self.edt_region_id = QLineEdit()
        self.edt_region_id.setReadOnly(True)
        form.addWidget(self.edt_region_id, 0, 1)
        form.addWidget(QLabel("Name"), 0, 2)
        self.edt_region_name = QLineEdit()
        form.addWidget(self.edt_region_name, 0, 3)
        form.addWidget(QLabel("PLC device"), 1, 0)
        self.edt_region_plc = QLineEdit()
        self.edt_region_plc.setPlaceholderText("e.g. M200")
        self.edt_region_plc.textChanged.connect(self._validate_device)
        form.addWidget(self.edt_region_plc, 1, 1)
        self.chk_region_enabled = QCheckBox("Enabled")
        form.addWidget(self.chk_region_enabled, 1, 2)
        self.btn_region_apply = QPushButton("Apply")
        self.btn_region_apply.setProperty("class", "primary")
        self.btn_region_apply.clicked.connect(self._apply_region)
        form.addWidget(self.btn_region_apply, 1, 3)
        lay.addLayout(form)

        row = QHBoxLayout()
        self.btn_region_delete = QPushButton("Remove region")
        self.btn_region_delete.setProperty("class", "danger")
        self.btn_region_delete.clicked.connect(
            lambda: self.region_delete_requested.emit(self.edt_region_id.text().strip()))
        self.btn_region_save = QPushButton("Save mapping")
        self.btn_region_save.setProperty("class", "success")
        self.btn_region_save.clicked.connect(self.regions_save_requested)
        row.addWidget(self.btn_region_delete)
        row.addStretch(1)
        row.addWidget(self.btn_region_save)
        lay.addLayout(row)
        return page

    def _build_raw(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(6, 6, 6, 6)
        info = QLabel("Exactly what the camera sent, before any interpretation. Every model words its "
                      "alarms differently - use this to see event type, region id and target type.")
        info.setWordWrap(True)
        info.setProperty("class", "hint")
        lay.addWidget(info)
        self.raw_text = QPlainTextEdit()
        self.raw_text.setReadOnly(True)
        self.raw_text.setMaximumBlockCount(4000)
        self.raw_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.raw_text.setStyleSheet("font-family: Consolas, 'Cascadia Mono', monospace; font-size: 9pt;")
        lay.addWidget(self.raw_text, 1)
        row = QHBoxLayout()
        self.chk_raw_enabled = QCheckBox("Capture raw payloads")
        self.chk_raw_enabled.setChecked(True)
        row.addWidget(self.chk_raw_enabled)
        row.addStretch(1)
        btn_clear = QPushButton("Clear")
        btn_clear.setProperty("size", "sm")
        btn_clear.clicked.connect(self.raw_text.clear)
        row.addWidget(btn_clear)
        lay.addLayout(row)
        return page

    # ------------------------------------------------------------------ updates
    def _sim_region(self) -> str:
        return self.edt_sim_region.text().strip() or "1"

    def set_channel_state(self, state: str, message: str = "") -> None:
        colors = {"ONLINE": COLOR_OK, "CONNECTING": COLOR_WARN, "RECONNECTING": COLOR_WARN,
                  "ERROR": COLOR_ERROR, "DISCONNECTED": COLOR_TEXT_DIM, "DISABLED": COLOR_TEXT_MUTED}
        color = colors.get(state, COLOR_TEXT_DIM)
        text = f"AI EVENT CHANNEL: {state}"
        if message:
            text += f"   -   {message}"
        self.lbl_channel.setText(text)
        self.lbl_channel.setStyleSheet(f"color: {color}; font-weight: 700;")
        self.btn_connect.setEnabled(state not in ("ONLINE", "CONNECTING"))
        self.btn_disconnect.setEnabled(state in ("ONLINE", "CONNECTING", "RECONNECTING", "ERROR"))

    def set_simulation_available(self, available: bool) -> None:
        self.lbl_sim_hint.setVisible(not available)

    def add_event(self, event: CameraEvent) -> None:
        self.table.insertRow(0)
        etype = event.type_enum
        values = (event.timestamp.strftime("%H:%M:%S.%f")[:-3], event.event_type,
                  event.region_id or "-", event.target_type or "-", event.description)
        for col, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setToolTip(event.summary())
            if col == 1:
                if etype.starts_presence:
                    item.setForeground(QColor(COLOR_ERROR))
                elif etype.ends_presence:
                    item.setForeground(QColor(COLOR_OK))
                elif etype.is_health:
                    item.setForeground(QColor(COLOR_WARN))
            if col == 3 and event.is_non_human_target:
                item.setForeground(QColor(COLOR_TEXT_MUTED))
            self.table.setItem(0, col, item)
        while self.table.rowCount() > MAX_ROWS:
            self.table.removeRow(self.table.rowCount() - 1)
        self.lbl_count.setText(f"{self.table.rowCount()} event(s)")

    def set_events(self, events: List[CameraEvent]) -> None:
        self.table.setRowCount(0)
        for event in events:
            self.add_event(event)

    def clear_events(self) -> None:
        self.table.setRowCount(0)
        self.lbl_count.setText("0 event(s)")

    def add_raw(self, payload: str) -> None:
        if not self.chk_raw_enabled.isChecked():
            return
        self.raw_text.appendPlainText(payload.strip() + "\n" + "-" * 60)

    def set_regions(self, regions: List[RegionMapping], states: Optional[Dict[str, ZoneState]] = None) -> None:
        self._zone_states = dict(states or self._zone_states)
        selected = self.edt_region_id.text().strip()
        self.region_table.blockSignals(True)
        self.region_table.setRowCount(len(regions))
        for row, region in enumerate(regions):
            state = self._zone_states.get(region.camera_region_id)
            values = (region.camera_region_id, region.name, region.plc_device or "-",
                      "Y" if region.enabled else "N", state.value if state else "-")
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col in (0, 3, 4):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == 4 and state is not None:
                    item.setForeground(QColor(COLOR_ERROR if state.occupied else COLOR_OK))
                if not region.enabled:
                    item.setForeground(QColor(COLOR_TEXT_MUTED))
                self.region_table.setItem(row, col, item)
            if region.camera_region_id == selected:
                self.region_table.selectRow(row)
        self.region_table.blockSignals(False)

    def update_zone_states(self, states: Dict[str, ZoneState]) -> None:
        self._zone_states = dict(states)
        for row in range(self.region_table.rowCount()):
            id_item = self.region_table.item(row, 0)
            state_item = self.region_table.item(row, 4)
            if id_item is None or state_item is None:
                continue
            state = states.get(id_item.text())
            if state is not None and state_item.text() != state.value:
                state_item.setText(state.value)
                state_item.setForeground(QColor(COLOR_ERROR if state.occupied else COLOR_OK))

    def set_regions_dirty(self, dirty: bool) -> None:
        self.btn_region_save.setText("Save mapping *" if dirty else "Save mapping")

    # ------------------------------------------------------------------ region form
    def _region_selected(self) -> None:
        rows = self.region_table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        self.edt_region_id.setText(self.region_table.item(row, 0).text())
        self.edt_region_name.setText(self.region_table.item(row, 1).text())
        device = self.region_table.item(row, 2).text()
        self.edt_region_plc.setText("" if device == "-" else device)
        self.chk_region_enabled.setChecked(self.region_table.item(row, 3).text() == "Y")

    def _validate_device(self) -> None:
        text = self.edt_region_plc.text().strip()
        ok = (not text) or is_valid_device(text)
        self.edt_region_plc.setStyleSheet("" if ok else f"border: 1px solid {COLOR_ERROR};")

    def _apply_region(self) -> None:
        region_id = self.edt_region_id.text().strip()
        if not region_id:
            return
        device = self.edt_region_plc.text().strip().upper()
        if device and not is_valid_device(device):
            return
        self.region_changed.emit(region_id, self.edt_region_name.text().strip(), device,
                                 self.chk_region_enabled.isChecked())
