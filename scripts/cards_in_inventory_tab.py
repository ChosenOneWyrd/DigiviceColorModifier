from PyQt5 import QtCore, QtWidgets
import csv
import os
import runpy
import shutil
import struct
import sys
import tempfile
from typing import Optional
from pathlib import Path

from common import *
from set_d_ark_inventory import EMPTY_SLOT, INVENTORY_OFFSET, find_save_pair


class SafeInternalScriptWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal(bool, str)

    def __init__(self, script_name, script_args, desc, parent=None):
        super().__init__(parent)
        self.script_name = script_name
        self.script_args = script_args
        self.desc = desc

    @QtCore.pyqtSlot()
    def run(self):
        old_argv = sys.argv
        old_cwd = os.getcwd()
        try:
            script_path = os.path.join(SCRIPT_DIR, self.script_name)
            sys.argv = [script_path] + self.script_args
            os.chdir(SCRIPT_DIR)
            runpy.run_path(script_path, run_name="__main__")
            self.finished.emit(True, f"{self.desc} completed successfully.")
        except SystemExit as exc:
            code = exc.code
            if code in (None, 0):
                self.finished.emit(True, f"{self.desc} completed successfully.")
            else:
                self.finished.emit(False, f"{self.desc} exited with code {code}.")
        except Exception as exc:
            self.finished.emit(False, f"{self.desc} failed: {exc}")
        finally:
            os.chdir(old_cwd)
            sys.argv = old_argv


class CardsInInventoryTab(QtWidgets.QWidget):
    INVENTORY_SCRIPT = "set_d_ark_inventory.py"
    UNLIMITED_SCRIPT = "d_ark_unlimited_cards_and_search.py"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_bin_path: Optional[str] = None
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        top_box = QtWidgets.QGroupBox("D-Ark 25th Color BIN Selection")
        top = QtWidgets.QHBoxLayout(top_box)
        self.bin_path_edit = QtWidgets.QLineEdit()
        self.bin_path_edit.setReadOnly(True)
        self.bin_btn = QtWidgets.QPushButton("Select .bin file...")
        top.addWidget(QtWidgets.QLabel("Selected D-Ark .bin:"))
        top.addWidget(self.bin_path_edit, 1)
        top.addWidget(self.bin_btn)
        layout.addWidget(top_box)

        controls_box = QtWidgets.QGroupBox("Current Card Inventory")
        controls = QtWidgets.QHBoxLayout(controls_box)

        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.refresh_btn.setStyleSheet("background-color:#008000;color:white;font-weight:600;font-size:14pt;")

        self.save_btn = QtWidgets.QPushButton("Save Inventory Edits to BIN")
        self.save_btn.setStyleSheet("background-color:#008000;color:white;font-weight:600;font-size:14pt;")
        self.save_btn.setEnabled(False)

        self.unlimited_btn = QtWidgets.QPushButton("Make Card Usage and Card Search Unlimited?")
        self.unlimited_btn.setStyleSheet("background-color:#0006b1;color:white;font-weight:600;font-size:14pt;")
        self.unlimited_btn.setEnabled(False)

        controls.addWidget(self.refresh_btn)
        controls.addWidget(self.save_btn)
        controls.addWidget(self.unlimited_btn, 1)
        layout.addWidget(controls_box)

        self.table = QtWidgets.QTableWidget(3, 2)
        self.table.setHorizontalHeaderLabels(["Slot", "Which Card?"])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)

        for row in range(3):
            item = QtWidgets.QTableWidgetItem(str(row + 1))
            item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
            item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, item)

        self.table.setColumnWidth(0, 80)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        note = QtWidgets.QLabel(
            "The three dropdowns edit only the current held cards. The unlimited button applies both existing firmware patches: "
            "Search costs 0 points, and cards used in battle stay in inventory. Search discard/replacement remains enabled."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.status_label = QtWidgets.QLabel("Ready.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.bin_btn.clicked.connect(self.pick_bin)
        self.refresh_btn.clicked.connect(self.refresh_inventory)
        self.save_btn.clicked.connect(self.save_inventory)
        self.unlimited_btn.clicked.connect(self.apply_unlimited_patch)

    def require_bin(self):
        if not self.current_bin_path or not os.path.isfile(self.current_bin_path):
            QtWidgets.QMessageBox.warning(self, "BIN required", "Please select a valid D-Ark .bin file.")
            return False
        return True

    def make_card_combo(self, current_card_id):
        combo = NoWheelComboBox()
        for card_id in range(1, 41):
            combo.addItem(f"{card_id:02d} - {CARD_NAMES[card_id]}", card_id)
        index = combo.findData(current_card_id)
        if index >= 0:
            combo.setCurrentIndex(index)
        return combo

    def pick_bin(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select D-Ark 25th Color .bin file", "", "BIN files (*.bin);;All files (*)")
        if not path:
            return
        self.current_bin_path = path
        self.bin_path_edit.setText(path)
        self.refresh_inventory()

    def read_inventory(self):
        data = Path(self.current_bin_path).read_bytes()
        save_base = find_save_pair(data)
        raw = struct.unpack_from("<HHH", data, save_base + INVENTORY_OFFSET)
        cards = []
        for value in raw:
            if value == EMPTY_SLOT:
                cards.append(None)
            elif 0 <= value <= 39:
                cards.append(value + 1)
            else:
                raise RuntimeError(f"Unexpected inventory value 0x{value:04X}")
        return cards

    def refresh_inventory(self):
        if not self.require_bin():
            return
        try:
            cards = self.read_inventory()
        except Exception as exc:
            self.save_btn.setEnabled(False)
            self.unlimited_btn.setEnabled(False)
            QtWidgets.QMessageBox.critical(self, "Inventory read error", str(exc))
            self.status_label.setText("Failed to read D-Ark inventory.")
            return

        for row, card_id in enumerate(cards):
            if card_id is None:
                combo = NoWheelComboBox()
                combo.addItem("(EMPTY)", None)
                for cid in range(1, 41):
                    combo.addItem(f"{cid:02d} - {CARD_NAMES[cid]}", cid)
                combo.setCurrentIndex(0)
            else:
                combo = self.make_card_combo(card_id)
            self.table.setCellWidget(row, 1, combo)

        self.save_btn.setEnabled(True)
        self.unlimited_btn.setEnabled(True)
        labels = ["EMPTY" if cid is None else f"{cid} - {CARD_NAMES[cid]}" for cid in cards]
        self.status_label.setText("Current inventory: " + " | ".join(labels))

    def selected_cards(self):
        cards = []
        for row in range(3):
            combo = self.table.cellWidget(row, 1)
            if combo is None:
                raise RuntimeError(f"Missing card dropdown for slot {row + 1}.")
            card_id = combo.currentData()
            if card_id is None:
                raise RuntimeError("All three inventory slots must contain a card before saving.")
            cards.append(int(card_id))
        if len(set(cards)) != 3:
            raise RuntimeError("Please select three different cards. Duplicate inventory cards are blocked by the backend.")
        return cards

    def make_temp_output(self, prefix):
        target = os.path.abspath(self.current_bin_path)
        directory = os.path.dirname(target)
        fd, path = tempfile.mkstemp(prefix=prefix, suffix=".bin", dir=directory)
        os.close(fd)
        try:
            os.remove(path)
        except OSError:
            pass
        return path

    def run_script_to_replace_bin(self, script_name, script_args, temp_output, title, message, success_title, success_message):
        dlg = BusyDialog(title, message, self)
        worker = SafeInternalScriptWorker(script_name, script_args, title)
        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        def done(ok, msg):
            dlg.accept()
            thread.quit()
            thread.wait()

            final_ok = ok
            final_msg = msg

            if ok:
                try:
                    if not os.path.isfile(temp_output):
                        raise RuntimeError("The backend reported success but did not create its output BIN.")
                    os.replace(temp_output, self.current_bin_path)
                except Exception as exc:
                    final_ok = False
                    final_msg = f"Backend succeeded, but replacing the selected BIN failed: {exc}"

            if os.path.exists(temp_output):
                try:
                    os.remove(temp_output)
                except OSError:
                    pass

            if final_ok:
                self.status_label.setText(success_message)
                QtWidgets.QMessageBox.information(self, success_title, success_message)
                self.refresh_inventory()
            else:
                self.status_label.setText(final_msg)
                QtWidgets.QMessageBox.critical(self, f"{title} Error", final_msg)

        worker.finished.connect(done)
        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()

    def save_inventory(self):
        if not self.require_bin():
            return

        try:
            cards = self.selected_cards()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid inventory", str(exc))
            return

        script_path = os.path.join(SCRIPT_DIR, self.INVENTORY_SCRIPT)
        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.critical(self, "Missing script", f"{self.INVENTORY_SCRIPT} not found in scripts/.")
            return

        temp_output = self.make_temp_output(".d_ark_inventory_")
        args = [self.current_bin_path, temp_output, "--cards", str(cards[0]), str(cards[1]), str(cards[2])]
        self.run_script_to_replace_bin(
            self.INVENTORY_SCRIPT,
            args,
            temp_output,
            "Save Inventory",
            "Please wait...\nUpdating the three current D-Ark inventory cards.",
            "Inventory Updated",
            "The three current inventory cards were updated successfully.",
        )

    def apply_unlimited_patch(self):
        if not self.require_bin():
            return

        script_path = os.path.join(SCRIPT_DIR, self.UNLIMITED_SCRIPT)
        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.critical(self, "Missing script", f"{self.UNLIMITED_SCRIPT} not found in scripts/.")
            return

        result = QtWidgets.QMessageBox.question(
            self,
            "Make Card Usage and Card Search Unlimited?",
            "Please make sure you have AT LEAST 30 Search Points available before applying this patch.\n\n"
            "Two patches will be applied:\n\n"
            "• Digimon Search costs 0 Search Points.\n"
            "• Cards used in battle remain in inventory.\n\n"
            "Continue?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if result != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        temp_output = self.make_temp_output(".d_ark_unlimited_cards_")
        args = [self.current_bin_path, temp_output]
        self.run_script_to_replace_bin(
            self.UNLIMITED_SCRIPT,
            args,
            temp_output,
            "Unlimited Cards",
            "Please wait...\nApplying unlimited card usage and Search patch.",
            "Unlimited Cards Enabled",
            "Card usage and Digimon Search are now unlimited for this BIN.",
        )
