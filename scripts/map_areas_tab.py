from PyQt5 import QtCore, QtGui, QtWidgets
import csv
import os
import shutil
import sys
import tempfile

from common import *


FRIEND_COLUMNS = [
    "friend_digimon_id",
    "friend_digimon_id_2",
    "friend_digimon_id_3",
    "friend_digimon_id_4",
]

NUMERIC_COLUMNS = {
    "steps",
    "friend_digimon_id",
    "friend_digimon_id_2",
    "friend_digimon_id_3",
    "friend_digimon_id_4",
    "enemy_stage",
    "shake_mash_follow_win_count",
    "min_stage_req",
    "digital_gate_open",
}

HIDDEN_EXACT_COLUMNS = {"map_id"}
HIDDEN_PREFIXES = ("skip", "unknown")


class MapAreasTab(QtWidgets.QWidget):
    """
    Map Areas tab for D-3 and Digivice.

    Uses:
        export_d3_map_areas.py / import_d3_map_areas.py
        export_digivice_map_areas.py / import_digivice_map_areas.py

    Important safety behavior:
      - map_id, skip*, and unknown* columns are kept hidden and preserved during GUI Save.
      - rows containing 57345 in any friend_digimon_id* column are greyed out and preserved
        during GUI Save, Import, and Reset operations.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_bin_type_key = None
        self.current_bin_path = None

        self.rows = []
        self.headers = []
        self.visible_headers = []
        self.disabled_rows = set()
        self.map_data = {}

        self._build_ui()
        self.load_mappings()

    # ---------------- UI ----------------

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

        io_box = QtWidgets.QGroupBox("Map Areas CSV & In-App Editing")
        io_layout = QtWidgets.QGridLayout(io_box)

        self.export_csv_edit = QtWidgets.QLineEdit(self.get_default_export_csv())

        self.export_btn = QtWidgets.QPushButton("Export Map Areas to CSV")
        self.export_btn.setStyleSheet("background-color:#0006b1;color:white;font-weight:600;font-size:14pt;")

        self.import_btn = QtWidgets.QPushButton("Import Map Areas from CSV")
        self.import_btn.setStyleSheet("background-color:#0006b1;color:white;font-weight:600;font-size:14pt;")

        self.load_table_btn = QtWidgets.QPushButton("Refresh")
        self.load_table_btn.setStyleSheet("background-color:#008000;color:white;font-weight:600;font-size:14pt;")

        self.reset_btn = QtWidgets.QPushButton("Reset to Original ?")
        self.reset_btn.setStyleSheet("background-color:#960202;color:white;font-weight:600;font-size:14pt;")

        self.save_edits_btn = QtWidgets.QPushButton("Save Map Area Edits to BIN")
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
        self.table.setAlternatingRowColors(True)
        main_layout.addWidget(self.table, 1)

        self.status_label = QtWidgets.QLabel(
            "Ready. Rows containing 57345 in a friend_digimon_id column are protected and greyed out."
        )
        self.status_label.setWordWrap(True)
        main_layout.addWidget(self.status_label)

        self.bin_type_combo.currentIndexChanged.connect(self.on_bin_type_changed)
        self.bin_browse_btn.clicked.connect(self.on_select_bin_file)
        self.export_btn.clicked.connect(self.on_export_clicked)
        self.import_btn.clicked.connect(self.on_import_clicked)
        self.load_table_btn.clicked.connect(self.on_load_table_clicked)
        self.save_edits_btn.clicked.connect(self.on_save_edits_clicked)
        self.reset_btn.clicked.connect(self.on_reset_clicked)

    def _short_status(self, msg):
        return msg if len(msg) <= 120 else msg[:117] + "..."

    # ---------------- config ----------------

    def is_digivice(self):
        return self.current_bin_type_key == "Digivice"

    def get_export_script(self):
        return "export_digivice_map_areas.py" if self.is_digivice() else "export_d3_map_areas.py"

    def get_import_script(self):
        return "import_digivice_map_areas.py" if self.is_digivice() else "import_d3_map_areas.py"

    def get_original_csv(self):
        return "digivice_map_areas_original.csv" if self.is_digivice() else "d3_map_areas_original.csv"

    def get_default_export_csv(self):
        name = "digivice_map_areas.csv" if self.is_digivice() else "d3_map_areas.csv"
        return os.path.join(os.path.expanduser("~"), "Desktop", name)

    def mapping_files(self):
        if self.is_digivice():
            return {
                "encounter_type": "digivice_encounter_type_map.csv",
                "encounter_type_2": "digivice_encounter_type_map.csv",
                "area_id": "digivice_area_id_map.csv",
                "region_id": "digivice_region_id_map.csv",
                "battle_type": "digivice_battle_type_map.csv",
            }
        return {
            "encounter_type": "d3_encounter_type_map.csv",
            "encounter_type_2": "d3_encounter_type_map.csv",
            "area_id": "d3_area_id_map.csv",
            "region_id": "d3_region_id_map.csv",
            "battle_type": "d3_battle_type_map.csv",
            "boss_cut_scene_id": "d3_boss_cut_scene_id_map.csv",
        }

    def load_mappings(self):
        self.map_data = {}
        for column, filename in self.mapping_files().items():
            self.map_data[column] = self.load_simple_map(os.path.join(SCRIPT_DIR, filename))

    def load_simple_map(self, path):
        mapping = {}
        if not os.path.isfile(path):
            return mapping
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = str(row.get("key", "")).strip()
                value = str(row.get("value", "")).strip()
                if key != "":
                    mapping[key] = value
        return mapping

    def require_all(self):
        if not self.current_bin_type_key:
            QtWidgets.QMessageBox.warning(self, "Type required", "Please select the BIN type first.")
            return False
        if self.current_bin_type_key not in ("D-3", "Digivice"):
            QtWidgets.QMessageBox.warning(self, "Unsupported type", "Map Areas editing supports D-3 and Digivice only.")
            return False
        if not self.current_bin_path or not os.path.isfile(self.current_bin_path):
            QtWidgets.QMessageBox.warning(self, "BIN required", "Please select a valid .bin file.")
            return False
        return True

    # ---------------- widgets ----------------

    def make_spin(self, value, enabled=True):
        spin = QtWidgets.QSpinBox()
        spin.setMinimum(0)
        spin.setMaximum(65535)
        try:
            spin.setValue(int(str(value).strip(), 0))
        except Exception:
            spin.setValue(0)
        spin.setEnabled(enabled)
        return spin

    def make_combo(self, mapping, current_value, enabled=True, readonly=False):
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
            combo.insertItem(
                0,
                f"(current value: {current_value})",
                current_value
            )
            combo.setCurrentIndex(0)

        combo.setEnabled(enabled)

        if readonly:
            combo._readonly_combo = True
            combo.installEventFilter(self)

        return combo
    
    def eventFilter(self, obj, event):

        if getattr(obj, "_readonly_combo", False):

            if event.type() in (
                QtCore.QEvent.MouseButtonPress,
                QtCore.QEvent.MouseButtonDblClick,
                QtCore.QEvent.Wheel,
                QtCore.QEvent.KeyPress,
            ):
                return True

        return super().eventFilter(obj, event)

    def is_hidden_column(self, column):
        return column in HIDDEN_EXACT_COLUMNS or any(column.startswith(prefix) for prefix in HIDDEN_PREFIXES)

    def is_protected_row(self, row):
        for col in FRIEND_COLUMNS:
            if str(row.get(col, "")).strip() == "57345":
                return True
        return False

    # ---------------- events ----------------

    def on_bin_type_changed(self, index):
        self.current_bin_type_key = None if index <= 0 else self.bin_type_combo.itemData(index)
        self.export_csv_edit.setText(self.get_default_export_csv())
        self.load_mappings()
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self.save_edits_btn.setEnabled(False)

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
        self.on_load_table_clicked()

    def on_export_clicked(self):
        if not self.require_all():
            return
        out_csv = self.export_csv_edit.text().strip()
        if not out_csv:
            QtWidgets.QMessageBox.warning(self, "CSV path required", "Please specify an export CSV path.")
            return
        self.run_export_script(out_csv, "Export Map Areas", load_after=True)

    def on_import_clicked(self):
        if not self.require_all():
            return
        in_csv, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select map_areas.csv",
            "",
            "CSV files (*.csv);;All files (*)",
        )
        if not in_csv:
            return
        self.run_import_with_merge(in_csv, "Import Map Areas", reload_after=True, preserve_hidden=False)

    def on_load_table_clicked(self):
        if not self.require_all():
            return
        tmp_dir = tempfile.mkdtemp(prefix="map_areas_gui_")
        tmp_csv = os.path.join(tmp_dir, "map_areas_tmp.csv")

        def after(ok, msg):
            try:
                if ok:
                    self.load_mappings()
                    self.populate_table_from_csv(tmp_csv)
                    self.save_edits_btn.setEnabled(True)
                    self.status_label.setText("Map Areas loaded.")
                else:
                    self.status_label.setText(self._short_status(msg))
                    QtWidgets.QMessageBox.critical(self, "Refresh Error", msg)
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        self.run_export_script(tmp_csv, "Refresh Map Areas", load_after=False, callback=after)

    def on_save_edits_clicked(self):
        if not self.require_all():
            return
        if not self.rows:
            QtWidgets.QMessageBox.warning(self, "Nothing loaded", "Please Refresh or Export before saving edits.")
            return

        tmp_dir = tempfile.mkdtemp(prefix="map_areas_save_")
        tmp_csv = os.path.join(tmp_dir, "map_areas_save.csv")
        try:
            rows_to_write = self.build_rows_for_save()
            self.write_csv(tmp_csv, self.headers, rows_to_write)
            self.run_import_script(tmp_csv, "Save Map Area Edits", reload_after=True, cleanup_dir=tmp_dir)
        except Exception as e:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            QtWidgets.QMessageBox.critical(self, "Save Error", str(e))

    def on_reset_clicked(self):
        if not self.require_all():
            return
        original_csv = os.path.join(SCRIPT_DIR, self.get_original_csv())
        if not os.path.isfile(original_csv):
            QtWidgets.QMessageBox.critical(self, "Missing file", f"{self.get_original_csv()} not found next to this GUI.")
            return
        res = QtWidgets.QMessageBox.warning(
            self,
            "Reset Map Areas to Original?",
            (
                "This will overwrite editable map area rows in the selected BIN with your original CSV.\n\n"
                "Rows containing 57345 in friend_digimon_id columns will stay protected.\n\n"
                "Continue?"
            ),
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if res != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self.run_import_with_merge(original_csv, "Reset Map Areas", reload_after=True, preserve_hidden=False)

    # ---------------- script runners ----------------

    def run_export_script(self, out_csv, desc, load_after=False, callback=None):
        script = self.get_export_script()
        script_path = os.path.join(SCRIPT_DIR, script)
        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.critical(self, "Missing script", f"{script} not found next to this GUI.")
            if callback:
                callback(False, f"{script} not found")
            return

        dlg = BusyDialog(desc, f"Please wait...\n{desc}.", self)
        worker = InternalScriptWorker(script_name=script, script_args=[self.current_bin_path, out_csv], desc=desc)
        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        def done(ok, msg):
            dlg.accept()
            thread.quit()
            thread.wait()
            if callback:
                callback(ok, msg)
                return
            self.status_label.setText(self._short_status(msg))
            if ok:
                if load_after:
                    try:
                        self.populate_table_from_csv(out_csv)
                        self.save_edits_btn.setEnabled(True)
                    except Exception as e:
                        QtWidgets.QMessageBox.warning(self, "Table load warning", f"Export worked, but table load failed:\n{e}")
                QtWidgets.QMessageBox.information(self, "Map Areas Exported", f"Map areas were exported to:\n{out_csv}")
            else:
                QtWidgets.QMessageBox.critical(self, f"{desc} Error", msg)

        worker.finished.connect(done)
        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()

    def run_import_script(self, in_csv, desc, reload_after=True, cleanup_dir=None):
        script = self.get_import_script()
        script_path = os.path.join(SCRIPT_DIR, script)
        if not os.path.isfile(script_path):
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
            QtWidgets.QMessageBox.critical(self, "Missing script", f"{script} not found next to this GUI.")
            return

        dlg = BusyDialog(desc, f"Please wait...\n{desc}.", self)
        worker = InternalScriptWorker(script_name=script, script_args=[self.current_bin_path, in_csv, self.current_bin_path], desc=desc)
        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        def done(ok, msg):
            dlg.accept()
            thread.quit()
            thread.wait()
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
            self.status_label.setText(self._short_status(msg))
            if ok:
                QtWidgets.QMessageBox.information(self, desc, f"{desc} completed successfully.")
                if reload_after:
                    self.on_load_table_clicked()
            else:
                QtWidgets.QMessageBox.critical(self, f"{desc} Error", msg)

        worker.finished.connect(done)
        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()

    def run_import_with_merge(self, source_csv, desc, reload_after=True, preserve_hidden=False):
        """
        Export current ROM first, overlay allowed rows from source_csv, then import.
        This is what keeps protected rows unchanged.
        """
        tmp_dir = tempfile.mkdtemp(prefix="map_areas_import_")
        current_csv = os.path.join(tmp_dir, "current.csv")
        merged_csv = os.path.join(tmp_dir, "merged.csv")

        def after_export(ok, msg):
            if not ok:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                self.status_label.setText(self._short_status(msg))
                QtWidgets.QMessageBox.critical(self, f"{desc} Error", msg)
                return
            try:
                headers, current_rows = self.read_csv(current_csv)
                source_headers, source_rows = self.read_csv(source_csv)
                if headers != source_headers:
                    raise ValueError(
                        "CSV columns do not match this device.\n\n"
                        f"Expected:\n{headers}\n\nFound:\n{source_headers}"
                    )
                if len(current_rows) != len(source_rows):
                    raise ValueError(f"Expected {len(current_rows)} rows but found {len(source_rows)} rows in source CSV.")

                merged = []
                for idx, current_row in enumerate(current_rows):
                    # if self.is_protected_row(current_row):
                    #     merged.append(dict(current_row))
                    # else:
                    #     if preserve_hidden:
                    #         row = dict(current_row)
                    #         for col in headers:
                    #             if not self.is_hidden_column(col):
                    #                 row[col] = source_rows[idx].get(col, row.get(col, "0"))
                    #         merged.append(row)
                    #     else:
                    #         merged.append(dict(source_rows[idx]))
                    if preserve_hidden:
                        row = dict(current_row)
                        for col in headers:
                            if not self.is_hidden_column(col):
                                row[col] = source_rows[idx].get(col, row.get(col, "0"))
                        merged.append(row)
                    else:
                        merged.append(dict(source_rows[idx]))
                self.write_csv(merged_csv, headers, merged)
                self.run_import_script(merged_csv, desc, reload_after=reload_after, cleanup_dir=tmp_dir)
            except Exception as e:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                QtWidgets.QMessageBox.critical(self, f"{desc} Error", str(e))

        self.run_export_script(current_csv, f"Prepare {desc}", callback=after_export)

    # ---------------- table / CSV ----------------

    def read_csv(self, path):
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            headers = list(reader.fieldnames or [])
            rows = [dict(row) for row in reader]
        return headers, rows

    def write_csv(self, path, headers, rows):
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow({h: str(row.get(h, "0")).strip() for h in headers})

    def populate_table_from_csv(self, csv_path):
        self.headers, self.rows = self.read_csv(csv_path)
        self.visible_headers = [h for h in self.headers if not self.is_hidden_column(h)]
        self.disabled_rows = {idx for idx, row in enumerate(self.rows) if self.is_protected_row(row)}

        self.table.clear()
        self.table.setRowCount(len(self.rows))
        self.table.setColumnCount(len(self.visible_headers))
        self.table.setHorizontalHeaderLabels(self.visible_headers)

        for r_idx, row in enumerate(self.rows):
            protected = r_idx in self.disabled_rows
            self.table.setVerticalHeaderItem(r_idx, QtWidgets.QTableWidgetItem(str(r_idx + 1)))

            for c_idx, column in enumerate(self.visible_headers):
                value = row.get(column, "0")
                if column in self.map_data:

                    readonly = column in ("area_id", "region_id")

                    widget = self.make_combo(
                        self.map_data[column],
                        value,
                        enabled=not protected,
                        readonly=readonly,
                    )

                    self.table.setCellWidget(r_idx, c_idx, widget)
                elif column in NUMERIC_COLUMNS:
                    widget = self.make_spin(value, enabled=not protected)
                    self.table.setCellWidget(r_idx, c_idx, widget)
                else:
                    item = QtWidgets.QTableWidgetItem(str(value))
                    flags = item.flags()
                    if protected:
                        flags &= ~QtCore.Qt.ItemFlag.ItemIsEditable
                    item.setFlags(flags)
                    self.table.setItem(r_idx, c_idx, item)

            if protected:
                self.grey_row(r_idx)

        self.table.resizeColumnsToContents()

        COLUMN_WIDTHS = {
            "area_id": 100,
            "region_id": 125,
            "battle_type": 110,

            "friend_digimon_id": 130,
            "friend_digimon_id_2": 130,
            "friend_digimon_id_3": 130,
            "min_stage_req": 100,
            "shake_mash_follow_win_count": 240,

            "encounter_type": 170,
            "encounter_type_2": 170,
            "boss_cut_scene_id": 170,
        }

        for idx, column in enumerate(self.visible_headers):
            if column in COLUMN_WIDTHS:
                self.table.setColumnWidth(idx, COLUMN_WIDTHS[column])
        self.status_label.setText(
            f"Loaded {len(self.rows)} map area rows. Protected rows: {len(self.disabled_rows)}."
        )

    def grey_row(self, row_idx):
        bg = QtGui.QColor(85, 85, 85)
        fg = QtGui.QColor(170, 170, 170)
        for c_idx in range(self.table.columnCount()):
            item = self.table.item(row_idx, c_idx)
            if item is None:
                item = QtWidgets.QTableWidgetItem("")
                self.table.setItem(row_idx, c_idx, item)
            item.setBackground(bg)
            item.setForeground(fg)
            widget = self.table.cellWidget(row_idx, c_idx)
            if widget is not None:
                widget.setEnabled(False)
                widget.setStyleSheet("background-color:#555555;color:#aaaaaa;")

    def widget_value(self, row_idx, col_idx):
        widget = self.table.cellWidget(row_idx, col_idx)
        if isinstance(widget, QtWidgets.QSpinBox):
            return str(widget.value())
        if isinstance(widget, QtWidgets.QComboBox):
            data = widget.currentData()
            return str(data if data is not None else widget.currentText())
        item = self.table.item(row_idx, col_idx)
        return "" if item is None else item.text().strip()

    def build_rows_for_save(self):
        if not self.headers or not self.rows:
            raise ValueError("No map area data is loaded.")

        new_rows = [dict(row) for row in self.rows]
        visible_index = {column: idx for idx, column in enumerate(self.visible_headers)}

        for r_idx, row in enumerate(new_rows):
            if r_idx in self.disabled_rows:
                continue
            for column, c_idx in visible_index.items():
                # Hidden columns never appear here; this keeps map_id, skip*, unknown* preserved.
                row[column] = self.widget_value(r_idx, c_idx)

        return new_rows
