from PyQt5 import QtCore, QtWidgets
import os
import shutil
import tempfile
import sys
import shutil
import csv
import runpy

from common import *

# ----------------- Friend Table tab -----------------
class FriendTableTab(QtWidgets.QWidget):
    """
    Friend Table tab for D-3.
    Uses:
        export_d3_friend_table.py
        import_d3_friend_table.py
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self.current_bin_type_key = None
        self.current_bin_path = None

        self.name_map = {}
        self.sprite_map = {}
        self.shot_sound_map = {}
        self.friend_hidden_rows = {}

        self._build_ui()
        self.load_mappings()

    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)

        top_box = QtWidgets.QGroupBox("BIN Selection")
        top_layout = QtWidgets.QHBoxLayout(top_box)

        self.bin_type_combo = NoWheelComboBox()
        self.bin_type_combo.addItem("Select BIN type...")
        for key, info in BIN_TYPES.items():
            self.bin_type_combo.addItem(info["label"], key)

        self.bin_path_edit = QtWidgets.QLineEdit()
        self.bin_path_edit.setReadOnly(True)
        self.bin_browse_btn = QtWidgets.QPushButton("Select .bin file...")

        top_layout.addWidget(QtWidgets.QLabel("Type of .bin file:"))
        top_layout.addWidget(self.bin_type_combo)
        top_layout.addSpacing(20)
        top_layout.addWidget(QtWidgets.QLabel("Selected .bin:"))
        top_layout.addWidget(self.bin_path_edit)
        top_layout.addWidget(self.bin_browse_btn)

        main_layout.addWidget(top_box)

        io_box = QtWidgets.QGroupBox("Friend Table CSV & In-App Editing")
        io_layout = QtWidgets.QGridLayout(io_box)

        default_csv = os.path.join(os.path.expanduser("~"), "Desktop", "d3_friend_table.csv")
        self.export_csv_edit = QtWidgets.QLineEdit(default_csv)

        self.export_btn = QtWidgets.QPushButton("Export Friend Table to CSV")
        self.export_btn.setStyleSheet("background-color:#0006b1;color:white;font-weight:600;font-size:14pt;")

        self.import_btn = QtWidgets.QPushButton("Import Friend Table from CSV")
        self.import_btn.setStyleSheet("background-color:#0006b1;color:white;font-weight:600;font-size:14pt;")

        self.load_table_btn = QtWidgets.QPushButton("Refresh")
        self.load_table_btn.setStyleSheet("background-color:#008000;color:white;font-weight:600;font-size:14pt;")

        self.reset_btn = QtWidgets.QPushButton("Reset to Original ?")
        self.reset_btn.setStyleSheet("background-color:#960202;color:white;font-weight:600;font-size:14pt;")

        self.save_edits_btn = QtWidgets.QPushButton("Save Friend Table Edits to BIN")
        self.save_edits_btn.setStyleSheet("background-color:#008000;color:white;font-weight:600;font-size:14pt;")
        self.save_edits_btn.setEnabled(False)

        io_layout.addWidget(QtWidgets.QLabel("Export CSV path:"), 0, 0)
        io_layout.addWidget(self.export_csv_edit, 0, 1)
        io_layout.addWidget(self.export_btn, 0, 2)

        io_layout.addWidget(self.load_table_btn, 1, 0)
        io_layout.addWidget(self.reset_btn, 1, 1)
        io_layout.addWidget(self.save_edits_btn, 1, 2)
        io_layout.addWidget(self.import_btn, 1, 3)

        main_layout.addWidget(io_box)

        self.table = QtWidgets.QTableWidget()
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        main_layout.addWidget(self.table, 1)

        self.status_label = QtWidgets.QLabel("Ready.")
        self.status_label.setWordWrap(True)
        main_layout.addWidget(self.status_label)

        self.bin_type_combo.currentIndexChanged.connect(self.on_bin_type_changed)
        self.bin_browse_btn.clicked.connect(self.on_select_bin_file)
        self.export_btn.clicked.connect(self.on_export_clicked)
        self.import_btn.clicked.connect(self.on_import_clicked)
        self.load_table_btn.clicked.connect(self.on_load_table_clicked)
        self.reset_btn.clicked.connect(self.on_reset_clicked)
        self.save_edits_btn.clicked.connect(self.on_save_edits_clicked)

    def _short_status(self, msg):
        return msg if len(msg) <= 100 else msg[:97] + "..."

    def is_digivice(self):
        return self.current_bin_type_key == "Digivice"

    def get_export_script(self):
        return (
            "export_digivice_friend_table.py"
            if self.is_digivice()
            else "export_d3_friend_table.py"
        )

    def get_import_script(self):
        return (
            "import_digivice_friend_table.py"
            if self.is_digivice()
            else "import_d3_friend_table.py"
        )

    def get_original_csv(self):
        return (
            "digivice_friend_table_original.csv"
            if self.is_digivice()
            else "d3_friend_table_original.csv"
        )

    def get_sprite_map_csv(self):
        return (
            "digivice_sprite_map.csv"
            if self.is_digivice()
            else "d3_sprite_map.csv"
        )

    def get_shot_sound_map_csv(self):
        return (
            "digivice_attack_shot_sound_id_map.csv"
            if self.is_digivice()
            else "d3_attack_shot_sound_id_map.csv"
        )

    def get_names_export_script(self):
        return (
            "export_digivice_names.py"
            if self.is_digivice()
            else "export_d3_names.py"
        )

    def get_default_export_csv(self):
        return os.path.join(
            os.path.expanduser("~"),
            "Desktop",
            (
                "digivice_friend_table.csv"
                if self.is_digivice()
                else "d3_friend_table.csv"
            )
        )

    def load_mappings(self):
        def csv_path(name):
            return os.path.join(SCRIPT_DIR, name)

        self.name_map = {}

        if not self.current_bin_type_key:
            self.sprite_map = {}
            self.shot_sound_map = {}
            return

        self.sprite_map = self.load_simple_map(csv_path(self.get_sprite_map_csv()))
        self.shot_sound_map = self.load_simple_map(csv_path(self.get_shot_sound_map_csv()))

    def load_simple_map(self, path):
        m = {}
        if not os.path.isfile(path):
            return m

        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                key = str(row.get("key", "")).strip()
                value = str(row.get("value", "")).strip()
                if key:
                    m[key] = value
        return m

    def build_name_map_from_bin(self):
        if not self.current_bin_path or not os.path.isfile(self.current_bin_path):
            return {}

        tmp_dir = tempfile.mkdtemp(prefix="friend_names_map_")
        tmp_csv = os.path.join(tmp_dir, "names_tmp.csv")

        script_name = self.get_names_export_script()
        script = os.path.join(SCRIPT_DIR, script_name)
        replace_map = os.path.join(SCRIPT_DIR, "replace_map.csv")

        if not os.path.isfile(script):
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return {}

        old_argv = sys.argv
        try:
            sys.argv = [
                script_name,
                self.current_bin_path,
                replace_map,
                tmp_csv,
            ]

            runpy.run_path(script, run_name="__main__")

            mapping = {}
            with open(tmp_csv, encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    si = str(row.get("string_index", "")).strip()
                    name = str(row.get("name", "")).strip()
                    if si and name:
                        mapping[f"{name} ({si})"] = si

            return mapping

        except Exception as e:
            print(f"[WARN] Failed to build friend name map from BIN: {e}")
            return {}

        finally:
            sys.argv = old_argv
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def make_spin(self, value):
        spin = QtWidgets.QSpinBox()
        spin.setMinimum(0)
        spin.setMaximum(65535)
        try:
            spin.setValue(int(str(value).strip(), 0))
        except Exception:
            spin.setValue(0)
        return spin

    def make_combo(self, mapping, current_value):
        combo = NoWheelComboBox()
        current_value = str(current_value).strip()

        matched = False
        for key, value in mapping.items():
            value = str(value).strip()
            combo.addItem(key, value)
            if value == current_value:
                combo.setCurrentText(key)
                matched = True

        if not matched:
            fallback = f"(current value: {current_value})"
            combo.insertItem(0, fallback, current_value)
            combo.setCurrentIndex(0)

        return combo

    def on_bin_type_changed(self, index):
        if index <= 0:
            self.current_bin_type_key = None
        else:
            self.current_bin_type_key = self.bin_type_combo.itemData(index)

        if self.current_bin_type_key:
            self.export_csv_edit.setText(self.get_default_export_csv())

        self.load_mappings()

    def require_all(self):
        if not self.current_bin_type_key:
            QtWidgets.QMessageBox.warning(self, "Type required", "Please select the BIN type first.")
            return False

        if not self.current_bin_path or not os.path.isfile(self.current_bin_path):
            QtWidgets.QMessageBox.warning(self, "BIN required", "Please select a valid .bin file.")
            return False

        return True

    def on_select_bin_file(self):
        if not self.current_bin_type_key:
            QtWidgets.QMessageBox.warning(self, "Type required", "Please select the BIN type first.")
            return

        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select .bin file",
            "",
            "BIN files (*.bin);;All files (*)",
        )
        if not path:
            return

        self.current_bin_path = path
        self.bin_path_edit.setText(path)
        self.name_map = self.build_name_map_from_bin()

        self.on_load_table_clicked()

    def on_export_clicked(self):
        if not self.require_all():
            return

        out_csv = self.export_csv_edit.text().strip()
        if not out_csv:
            QtWidgets.QMessageBox.warning(self, "CSV path required", "Please specify an export CSV path.")
            return

        script = self.get_export_script()
        if not os.path.isfile(os.path.join(SCRIPT_DIR, script)):
            QtWidgets.QMessageBox.critical(self, "Missing script", f"{script} not found next to this GUI.")
            return

        dlg = BusyDialog("Export Friend Table", "Please wait...\nExporting friend table.", self)

        worker = InternalScriptWorker(
            script_name=script,
            script_args=[self.current_bin_path, out_csv],
            desc="Export Friend Table",
        )

        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        def done(ok, msg):
            dlg.accept()
            thread.quit()
            thread.wait()
            self.status_label.setText(self._short_status(msg))

            if ok:
                QtWidgets.QMessageBox.information(
                    self,
                    "Friend Table Exported",
                    f"Friend table was exported to {out_csv} on your Desktop.",
                )
                self.populate_table_from_csv(out_csv)
                self.save_edits_btn.setEnabled(True)
            else:
                QtWidgets.QMessageBox.critical(self, "Export Friend Table Error", msg)

        worker.finished.connect(done)
        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()

    def on_import_clicked(self):
        if not self.require_all():
            return

        in_csv, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select d3_friend_table.csv",
            "",
            "CSV files (*.csv);;All files (*)",
        )
        if not in_csv:
            return

        self.run_import_script(in_csv, reload_after=True)

    def on_load_table_clicked(self):
        if not self.require_all():
            return

        tmp_dir = tempfile.mkdtemp(prefix="d3_friend_table_gui_")
        tmp_csv = os.path.join(tmp_dir, "d3_friend_table_tmp.csv")

        script = self.get_export_script()
        if not os.path.isfile(os.path.join(SCRIPT_DIR, script)):
            QtWidgets.QMessageBox.critical(self, "Missing script", f"{script} not found next to this GUI.")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        dlg = BusyDialog("Refresh", "Please wait...\nLoading friend table from D3.bin.", self)

        worker = InternalScriptWorker(
            script_name=script,
            script_args=[self.current_bin_path, tmp_csv],
            desc="Refresh Friend Table",
        )

        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        def done(ok, msg):
            dlg.accept()
            thread.quit()
            thread.wait()

            if ok:
                try:
                    self.name_map = self.build_name_map_from_bin()
                    self.populate_table_from_csv(tmp_csv)
                    self.save_edits_btn.setEnabled(True)
                    self.status_label.setText("Friend table loaded.")
                except Exception as e:
                    QtWidgets.QMessageBox.critical(self, "CSV error", f"Failed to refresh Friend Table:\n{e}")
                    self.status_label.setText("Friend table load failed.")
            else:
                self.status_label.setText(self._short_status(msg))
                QtWidgets.QMessageBox.critical(self, "Refresh Friend Table Error", msg)

            shutil.rmtree(tmp_dir, ignore_errors=True)

        worker.finished.connect(done)
        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()

    def on_reset_clicked(self):
        if not self.require_all():
            return

        original_csv = os.path.join(SCRIPT_DIR, self.get_original_csv())

        if not os.path.isfile(original_csv):
            QtWidgets.QMessageBox.critical(
                self,
                "Missing file",
                f"{self.get_original_csv()} not found next to this GUI."
            )
            return

        res = QtWidgets.QMessageBox.warning(
            self,
            "Reset Friend Table to Original?",
            (
                "This will overwrite ALL friend table data in the BIN\n"
                "with the baseline values from your bin file.\n\n"
                "You will not lose game progress. But friend table modding changes will be lost.\n\n"
                "Continue?"
            ),
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )

        if res != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        self.run_import_script(original_csv, reload_after=True)

    def populate_table_from_csv(self, csv_path):
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))

        headers = [
            "digimon_id",
            "string_index",
            "sprite_index",
            "attack_shot_sprite_index",
            "attack_shot_sound_id",
        ]

        pretty = [
            "digimon_id",
            "Name",
            "sprite_index",
            "attack_shot_sprite_index",
            "attack_shot_sound_id",
        ]

        self.table.clear()
        self.table.setRowCount(len(rows))
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(pretty)

        self.friend_hidden_rows = {}

        for r_idx, row in enumerate(rows):
            self.table.setVerticalHeaderItem(
                r_idx,
                QtWidgets.QTableWidgetItem(str(r_idx + 1))
            )

            self.friend_hidden_rows[r_idx] = {
                "meta_offset": str(row.get("meta_offset", "")),
                "data_offset": str(row.get("data_offset", "")),
                "unknown": str(row.get("unknown", "0")),
            }

            self.table.setCellWidget(
                r_idx,
                0,
                self.make_spin(row.get("digimon_id", 0)),
            )

            self.table.setCellWidget(
                r_idx,
                1,
                self.make_combo(self.name_map, row.get("string_index", "")),
            )

            self.table.setCellWidget(
                r_idx,
                2,
                self.make_combo(self.sprite_map, row.get("sprite_index", "")),
            )

            self.table.setCellWidget(
                r_idx,
                3,
                self.make_spin(row.get("attack_shot_sprite_index", 0)),
            )

            self.table.setCellWidget(
                r_idx,
                4,
                self.make_combo(self.shot_sound_map, row.get("attack_shot_sound_id", "")),
            )

        self.table.resizeColumnsToContents()

        self.table.setColumnWidth(1, 160)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.Fixed
        )

    def on_save_edits_clicked(self):
        if not self.require_all():
            return

        if self.table.rowCount() == 0:
            QtWidgets.QMessageBox.information(self, "No data", "There is no friend table loaded.")
            return

        rows_out = []

        for r in range(self.table.rowCount()):
            hidden = self.friend_hidden_rows.get(r, {})

            row = {
                "meta_offset": hidden.get("meta_offset", ""),
                "data_offset": hidden.get("data_offset", ""),
                "digimon_id": self.table.cellWidget(r, 0).value(),
                "string_index": self.table.cellWidget(r, 1).currentData(),
                "sprite_index": self.table.cellWidget(r, 2).currentData(),
                "attack_shot_sprite_index": self.table.cellWidget(r, 3).value(),
                "attack_shot_sound_id": self.table.cellWidget(r, 4).currentData(),
                "unknown": hidden.get("unknown", "0"),
            }

            rows_out.append(row)

        fieldnames = [
            "meta_offset",
            "data_offset",
            "digimon_id",
            "string_index",
            "sprite_index",
            "attack_shot_sprite_index",
            "attack_shot_sound_id",
            "unknown",
        ]

        tmp_dir = tempfile.mkdtemp(prefix="d3_friend_table_save_")
        tmp_csv = os.path.join(tmp_dir, "d3_friend_table_edit.csv")

        try:
            with open(tmp_csv, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows_out)
        except Exception as e:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            QtWidgets.QMessageBox.critical(self, "CSV error", f"Failed to write temp CSV:\n{e}")
            return

        self.run_import_script(tmp_csv, reload_after=True, cleanup_dir=tmp_dir)

    def run_import_script(self, csv_path, reload_after=False, cleanup_dir=None):
        script = self.get_import_script()

        if not os.path.isfile(os.path.join(SCRIPT_DIR, script)):
            QtWidgets.QMessageBox.critical(self, "Missing script", f"{script} not found next to this GUI.")
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
            return

        dlg = BusyDialog("Import Friend Table", "Please wait...\nApplying friend table changes to BIN.", self)

        worker = InternalScriptWorker(
            script_name=script,
            script_args=[
                self.current_bin_path,
                csv_path,
                self.current_bin_path,
            ],
            desc="Import Friend Table",
        )

        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        def done(ok, msg):
            dlg.accept()
            thread.quit()
            thread.wait()

            self.status_label.setText(self._short_status(msg))

            if ok:
                QtWidgets.QMessageBox.information(self, "Friend Table Imported", msg)
                if reload_after:
                    self.on_load_table_clicked()
            else:
                QtWidgets.QMessageBox.critical(self, "Friend Table Import Error", msg)

            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)

        worker.finished.connect(done)
        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()