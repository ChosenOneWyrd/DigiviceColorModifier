from PyQt5 import QtCore, QtGui, QtWidgets
import os
import shutil
import tempfile
import shutil
import csv
import runpy

from common import *

# ----------------- Names tab -----------------
class NamesTab(QtWidgets.QWidget):
    """
    Names tab — edits names for:
        D-3 25th Color Evolution
        Digivice 25th Color Evolution
        D-ark 25th Color Evolution
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self.current_bin_type_key = None
        self.current_bin_path = None
        self.replace_map_path = os.path.join(SCRIPT_DIR, "replace_map.csv")

        self.original_names = []
        self._last_forbidden_indexes = []

        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        # ---------- BIN Selection ----------
        top_box = QtWidgets.QGroupBox("BIN Selection")
        top = QtWidgets.QHBoxLayout(top_box)

        self.bin_type_combo = NoWheelComboBox()
        self.bin_type_combo.addItem("Select BIN type...")
        for key, info in BIN_TYPES.items():
            self.bin_type_combo.addItem(info["label"], key)

        self.bin_path_edit = QtWidgets.QLineEdit()
        self.bin_path_edit.setReadOnly(True)
        self.bin_btn = QtWidgets.QPushButton("Select .bin")

        top.addWidget(QtWidgets.QLabel("Type:"))
        top.addWidget(self.bin_type_combo)
        top.addSpacing(20)
        top.addWidget(QtWidgets.QLabel("BIN:"))
        top.addWidget(self.bin_path_edit)
        top.addWidget(self.bin_btn)

        layout.addWidget(top_box)

        # ---------- Controls ----------
        io_box = QtWidgets.QGroupBox("Names CSV & In-App Editing")
        io_layout = QtWidgets.QGridLayout(io_box)

        default_csv = os.path.join(os.path.expanduser("~"), "Desktop", "d3_names.csv")
        self.export_csv_edit = QtWidgets.QLineEdit(default_csv)

        self.export_btn = QtWidgets.QPushButton("Export Names to CSV")
        self.export_btn.setStyleSheet("background-color:#0006b1;color:white;font-weight:600;font-size:14pt;")

        self.import_btn = QtWidgets.QPushButton("Import Names from CSV")
        self.import_btn.setStyleSheet("background-color:#0006b1;color:white;font-weight:600;font-size:14pt;")

        self.load_table_btn = QtWidgets.QPushButton("Refresh")
        self.load_table_btn.setStyleSheet("background-color:#008000;color:white;font-weight:600;font-size:14pt;")

        self.reset_btn = QtWidgets.QPushButton("Reset to Original ?")
        self.reset_btn.setStyleSheet("background-color:#960202;color:white;font-weight:600;font-size:14pt;")

        self.save_edits_btn = QtWidgets.QPushButton("Save Name Edits to BIN")
        self.save_edits_btn.setStyleSheet("background-color:#008000;color:white;font-weight:600;font-size:14pt;")
        self.save_edits_btn.setEnabled(False)

        io_layout.addWidget(QtWidgets.QLabel("Export CSV path:"), 0, 0)
        io_layout.addWidget(self.export_csv_edit, 0, 1)
        io_layout.addWidget(self.export_btn, 0, 2)

        io_layout.addWidget(self.load_table_btn, 1, 0)
        io_layout.addWidget(self.reset_btn, 1, 1)
        io_layout.addWidget(self.save_edits_btn, 1, 2)
        io_layout.addWidget(self.import_btn, 1, 3)

        layout.addWidget(io_box)

        # ---------- Table ----------
        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["string_index", "Name"])
        # self.table.setColumnHidden(0, True)
        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked |
            QtWidgets.QAbstractItemView.EditTrigger.SelectedClicked |
            QtWidgets.QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)

        layout.addWidget(self.table, 1)

        # ---------- Status ----------
        self.status_label = QtWidgets.QLabel("Ready.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # ---------- Signals ----------
        self.bin_type_combo.currentIndexChanged.connect(self.on_type_changed)
        self.bin_btn.clicked.connect(self.pick_bin)
        self.export_btn.clicked.connect(self.export_names)
        self.import_btn.clicked.connect(self.import_names)
        self.load_table_btn.clicked.connect(self.load_names_clicked)
        self.save_edits_btn.clicked.connect(self.save_edits_clicked)
        self.reset_btn.clicked.connect(self.on_reset_clicked)

    def _short_status(self, msg: str) -> str:
        return msg if len(msg) <= 100 else msg[:97] + "..."
    
    def get_names_export_script(self):
        if self.current_bin_type_key == "D-3":
            return "export_d3_names.py"

        if self.current_bin_type_key == "Digivice":
            return "export_digivice_names.py"

        if self.current_bin_type_key == "D-ark":
            return "export_d_ark_names.py"

        raise RuntimeError(f"Unsupported BIN type for names export: {self.current_bin_type_key}")


    def get_names_import_script(self):
        if self.current_bin_type_key == "D-3":
            return "import_d3_names.py"

        if self.current_bin_type_key == "Digivice":
            return "import_digivice_names.py"

        if self.current_bin_type_key == "D-ark":
            return "import_d_ark_names.py"

        raise RuntimeError(f"Unsupported BIN type for names import: {self.current_bin_type_key}")


    def get_names_original_csv(self):
        if self.current_bin_type_key == "D-3":
            return "d3_names_original.csv"

        if self.current_bin_type_key == "Digivice":
            return "digivice_names_original.csv"

        if self.current_bin_type_key == "D-ark":
            return "d_ark_names_original.csv"

        raise RuntimeError(f"Unsupported BIN type for original names: {self.current_bin_type_key}")


    def get_default_names_csv(self):
        if self.current_bin_type_key == "D-3":
            fn = "d3_names.csv"

        elif self.current_bin_type_key == "Digivice":
            fn = "digivice_names.csv"

        elif self.current_bin_type_key == "D-ark":
            fn = "d_ark_names.csv"

        else:
            fn = "names.csv"

        return os.path.join(os.path.expanduser("~"), "Desktop", fn,)

    def require_all(self):
        if not self.current_bin_type_key:
            QtWidgets.QMessageBox.warning(self, "Missing type", "Select BIN type first.")
            return False

        if self.current_bin_type_key not in ("D-3", "Digivice", "D-ark"):
            QtWidgets.QMessageBox.warning(self, "Unsupported type", "Names editing is only enabled for D-3, Digivice, and D-ark.")
            return False

        if not self.current_bin_path or not os.path.isfile(self.current_bin_path):
            QtWidgets.QMessageBox.warning(self, "Missing BIN", "Select a valid .bin file.")
            return False

        if not self.replace_map_path or not os.path.isfile(self.replace_map_path):
            QtWidgets.QMessageBox.warning(self, "Missing replace_map.csv", "replace_map.csv was not found.")
            return False

        return True

    def on_type_changed(self, idx):
        if idx <= 0:
            self.current_bin_type_key = None
        else:
            self.current_bin_type_key = self.bin_type_combo.itemData(idx)

        if self.current_bin_type_key in ("D-3", "Digivice", "D-ark"):
            self.export_csv_edit.setText(self.get_default_names_csv())

    def pick_bin(self):
        if not self.current_bin_type_key:
            QtWidgets.QMessageBox.warning(self, "Type required", "Select BIN type first.")
            return

        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select .bin", "", "BIN files (*.bin);;All files (*)")
        if not path:
            return

        self.current_bin_path = path
        self.bin_path_edit.setText(path)

        # Auto-load table after selecting BIN
        self.load_names_clicked()

    def export_names(self):
        if not self.require_all():
            return

        out_csv = self.export_csv_edit.text().strip()
        if not out_csv:
            QtWidgets.QMessageBox.warning(self, "CSV path required", "Please specify an export CSV path.")
            return

        script = self.get_names_export_script()
        script_path = os.path.join(SCRIPT_DIR, script)
        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.critical(self, "Missing script", f"{script} not found next to this GUI.")
            return

        dlg = BusyDialog("Export Names", "Please wait...\nThis should be faster than the old NPC exporter.", self)

        worker = InternalScriptWorker(
            script_name=script,
            script_args=[self.current_bin_path, self.replace_map_path, out_csv],
            desc="Export Names"
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
                    "Names Exported",
                    f'Names were exported to "{os.path.basename(out_csv)}" on your Desktop.'
                )
                try:
                    self.populate_table_from_csv(out_csv)
                    self.save_edits_btn.setEnabled(True)
                except Exception as e:
                    QtWidgets.QMessageBox.warning(self, "Table load warning", f"Export worked, but table load failed:\n{e}")
            else:
                QtWidgets.QMessageBox.critical(self, "Export Names Error", msg)

        worker.finished.connect(done)
        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()

    def import_names(self):
        if not self.require_all():
            return

        csv_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select names csv",
            "",
            "CSV files (*.csv);;All files (*)"
        )
        if not csv_path:
            return

        if not os.path.isfile(csv_path):
            QtWidgets.QMessageBox.warning(self, "Invalid CSV", "Selected CSV file does not exist.")
            return

        self.run_import_script(csv_path, reload_after=True)

    def load_names_clicked(self):
        if not self.require_all():
            return

        tmp_dir = tempfile.mkdtemp(prefix="names_gui_")
        tmp_csv = os.path.join(tmp_dir, "names_tmp.csv")

        script = self.get_names_export_script()
        script_path = os.path.join(SCRIPT_DIR, script)
        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.critical(self, "Missing script", f"{script} not found next to this GUI.")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        dlg = BusyDialog("Refresh", "Please wait...\nLoading names from bin.", self)

        worker = InternalScriptWorker(
            script_name=script,
            script_args=[self.current_bin_path, self.replace_map_path, tmp_csv],
            desc="Refresh"
        )

        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        def done(ok, msg):
            dlg.accept()
            thread.quit()
            thread.wait()

            if ok:
                try:
                    self.populate_table_from_csv(tmp_csv)
                    self.save_edits_btn.setEnabled(True)
                    self.status_label.setText("Names loaded into table.")
                except Exception as e:
                    QtWidgets.QMessageBox.critical(self, "CSV error", f"Failed to load names table:\n{e}")
                    self.status_label.setText("Name table load failed.")
            else:
                self.status_label.setText(self._short_status(msg))
                QtWidgets.QMessageBox.critical(self, "Load Names Error", msg)

            shutil.rmtree(tmp_dir, ignore_errors=True)

        worker.finished.connect(done)
        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()

    def populate_table_from_csv(self, csv_path):
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))

        self.table.clear()
        self.table.setRowCount(len(rows))
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["string_index", "Name"])
        # self.table.setColumnHidden(0, True)

        self.original_names = []

        for r_idx, row in enumerate(rows):
            si = str(row.get("string_index", ""))
            name = str(row.get("name", ""))

            self.original_names.append(name)

            idx_item = QtWidgets.QTableWidgetItem(si)
            idx_item.setFlags(idx_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
            idx_item.setBackground(QtGui.QColor(70, 70, 70))
            idx_item.setForeground(QtGui.QColor(200, 200, 200))
            self.table.setColumnWidth(0, 80)
            self.table.horizontalHeader().setSectionResizeMode(
                0, QtWidgets.QHeaderView.ResizeMode.Fixed
            )

            name_item = QtWidgets.QTableWidgetItem(name)

            self.table.setItem(r_idx, 0, idx_item)
            self.table.setItem(r_idx, 1, name_item)
        
        for r in range(self.table.rowCount()):
            idx_item = self.table.item(r, 0)  # string_index column
            if idx_item:
                self.table.setVerticalHeaderItem(
                    r,
                    QtWidgets.QTableWidgetItem(idx_item.text())
                )

        self.table.resizeColumnsToContents()

    def save_edits_clicked(self):
        if not self.require_all():
            return

        if self.table.rowCount() == 0:
            QtWidgets.QMessageBox.information(self, "No names", "There are no names loaded in the table.")
            return

        rows_out = []
        self._last_forbidden_indexes = []

        for r in range(self.table.rowCount()):
            idx_item = self.table.item(r, 0)
            name_item = self.table.item(r, 1)

            si = idx_item.text() if idx_item else ""
            new_name = name_item.text() if name_item else ""
            old_name = self.original_names[r] if r < len(self.original_names) else new_name

            # Same GUI-side safety as your old table:
            # forbidden -> keep old
            # longer -> keep old
            # shorter -> pad with underscores
            if any(c in FORBIDDEN_CHARS for c in new_name):
                name_to_write = old_name
                if new_name != old_name:
                    self._last_forbidden_indexes.append(si)
            else:
                if len(new_name) > len(old_name):
                    name_to_write = old_name
                elif len(new_name) < len(old_name):
                    name_to_write = new_name + ("_" * (len(old_name) - len(new_name)))
                else:
                    name_to_write = new_name

            rows_out.append({
                "string_index": si,
                "name": name_to_write,
            })

        tmp_dir = tempfile.mkdtemp(prefix="names_save_")
        tmp_csv = os.path.join(tmp_dir, "names_edit.csv")

        try:
            with open(tmp_csv, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["string_index", "name"])
                writer.writeheader()
                writer.writerows(rows_out)
        except Exception as e:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            QtWidgets.QMessageBox.critical(self, "CSV error", f"Failed to write temp CSV:\n{e}")
            return

        self.run_import_script(tmp_csv, reload_after=True, cleanup_dir=tmp_dir)

    def on_reset_clicked(self):
        if not self.require_all():
            return

        original_csv = os.path.join(SCRIPT_DIR, self.get_names_original_csv())

        if not os.path.isfile(original_csv):
            QtWidgets.QMessageBox.critical(
                self,
                "Missing file",
                f"{self.get_names_original_csv()} not found next to this GUI."
            )
            return

        res = QtWidgets.QMessageBox.warning(
            self,
            "Reset Names to Original?",
            (
                "This will overwrite ALL names in this .bin file\n"
                "with the baseline names from the original .bin file.\n\n"
                "Note that this will not reset the names with forbidden characters.\n\n"
                "You will not lose game progress. But name modding changes will be lost.\n\n"
                "Continue?"
            ),
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )

        if res != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        self.run_import_script(original_csv, reload_after=True)

    def run_import_script(self, csv_path, reload_after=False, cleanup_dir=None):
        script = self.get_names_import_script()
        script_path = os.path.join(SCRIPT_DIR, script)

        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.critical(self, "Missing script", f"{script} not found next to this GUI.")
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
            return

        dlg = BusyDialog("Import Names", "Please wait...\nApplying name changes to BIN.", self)

        worker = InternalScriptWorker(
            script_name=script,
            script_args=[
                self.current_bin_path,
                csv_path,
                self.replace_map_path,
                "--out",
                self.current_bin_path,
            ],
            desc="Import Names"
        )

        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        def done(ok, msg):
            dlg.accept()
            thread.quit()
            thread.wait()

            self.status_label.setText(self._short_status(msg))

            if ok:
                extra = ""
                if self._last_forbidden_indexes:
                    extra = (
                        "\n\nOnly letters and numbers are allowed in names. "
                        "These string_index values were skipped:\n"
                        + ", ".join(str(x) for x in self._last_forbidden_indexes)
                    )

                QtWidgets.QMessageBox.information(self, "Names Imported", msg + extra)

                if reload_after:
                    self.load_names_clicked()
            else:
                QtWidgets.QMessageBox.critical(self, "Import Names Error", msg)

            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)

        worker.finished.connect(done)
        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()