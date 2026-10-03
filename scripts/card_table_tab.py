from PyQt5 import QtCore, QtWidgets
import os
import shutil
import tempfile
import sys
import csv
import runpy
from typing import Optional
from common import *

class CardTableTab(QtWidgets.QWidget):
    """
    D-Ark 25th Color Card Table editor.

    Uses:
        export_d_ark_card_table.py
        import_d_ark_card_table.py
        export_d_ark_names.py
        d_ark_effect_type_id_map.csv

    Visible GUI fields:
        card_id                  read-only
        Which Card?              static/read-only UI-only card name
        string_index             D-Ark name dropdown
        card_menu_string_index   D-Ark name dropdown
        effect_type_id           effect dropdown
        search_unlock_value      numeric
        sprite_index             numeric
        alternate_sprite_index   numeric

    Hidden-but-preserved field:
        description_string_index

    "Which Card?" is UI-only and is never exported/imported in CSV.
    """

    EXPORT_SCRIPT = "export_d_ark_card_table.py"
    IMPORT_SCRIPT = "import_d_ark_card_table.py"
    ORIGINAL_CSV = "d_ark_card_table_original.csv"
    EFFECT_MAP_CSV = "d_ark_effect_type_id_map.csv"

    def __init__(self, parent=None):
        super().__init__(parent)

        self.current_bin_path: Optional[str] = None
        self.name_map = {}
        self.effect_map = {}
        self.hidden_rows = {}

        self._build_ui()
        self.load_mappings()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)

        top_box = QtWidgets.QGroupBox("D-Ark 25th Color BIN Selection")
        top_layout = QtWidgets.QHBoxLayout(top_box)

        self.bin_path_edit = QtWidgets.QLineEdit()
        self.bin_path_edit.setReadOnly(True)
        self.bin_browse_btn = QtWidgets.QPushButton("Select .bin file...")

        top_layout.addWidget(QtWidgets.QLabel("Selected D-Ark .bin:"))
        top_layout.addWidget(self.bin_path_edit, 1)
        top_layout.addWidget(self.bin_browse_btn)

        main_layout.addWidget(top_box)

        io_box = QtWidgets.QGroupBox("Card Table CSV & In-App Editing")
        io_layout = QtWidgets.QGridLayout(io_box)

        default_csv = os.path.join(
            os.path.expanduser("~"),
            "Desktop",
            "d_ark_card_table.csv",
        )
        self.export_csv_edit = QtWidgets.QLineEdit(default_csv)

        self.export_btn = QtWidgets.QPushButton("Export Card Table to CSV")
        self.export_btn.setStyleSheet(
            "background-color:#0006b1;color:white;font-weight:600;font-size:14pt;"
        )

        self.import_btn = QtWidgets.QPushButton("Import Card Table from CSV")
        self.import_btn.setStyleSheet(
            "background-color:#0006b1;color:white;font-weight:600;font-size:14pt;"
        )

        self.load_table_btn = QtWidgets.QPushButton("Refresh")
        self.load_table_btn.setStyleSheet(
            "background-color:#008000;color:white;font-weight:600;font-size:14pt;"
        )

        self.reset_btn = QtWidgets.QPushButton("Reset to Original ?")
        self.reset_btn.setStyleSheet(
            "background-color:#960202;color:white;font-weight:600;font-size:14pt;"
        )

        self.save_edits_btn = QtWidgets.QPushButton("Save Card Table Edits to BIN")
        self.save_edits_btn.setStyleSheet(
            "background-color:#008000;color:white;font-weight:600;font-size:14pt;"
        )
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
        self.table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        main_layout.addWidget(self.table, 1)

        self.status_label = QtWidgets.QLabel("Ready.")
        self.status_label.setWordWrap(True)
        main_layout.addWidget(self.status_label)

        self.bin_browse_btn.clicked.connect(self.on_select_bin_file)
        self.export_btn.clicked.connect(self.on_export_clicked)
        self.import_btn.clicked.connect(self.on_import_clicked)
        self.load_table_btn.clicked.connect(self.on_load_table_clicked)
        self.save_edits_btn.clicked.connect(self.on_save_edits_clicked)
        self.reset_btn.clicked.connect(self.on_reset_clicked)

    def _short_status(self, msg: str) -> str:
        return msg if len(msg) <= 100 else msg[:97] + "..."

    def require_bin(self) -> bool:
        if not self.current_bin_path or not os.path.isfile(self.current_bin_path):
            QtWidgets.QMessageBox.warning(
                self,
                "BIN required",
                "Please select a valid D-Ark .bin file.",
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Mappings
    # ------------------------------------------------------------------

    def load_simple_map(self, path):
        result = {}

        if not os.path.isfile(path):
            return result

        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                key = str(row.get("key", "")).strip()
                value = str(row.get("value", "")).strip()

                if key:
                    result[key] = value

        return result

    def load_mappings(self):
        effect_path = os.path.join(SCRIPT_DIR, self.EFFECT_MAP_CSV)
        self.effect_map = self.load_simple_map(effect_path)

    def build_name_map_from_bin(self):
        if not self.current_bin_path or not os.path.isfile(self.current_bin_path):
            return {}

        exporter = os.path.join(SCRIPT_DIR, "export_d_ark_names.py")
        replace_map = os.path.join(SCRIPT_DIR, "replace_map.csv")

        if not os.path.isfile(exporter):
            return {}

        if not os.path.isfile(replace_map):
            return {}

        tmp_dir = tempfile.mkdtemp(prefix="d_ark_card_names_")
        tmp_csv = os.path.join(tmp_dir, "d_ark_names_tmp.csv")

        old_argv = sys.argv

        try:
            sys.argv = [
                "export_d_ark_names.py",
                self.current_bin_path,
                replace_map,
                tmp_csv,
            ]

            runpy.run_path(exporter, run_name="__main__")

            mapping = {}

            with open(tmp_csv, encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    si = str(row.get("string_index", "")).strip()
                    name = str(row.get("name", "")).strip()

                    if not si:
                        continue

                    display = f"{name} ({si})" if name else f"(string {si})"
                    mapping[display] = si

            return mapping

        except Exception as exc:
            print(f"[WARN] Failed to build D-Ark name map: {exc}")
            return {}

        finally:
            sys.argv = old_argv
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Widgets
    # ------------------------------------------------------------------

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

        matched_index = None

        for key, value in mapping.items():
            raw = str(value).strip()
            combo.addItem(key, raw)

            if raw == current_value:
                matched_index = combo.count() - 1

        if matched_index is not None:
            combo.setCurrentIndex(matched_index)
        else:
            fallback = f"(current value: {current_value})"
            combo.insertItem(0, fallback, current_value)
            combo.setCurrentIndex(0)

        return combo

    def make_readonly_item(self, value, align_center=False):
        item = QtWidgets.QTableWidgetItem(str(value))
        item.setFlags(
            item.flags()
            & ~QtCore.Qt.ItemIsEditable
        )

        if align_center:
            item.setTextAlignment(QtCore.Qt.AlignCenter)

        return item

    def make_readonly_card_id_item(self, value):
        return self.make_readonly_item(value, align_center=True)

    # ------------------------------------------------------------------
    # BIN selection
    # ------------------------------------------------------------------

    def on_select_bin_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select D-Ark 25th Color .bin file",
            "",
            "BIN files (*.bin);;All files (*)",
        )

        if not path:
            return

        self.current_bin_path = path
        self.bin_path_edit.setText(path)

        self.load_mappings()
        self.name_map = self.build_name_map_from_bin()
        self.on_load_table_clicked()

    # ------------------------------------------------------------------
    # Export / import / refresh
    # ------------------------------------------------------------------

    def on_export_clicked(self):
        if not self.require_bin():
            return

        out_csv = self.export_csv_edit.text().strip()

        if not out_csv:
            QtWidgets.QMessageBox.warning(
                self,
                "CSV path required",
                "Please specify an export CSV path.",
            )
            return

        script_path = os.path.join(SCRIPT_DIR, self.EXPORT_SCRIPT)

        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.critical(
                self,
                "Missing script",
                f"{self.EXPORT_SCRIPT} not found next to this GUI.",
            )
            return

        dlg = BusyDialog(
            "Export Card Table",
            "Please wait...\nExporting D-Ark card table.",
            self,
        )

        worker = InternalScriptWorker(
            script_name=self.EXPORT_SCRIPT,
            script_args=[
                self.current_bin_path,
                out_csv,
            ],
            desc="Export Card Table",
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
                    "Card Table Exported",
                    f"Card table was exported to:\n{out_csv}",
                )

                try:
                    self.populate_table_from_csv(out_csv)
                    self.save_edits_btn.setEnabled(True)
                except Exception as exc:
                    QtWidgets.QMessageBox.warning(
                        self,
                        "Table load warning",
                        f"Export worked, but table load failed:\n{exc}",
                    )
            else:
                QtWidgets.QMessageBox.critical(
                    self,
                    "Export Card Table Error",
                    msg,
                )

        worker.finished.connect(done)
        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()

    def on_import_clicked(self):
        if not self.require_bin():
            return

        in_csv, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select d_ark_card_table.csv",
            "",
            "CSV files (*.csv);;All files (*)",
        )

        if not in_csv:
            return

        self.run_import_script(in_csv, reload_after=True)

    def on_load_table_clicked(self):
        if not self.require_bin():
            return

        tmp_dir = tempfile.mkdtemp(prefix="d_ark_card_table_gui_")
        tmp_csv = os.path.join(tmp_dir, "d_ark_card_table_tmp.csv")

        script_path = os.path.join(SCRIPT_DIR, self.EXPORT_SCRIPT)

        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.critical(
                self,
                "Missing script",
                f"{self.EXPORT_SCRIPT} not found next to this GUI.",
            )
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        dlg = BusyDialog(
            "Refresh",
            "Please wait...\nLoading D-Ark card table.",
            self,
        )

        worker = InternalScriptWorker(
            script_name=self.EXPORT_SCRIPT,
            script_args=[
                self.current_bin_path,
                tmp_csv,
            ],
            desc="Refresh Card Table",
        )

        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        def done(ok, msg):
            dlg.accept()
            thread.quit()
            thread.wait()

            if ok:
                try:
                    self.load_mappings()
                    self.name_map = self.build_name_map_from_bin()
                    self.populate_table_from_csv(tmp_csv)
                    self.save_edits_btn.setEnabled(True)
                    self.status_label.setText("D-Ark card table loaded.")
                except Exception as exc:
                    QtWidgets.QMessageBox.critical(
                        self,
                        "CSV error",
                        f"Failed to refresh Card Table:\n{exc}",
                    )
                    self.status_label.setText("Card table load failed.")
            else:
                self.status_label.setText(self._short_status(msg))
                QtWidgets.QMessageBox.critical(
                    self,
                    "Refresh Error",
                    msg,
                )

            shutil.rmtree(tmp_dir, ignore_errors=True)

        worker.finished.connect(done)
        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()

    # ------------------------------------------------------------------
    # Table
    # ------------------------------------------------------------------

    def populate_table_from_csv(self, csv_path):
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))

        headers = [
            "card_id",
            "which_card",
            "string_index",
            "card_menu_string_index",
            "effect_type_id",
            "search_unlock_value",
            "sprite_index",
            "alternate_sprite_index",
        ]

        pretty = [
            "card_id",
            "Which Card?",
            "Name text",
            "Card Menu Name text",
            "What it does?",
            "search_unlock_value",
            "sprite_index",
            "alternate_sprite_index",
        ]

        self.table.clear()
        self.table.setRowCount(len(rows))
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(pretty)

        self.hidden_rows = {}

        for r_idx, row in enumerate(rows):
            card_id = int(str(row.get("card_id", "0")).strip())

            self.table.setVerticalHeaderItem(
                r_idx,
                QtWidgets.QTableWidgetItem(str(card_id)),
            )

            # Column 0: read-only card ID.
            self.table.setItem(
                r_idx,
                0,
                self.make_readonly_card_id_item(card_id),
            )

            # Column 1: read-only, UI-only card name.
            card_name = CARD_NAMES.get(
                card_id,
                f"Unknown Card {card_id}",
            )
            self.table.setItem(
                r_idx,
                1,
                self.make_readonly_item(card_name),
            )

            self.table.setCellWidget(
                r_idx,
                2,
                self.make_combo(
                    self.name_map,
                    row.get("string_index", ""),
                ),
            )

            self.table.setCellWidget(
                r_idx,
                3,
                self.make_combo(
                    self.name_map,
                    row.get("card_menu_string_index", ""),
                ),
            )

            self.table.setCellWidget(
                r_idx,
                4,
                self.make_combo(
                    self.effect_map,
                    row.get("effect_type_id", ""),
                ),
            )

            self.table.setCellWidget(
                r_idx,
                5,
                self.make_spin(row.get("search_unlock_value", 0)),
            )

            self.table.setCellWidget(
                r_idx,
                6,
                self.make_spin(row.get("sprite_index", 0)),
            )

            self.table.setCellWidget(
                r_idx,
                7,
                self.make_spin(row.get("alternate_sprite_index", 0)),
            )

            self.hidden_rows[r_idx] = {
                "description_string_index": str(
                    row.get("description_string_index", "0")
                ).strip(),
            }

        self.table.resizeColumnsToContents()

        self.table.setColumnWidth(1, 190)
        self.table.setColumnWidth(2, 210)
        self.table.setColumnWidth(3, 210)

        self.table.horizontalHeader().setSectionResizeMode(
            1,
            QtWidgets.QHeaderView.ResizeMode.Fixed,
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2,
            QtWidgets.QHeaderView.ResizeMode.Fixed,
        )
        self.table.horizontalHeader().setSectionResizeMode(
            3,
            QtWidgets.QHeaderView.ResizeMode.Fixed,
        )

    # ------------------------------------------------------------------
    # Save / reset
    # ------------------------------------------------------------------

    def on_save_edits_clicked(self):
        if not self.require_bin():
            return

        if self.table.rowCount() == 0:
            QtWidgets.QMessageBox.information(
                self,
                "No data",
                "There is no D-Ark card table loaded.",
            )
            return

        rows_out = []

        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)

            if item is None:
                QtWidgets.QMessageBox.critical(
                    self,
                    "Card ID error",
                    f"Missing card_id at UI row {r + 1}.",
                )
                return

            card_id = int(item.text())

            # Column 1 ("Which Card?") is UI-only and intentionally ignored.

            hidden = self.hidden_rows.get(r, {})

            row = {
                "card_id": card_id,
                "effect_type_id": self.table.cellWidget(
                    r, 4
                ).currentData(),
                "string_index": self.table.cellWidget(
                    r, 2
                ).currentData(),
                "card_menu_string_index": self.table.cellWidget(
                    r, 3
                ).currentData(),
                "description_string_index": hidden.get(
                    "description_string_index",
                    "0",
                ),
                "search_unlock_value": self.table.cellWidget(
                    r, 5
                ).value(),
                "sprite_index": self.table.cellWidget(
                    r, 6
                ).value(),
                "alternate_sprite_index": self.table.cellWidget(
                    r, 7
                ).value(),
            }

            rows_out.append(row)

        # CSV schema remains unchanged: no "Which Card?" column.
        fieldnames = [
            "card_id",
            "effect_type_id",
            "string_index",
            "card_menu_string_index",
            "description_string_index",
            "search_unlock_value",
            "sprite_index",
            "alternate_sprite_index",
        ]

        tmp_dir = tempfile.mkdtemp(prefix="d_ark_card_table_save_")
        tmp_csv = os.path.join(tmp_dir, "d_ark_card_table_edit.csv")

        try:
            with open(
                tmp_csv,
                "w",
                encoding="utf-8",
                newline="",
            ) as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=fieldnames,
                )
                writer.writeheader()
                writer.writerows(rows_out)

        except Exception as exc:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            QtWidgets.QMessageBox.critical(
                self,
                "CSV error",
                f"Failed to write temp CSV:\n{exc}",
            )
            return

        self.run_import_script(
            tmp_csv,
            reload_after=True,
            cleanup_dir=tmp_dir,
        )

    def on_reset_clicked(self):
        if not self.require_bin():
            return

        original_csv = os.path.join(
            SCRIPT_DIR,
            self.ORIGINAL_CSV,
        )

        if not os.path.isfile(original_csv):
            QtWidgets.QMessageBox.critical(
                self,
                "Missing file",
                f"{self.ORIGINAL_CSV} not found next to this GUI.",
            )
            return

        res = QtWidgets.QMessageBox.warning(
            self,
            "Reset Card Table to Original?",
            (
                "This will restore the editable Card Table fields to the "
                "original D-Ark values.\n\n"
                "Game progress/save data will not be changed.\n\n"
                "Continue?"
            ),
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
        )

        if res != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        self.run_import_script(
            original_csv,
            reload_after=True,
        )

    def run_import_script(
        self,
        csv_path,
        reload_after=False,
        cleanup_dir=None,
    ):
        script_path = os.path.join(
            SCRIPT_DIR,
            self.IMPORT_SCRIPT,
        )

        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.critical(
                self,
                "Missing script",
                f"{self.IMPORT_SCRIPT} not found next to this GUI.",
            )

            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)

            return

        dlg = BusyDialog(
            "Import Card Table",
            "Please wait...\nApplying D-Ark card table changes to BIN.",
            self,
        )

        worker = InternalScriptWorker(
            script_name=self.IMPORT_SCRIPT,
            script_args=[
                self.current_bin_path,
                csv_path,
                self.current_bin_path,
            ],
            desc="Import Card Table",
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
                    "Card Table Imported",
                    msg,
                )

                if reload_after:
                    self.on_load_table_clicked()
            else:
                QtWidgets.QMessageBox.critical(
                    self,
                    "Card Table Import Error",
                    msg,
                )

            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)

        worker.finished.connect(done)
        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()
