#!/usr/bin/env python3
"""
Wi-Fi Signal Monitor (Linux)

Shows real-time Wi-Fi signal strength (dBm) using `iw`.
- No root required for `iw dev <iface> link` on most distros.
- Refresh interval adjustable.
- Lets you choose the wireless interface.

Dependencies:
  - Python 3
  - PyQt6  (pip install PyQt6)  OR on Arch: sudo pacman -S python-pyqt6
  - iw     (sudo pacman -S iw)

Run:
  python wifi_signal_monitor.py
"""

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


@dataclass
class LinkInfo:
    connected: bool
    ssid: str = ""
    bssid: str = ""
    freq_mhz: Optional[float] = None
    signal_dbm: Optional[int] = None
    rx_bitrate_mbps: Optional[float] = None
    tx_bitrate_mbps: Optional[float] = None
    raw: str = ""


def run_cmd(cmd: List[str], timeout: float = 2.0) -> str:
    p = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    return out if out else err


def list_wifi_ifaces() -> List[str]:
    """
    Parse `iw dev` to list interfaces. Usually includes wlan0, wlp*, etc.
    """
    if not shutil.which("iw"):
        return []
    out = run_cmd(["iw", "dev"])
    ifaces = []
    # Lines like: "Interface wlan0"
    for m in re.finditer(r"^\s*Interface\s+(\S+)\s*$", out, flags=re.MULTILINE):
        ifaces.append(m.group(1))
    return ifaces


def parse_iw_link(text: str) -> LinkInfo:
    """
    Parse output of: iw dev <iface> link
    """
    info = LinkInfo(connected=False, raw=text)

    if "Not connected." in text or "Not connected" in text:
        return info

    # Connected to xx:xx:...
    m = re.search(r"Connected to\s+([0-9a-f:]{17})", text, flags=re.IGNORECASE)
    if m:
        info.connected = True
        info.bssid = m.group(1).lower()

    m = re.search(r"^\s*SSID:\s*(.*)\s*$", text, flags=re.MULTILINE)
    if m:
        info.ssid = m.group(1).strip()

    m = re.search(r"^\s*freq:\s*([0-9.]+)\s*$", text, flags=re.MULTILINE)
    if m:
        try:
            info.freq_mhz = float(m.group(1))
        except ValueError:
            pass

    m = re.search(r"^\s*signal:\s*(-?\d+)\s*dBm\s*$", text, flags=re.MULTILINE)
    if m:
        try:
            info.signal_dbm = int(m.group(1))
        except ValueError:
            pass

    # rx bitrate: 136.1 MBit/s ...
    m = re.search(r"^\s*rx bitrate:\s*([0-9.]+)\s*MBit/s", text, flags=re.MULTILINE | re.IGNORECASE)
    if m:
        try:
            info.rx_bitrate_mbps = float(m.group(1))
        except ValueError:
            pass

    m = re.search(r"^\s*tx bitrate:\s*([0-9.]+)\s*MBit/s", text, flags=re.MULTILINE | re.IGNORECASE)
    if m:
        try:
            info.tx_bitrate_mbps = float(m.group(1))
        except ValueError:
            pass

    return info


def get_link_info(iface: str) -> LinkInfo:
    if not iface:
        return LinkInfo(connected=False, raw="")
    out = run_cmd(["iw", "dev", iface, "link"])
    return parse_iw_link(out)


def dbm_to_quality(dbm: Optional[int]) -> Optional[int]:
    """
    Rough mapping of dBm to 0–100%.
    -50 dBm ~ 100%
    -67 dBm ~ ~70%
    -80 dBm ~ ~30%
    -90 dBm ~ ~10%
    """
    if dbm is None:
        return None
    # Clamp to [-100, -50] then scale
    d = max(-100, min(-50, dbm))
    return int(round((d + 100) * 2))  # -100->0, -50->100


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Wi-Fi Signal Monitor (iw)")

        if not shutil.which("iw"):
            self._fatal_no_iw()

        self.iface_combo = QComboBox()
        self.refresh_ms = QSpinBox()
        self.refresh_ms.setRange(100, 5000)
        self.refresh_ms.setSingleStep(100)
        self.refresh_ms.setValue(500)

        self.btn_refresh_ifaces = QPushButton("Rescan Interfaces")
        self.btn_start_stop = QPushButton("Stop")

        self.lbl_status = QLabel("—")
        self.lbl_ssid = QLabel("—")
        self.lbl_bssid = QLabel("—")
        self.lbl_freq = QLabel("—")
        self.lbl_signal = QLabel("—")
        self.lbl_quality = QLabel("—")
        self.lbl_rx = QLabel("—")
        self.lbl_tx = QLabel("—")

        big = QFont()
        big.setPointSize(18)
        big.setBold(True)
        self.lbl_signal.setFont(big)

        self._build_ui()
        self._load_ifaces()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_reading)
        self.timer.start(self.refresh_ms.value())

        self.refresh_ms.valueChanged.connect(self._apply_interval)
        self.btn_refresh_ifaces.clicked.connect(self._load_ifaces)
        self.btn_start_stop.clicked.connect(self._toggle_timer)

        # First update
        self.update_reading()

    def _fatal_no_iw(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel("`iw` not found. Install it first (e.g., `sudo pacman -S iw`)."))
        self.setCentralWidget(w)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)

        # Controls
        ctrl = QGroupBox("Controls")
        form = QFormLayout(ctrl)
        form.addRow("Interface", self.iface_combo)
        form.addRow("Refresh (ms)", self.refresh_ms)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btn_refresh_ifaces)
        btn_row.addWidget(self.btn_start_stop)
        btn_row.addStretch(1)
        form.addRow(btn_row)
        v.addWidget(ctrl)

        # Readout
        readout = QGroupBox("Live Readout")
        grid = QGridLayout(readout)

        grid.addWidget(QLabel("Status"), 0, 0)
        grid.addWidget(self.lbl_status, 0, 1)

        grid.addWidget(QLabel("SSID"), 1, 0)
        grid.addWidget(self.lbl_ssid, 1, 1)

        grid.addWidget(QLabel("BSSID"), 2, 0)
        grid.addWidget(self.lbl_bssid, 2, 1)

        grid.addWidget(QLabel("Frequency"), 3, 0)
        grid.addWidget(self.lbl_freq, 3, 1)

        grid.addWidget(QLabel("Signal (dBm)"), 4, 0)
        grid.addWidget(self.lbl_signal, 4, 1)

        grid.addWidget(QLabel("Quality"), 5, 0)
        grid.addWidget(self.lbl_quality, 5, 1)

        grid.addWidget(QLabel("RX bitrate"), 6, 0)
        grid.addWidget(self.lbl_rx, 6, 1)

        grid.addWidget(QLabel("TX bitrate"), 7, 0)
        grid.addWidget(self.lbl_tx, 7, 1)

        v.addWidget(readout)

        # Raw output (optional compact)
        self.lbl_raw = QLabel("")
        self.lbl_raw.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_raw.setStyleSheet("color: #666;")
        v.addWidget(self.lbl_raw)

    def _load_ifaces(self):
        current = self.iface_combo.currentText().strip()
        self.iface_combo.blockSignals(True)
        self.iface_combo.clear()

        ifaces = list_wifi_ifaces()
        if not ifaces:
            self.iface_combo.addItem("wlan0")
        else:
            self.iface_combo.addItems(ifaces)

        # Restore prior selection if possible
        if current and current in ifaces:
            self.iface_combo.setCurrentText(current)
        self.iface_combo.blockSignals(False)

    def _apply_interval(self):
        if self.timer.isActive():
            self.timer.start(self.refresh_ms.value())

    def _toggle_timer(self):
        if self.timer.isActive():
            self.timer.stop()
            self.btn_start_stop.setText("Start")
        else:
            self.timer.start(self.refresh_ms.value())
            self.btn_start_stop.setText("Stop")

    def update_reading(self):
        iface = self.iface_combo.currentText().strip()
        info = get_link_info(iface)

        if not info.connected:
            self.lbl_status.setText("Not connected")
            self.lbl_ssid.setText("—")
            self.lbl_bssid.setText("—")
            self.lbl_freq.setText("—")
            self.lbl_signal.setText("—")
            self.lbl_quality.setText("—")
            self.lbl_rx.setText("—")
            self.lbl_tx.setText("—")
            self.lbl_raw.setText(info.raw[:800])
            return

        self.lbl_status.setText(f"Connected ({iface})")
        self.lbl_ssid.setText(info.ssid or "—")
        self.lbl_bssid.setText(info.bssid or "—")

        if info.freq_mhz is not None:
            band = "2.4 GHz" if info.freq_mhz < 3000 else ("5 GHz" if info.freq_mhz < 6000 else "6 GHz")
            self.lbl_freq.setText(f"{info.freq_mhz:.0f} MHz ({band})")
        else:
            self.lbl_freq.setText("—")

        if info.signal_dbm is not None:
            self.lbl_signal.setText(str(info.signal_dbm))
            q = dbm_to_quality(info.signal_dbm)
            self.lbl_quality.setText(f"{q}% (approx)" if q is not None else "—")
        else:
            self.lbl_signal.setText("—")
            self.lbl_quality.setText("—")

        self.lbl_rx.setText(f"{info.rx_bitrate_mbps:.1f} Mbps" if info.rx_bitrate_mbps is not None else "—")
        self.lbl_tx.setText(f"{info.tx_bitrate_mbps:.1f} Mbps" if info.tx_bitrate_mbps is not None else "—")

        # Show the last few lines of raw for debugging
        raw_lines = info.raw.strip().splitlines()
        self.lbl_raw.setText("\n".join(raw_lines[-10:]))


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.resize(520, 420)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
