#!/usr/bin/env python3
"""
Download Manager, native-looking macOS UI (PySide6).
Needs engine.py in the same folder.

    pip3 install PySide6
    python3 download_manager_qt.py
"""
import os
import re
import subprocess
import sys

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QTableWidget, QTableWidgetItem,
                               QMenu, QSystemTrayIcon, QToolBar, QVBoxLayout, QWidget)

import autostart
from bridge import PORT, start_server
from engine import MAX_ACTIVE, Download, fmt_size, fmt_time

HEADERS = ["Name", "Size", "Progress", "Speed", "ETA", "Status"]


def make_tray_icon():
    """Down-arrow glyph drawn in code; marked as a mask so macOS adapts it to light/dark menu bars."""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(Qt.black, 8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawLine(32, 10, 32, 40)
    p.drawPolyline([QPoint(18, 28), QPoint(32, 42), QPoint(46, 28)])
    p.drawLine(16, 54, 48, 54)
    p.end()
    icon = QIcon(pm)
    icon.setIsMask(True)
    return icon


class MainWindow(QMainWindow):
    incoming = Signal(str, dict)   # downloads handed over by the browser extension

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Download Manager")
        self.resize(940, 520)
        self.setAcceptDrops(True)
        self.setUnifiedTitleAndToolBarOnMac(True)
        self.items = []
        self.dest = os.path.expanduser("~/Downloads")

        # --- toolbar ---
        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)
        for text, slot, shortcut in (
            ("Pause", self.pause, None),
            ("Resume", self.resume, None),
            ("Cancel", self.cancel, None),
            ("Remove", self.remove, QKeySequence.Delete),
            ("Show in Finder", self.reveal, None),
        ):
            act = QAction(text, self)
            act.triggered.connect(slot)
            if shortcut:
                act.setShortcut(shortcut)
            tb.addAction(act)
        tb.addSeparator()
        folder = QAction("Folder…", self)
        folder.triggered.connect(self.choose_folder)
        tb.addAction(folder)

        # --- URL bar + table ---
        central = QWidget()
        lay = QVBoxLayout(central)
        row = QHBoxLayout()
        self.url = QLineEdit()
        self.url.setPlaceholderText("Paste a download link (or drag one onto the window)")
        self.url.setClearButtonEnabled(True)
        self.url.returnPressed.connect(self.add_from_field)
        add = QPushButton("Add")
        add.setDefault(True)
        add.clicked.connect(self.add_from_field)
        row.addWidget(self.url)
        row.addWidget(add)
        lay.addLayout(row)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.setColumnWidth(2, 150)
        for c in (1, 3, 4, 5):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        lay.addWidget(self.table)
        self.setCentralWidget(central)

        self.info = QLabel()
        self.statusBar().addWidget(self.info)
        self._update_info(0, 0)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(500)

        self._quitting = False
        self._setup_tray()
        self.incoming.connect(self.add_remote)
        self.bridge = start_server(lambda url, hdrs: self.incoming.emit(url, hdrs))
        if not self.bridge:
            self.statusBar().showMessage(f"Browser integration off: port {PORT} is in use (is another copy running?)")

    # ---------- adding ----------
    def add_from_field(self):
        if self.add_url(self.url.text().strip()):
            self.url.clear()

    def add_url(self, url, headers=None):
        if not re.match(r"^https?://", url, re.I):
            QMessageBox.warning(self, "Invalid URL", "Enter a full http:// or https:// link.")
            return False
        d = Download(url, self.dest, headers)
        self.items.append(d)
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(d.name))
        for c in (1, 3, 4, 5):
            self.table.setItem(r, c, QTableWidgetItem(""))
        for c in (1, 3, 4):
            self.table.item(r, c).setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setTextVisible(True)
        self.table.setCellWidget(r, 2, bar)
        return True

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(make_tray_icon(), self)
        menu = QMenu()
        menu.addAction("Show Window", self.reopen)
        menu.addAction("Pause All", self.pause_all)
        menu.addAction("Resume All", self.resume_all)
        menu.addSeparator()
        login = menu.addAction("Start at Login")
        login.setCheckable(True)
        login.setChecked(autostart.is_enabled())
        login.toggled.connect(autostart.set_enabled)
        menu.addSeparator()
        menu.addAction("Quit Download Manager", self.quit_app)
        self.tray_menu = menu          # keep a reference so it isn't garbage-collected
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("Download Manager")
        self.tray.show()

    def reopen(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def pause_all(self):
        for d in self.items:
            d.pause()

    def resume_all(self):
        for d in self.items:
            if d.status in ("Paused", "Error"):
                d.status = "Queued"

    def add_remote(self, url, headers):
        if self.add_url(url, headers):
            self.statusBar().showMessage("Added from browser", 3000)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        links = [u.toString() for u in md.urls()] if md.hasUrls() else md.text().split()
        for link in links:
            if link.lower().startswith(("http://", "https://")):
                self.add_url(link)

    # ---------- actions ----------
    def _selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return rows, [self.items[r] for r in rows]

    def pause(self):
        for d in self._selected()[1]:
            d.pause()

    def resume(self):
        for d in self._selected()[1]:
            if d.status in ("Paused", "Error"):
                d.status = "Queued"

    def cancel(self):
        for d in self._selected()[1]:
            d.cancel()

    def remove(self):
        rows, ds = self._selected()
        for r, d in reversed(list(zip(rows, ds))):
            d.cancel()
            self.table.removeRow(r)
            self.items.pop(r)

    def reveal(self):
        for d in self._selected()[1]:
            target = d.path if d.status == "Done" else d.dest_dir
            if target and os.path.exists(target):
                subprocess.run(["open", "-R", target])

    def choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Save downloads to", self.dest)
        if d:
            self.dest = d
            self._update_info(0, 0)

    # ---------- refresh ----------
    def _update_info(self, active, speed):
        folder = self.dest.replace(os.path.expanduser("~"), "~")
        self.info.setText(f"{active} active  •  {fmt_size(speed)}/s  •  Saving to {folder}")
        if getattr(self, "tray", None):
            self.tray.setToolTip(f"Download Manager: {active} active, {fmt_size(speed)}/s" if active else "Download Manager")

    def tick(self):
        running = sum(1 for d in self.items if d.active)
        total_speed = 0
        for r, d in enumerate(self.items):
            if d.status == "Queued" and running < MAX_ACTIVE:
                d.start()
                running += 1
            d.update_speed()
            total_speed += d.speed
            done, total = d.downloaded, d.total

            bar = self.table.cellWidget(r, 2)
            if total:
                bar.setRange(0, 100)
                bar.setValue(100 if d.status == "Done" else int(done / total * 100))
            elif d.status == "Downloading":
                bar.setRange(0, 0)          # unknown size: busy indicator
            else:
                bar.setRange(0, 100)
                bar.setValue(100 if d.status == "Done" else 0)

            eta = fmt_time((total - done) / d.speed) if d.speed and total else ""
            status = f"Error: {d.error[:40]}" if d.status == "Error" else d.status
            values = {0: d.name, 1: fmt_size(total) if total else "—",
                      3: f"{fmt_size(d.speed)}/s" if d.speed else "", 4: eta, 5: status}
            for c, text in values.items():
                item = self.table.item(r, c)
                if item.text() != text:
                    item.setText(text)
        self._update_info(running, total_speed)

    def quit_app(self):
        if any(d.active for d in self.items):
            ans = QMessageBox.question(self, "Quit?", "Downloads are in progress. Quit anyway?")
            if ans != QMessageBox.Yes:
                return
        self.pause_all()
        if self.bridge:
            self.bridge.shutdown()
        self._quitting = True
        QApplication.quit()

    def closeEvent(self, e):
        if self._quitting:
            e.accept()
        elif e.spontaneous():        # red close button: keep running in the menu bar
            e.ignore()
            self.hide()
        else:                        # Cmd+Q
            e.ignore()
            self.quit_app()


if __name__ == "__main__":
    import time

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)      # keep running in the menu bar when the window is closed
    win = MainWindow()
    if "--hidden" not in sys.argv:            # --hidden is used when launched at login
        win.show()

    # Clicking the Dock icon brings the window back (ignore the activation at launch)
    started = time.time()
    app.applicationStateChanged.connect(
        lambda s: win.reopen()
        if s == Qt.ApplicationActive and not win.isVisible() and time.time() - started > 3 else None)
    sys.exit(app.exec())
