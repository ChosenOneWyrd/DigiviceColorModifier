from PyQt5 import QtCore, QtWidgets
import os
import shutil
import tempfile
import sys
import csv
import runpy
from typing import Optional

from common import *

# ----------------- Partner Table tab -----------------

class PartnerTableTab(QtWidgets.QWidget):
    """
    Partner Table tab for D-3.
    Uses:
        export_d3_partner_table.py
        import_d3_partner_table.py
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self.current_bin_type_key: Optional[str] = None
        self.current_bin_path: Optional[str] = None

        self.name_map = {}
        self.sprite_map = {}
        self.jogress_map = {}
        self.evo_map = {}
        self.bgm_map = {}
        self.voice_map = {}
        self.shot_sound_map = {}
        self.partner_hidden_rows = []
        self.partner_ui_to_csv_index = {}

        self._build_ui()
        self.load_mappings()

    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)

        # BIN selection
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

        # CSV controls
        io_box = QtWidgets.QGroupBox("Partner Table CSV & In-App Editing")
        io_layout = QtWidgets.QGridLayout(io_box)

        default_csv = os.path.join(
            os.path.expanduser("~"),
            "Desktop",
            "partner_table.csv"
        )
        self.export_csv_edit = QtWidgets.QLineEdit(default_csv)

        self.export_btn = QtWidgets.QPushButton("Export Partner Table to CSV")
        self.export_btn.setStyleSheet("background-color:#0006b1;color:white;font-weight:600;font-size:14pt;")

        self.import_btn = QtWidgets.QPushButton("Import Partner Table from CSV")
        self.import_btn.setStyleSheet("background-color:#0006b1;color:white;font-weight:600;font-size:14pt;")

        self.load_table_btn = QtWidgets.QPushButton("Refresh")
        self.load_table_btn.setStyleSheet("background-color:#008000;color:white;font-weight:600;font-size:14pt;")

        self.reset_btn = QtWidgets.QPushButton("Reset to Original ?")
        self.reset_btn.setStyleSheet("background-color:#960202;color:white;font-weight:600;font-size:14pt;")

        self.save_edits_btn = QtWidgets.QPushButton("Save Partner Table Edits to BIN")
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
        self.save_edits_btn.clicked.connect(self.on_save_edits_clicked)
        self.reset_btn.clicked.connect(self.on_reset_clicked)

    def _short_status(self, msg: str) -> str:
        return msg if len(msg) <= 100 else msg[:97] + "..."
    
    def build_digivice_name_map_from_bin(self):
        if not self.current_bin_path or not os.path.isfile(self.current_bin_path):
            return {}

        tmp_dir = tempfile.mkdtemp(prefix="digivice_names_")
        tmp_csv = os.path.join(tmp_dir, "digivice_names_tmp.csv")

        script = os.path.join(
            SCRIPT_DIR,
            "export_digivice_names.py"
        )

        replace_map = os.path.join(
            SCRIPT_DIR,
            "replace_map.csv"
        )

        try:
            old_argv = sys.argv

            sys.argv = [
                "export_digivice_names.py",
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

        finally:
            sys.argv = old_argv
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def is_digivice(self):
        return self.current_bin_type_key == "Digivice"

    def export_script_name(self):
        return (
            "export_digivice_partner_table.py"
            if self.is_digivice()
            else "export_d3_partner_table.py"
        )

    def import_script_name(self):
        return (
            "import_digivice_partner_table.py"
            if self.is_digivice()
            else "import_d3_partner_table.py"
        )

    def partner_original_csv(self):
        return os.path.join(
            SCRIPT_DIR,
            (
                "digivice_partner_table_original.csv"
                if self.is_digivice()
                else "d3_partner_table_original.csv"
            ),
        )
    
    def get_partner_ui_order(self, row_count):
        # User-facing row numbers are 1-based.
        desired_1_based = (
            list(range(1, 11)) +
            [34, 35] +
            list(range(24, 29)) +
            list(range(11, 17)) +
            list(range(29, 34)) +
            list(range(17, 24)) +
            list(range(36, 39))
        )

        order = []
        seen = set()

        for n in desired_1_based:
            idx = n - 1
            if 0 <= idx < row_count and idx not in seen:
                order.append(idx)
                seen.add(idx)

        # Keep any extra rows after 38 in original order.
        for idx in range(row_count):
            if idx not in seen:
                order.append(idx)

        return order
    
    def build_name_map_from_bin(self):
        """
        Builds mapping: display_name -> string_index
        directly from D3.bin using export_d3_names.py
        """
        if not self.current_bin_path or not os.path.isfile(self.current_bin_path):
            return {}

        tmp_dir = tempfile.mkdtemp(prefix="names_map_")
        tmp_csv = os.path.join(tmp_dir, "names_tmp.csv")

        script = os.path.join(SCRIPT_DIR, "export_d3_names.py")
        replace_map = os.path.join(SCRIPT_DIR, "replace_map.csv")

        if not os.path.isfile(script):
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return {}

        try:
            # Run exporter internally
            old_argv = sys.argv
            sys.argv = [
                "export_d3_names.py",
                self.current_bin_path,
                replace_map,
                tmp_csv,
            ]

            runpy.run_path(script, run_name="__main__")

            # Build mapping
            mapping = {}
            with open(tmp_csv, encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    si = str(row.get("string_index", "")).strip()
                    name = str(row.get("name", "")).strip()
                    if si and name:
                        mapping[f"{name} ({si})"] = si

            return mapping

        except Exception as e:
            print(f"[WARN] Failed to build name map from BIN: {e}")
            return {}

        finally:
            sys.argv = old_argv
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def on_bin_type_changed(self, index: int):
        if index <= 0:
            self.current_bin_type_key = None
        else:
            self.current_bin_type_key = self.bin_type_combo.itemData(index)
        if self.current_bin_type_key == "Digivice":
            self.export_csv_edit.setText(
                os.path.join(
                    os.path.expanduser("~"),
                    "Desktop",
                    "digivice_partner_table.csv"
                )
            )
        elif self.current_bin_type_key == "D-3":
            self.export_csv_edit.setText(
                os.path.join(
                    os.path.expanduser("~"),
                    "Desktop",
                    "d3_partner_table.csv"
                )
            )

    def require_all(self) -> bool:
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

        self.load_mappings()

        if self.is_digivice():
            self.name_map = self.build_digivice_name_map_from_bin()
        else:
            self.name_map = self.build_name_map_from_bin()

        self.on_load_table_clicked()

    # ---------------- mappings ----------------

    def load_mappings(self):

        def csv_path(name):
            return os.path.join(SCRIPT_DIR, name)

        if self.is_digivice():

            self.sprite_map = self.load_simple_map(
                csv_path("digivice_sprite_map.csv")
            )

            self.jogress_map = self.load_simple_map(
                csv_path("digivice_jogress_win_partner_id_map.csv")
            )

            self.evo_map = self.load_simple_map(
                csv_path("digivice_evo_animation_map.csv")
            )

            self.voice_map = self.load_simple_map(
                csv_path("digivice_attack_voice_sound_id_map.csv")
            )

            self.shot_sound_map = self.load_simple_map(
                csv_path("digivice_attack_shot_sound_id_map.csv")
            )

        else:

            self.sprite_map = self.load_simple_map(
                csv_path("d3_sprite_map.csv")
            )

            self.jogress_map = self.load_simple_map(
                csv_path("d3_jogress_win_partner_id_map.csv")
            )

            self.evo_map = self.load_simple_map(
                csv_path("d3_evo_animation_map.csv")
            )

            self.bgm_map = self.load_simple_map(
                csv_path("d3_background_music_during_battle_id_map.csv")
            )

            self.voice_map = self.load_simple_map(
                csv_path("d3_attack_voice_sound_id_map.csv")
            )

            self.shot_sound_map = self.load_simple_map(
                csv_path("d3_attack_shot_sound_id_map.csv")
            )

    def load_name_map(self, path):
        """
        Returns display_name -> string_index.
        d3_names_original.csv columns:
            string_index,name
        """
        m = {}
        if not os.path.isfile(path):
            return m

        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                si = str(row.get("string_index", "")).strip()
                name = str(row.get("name", "")).strip()
                if si != "" and name != "":
                    m[name] = si
        return m

    def load_simple_map(self, path):
        """
        Expected columns:
            key,value
        """
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

    # ---------------- helper widgets ----------------

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

    def make_sprite_combo(self, row):
        combo = NoWheelComboBox()

        cur_j = str(row["jogress_win_partner_id"]).strip()
        cur_s = str(row["sprite_index"]).strip()
        cur_u = str(row["special_unlock"]).strip()
        current_tuple = f"{cur_j}|{cur_s}|{cur_u}"

        matched = False

        for key, value in self.sprite_map.items():
            parts = [p.strip() for p in str(value).split("|")]
            if len(parts) != 3:
                continue

            normalized = "|".join(parts)
            combo.addItem(key, normalized)

            if normalized == current_tuple:
                combo.setCurrentText(key)
                matched = True

        if not matched:
            fallback = f"(current values: {current_tuple})"
            combo.insertItem(0, fallback, current_tuple)
            combo.setCurrentIndex(0)

        return combo

    # ---------------- export/import/load ----------------

    def on_export_clicked(self):
        if not self.require_all():
            return

        out_csv = self.export_csv_edit.text().strip()
        if not out_csv:
            QtWidgets.QMessageBox.warning(self, "CSV path required", "Please specify an export CSV path.")
            return

        script = self.export_script_name()
        script_path = os.path.join(SCRIPT_DIR, script)
        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.critical(self, "Missing script", f"{script} not found next to this GUI.")
            return

        dlg = BusyDialog("Export Partner Table", "Please wait...\nExporting partner table.", self)

        worker = InternalScriptWorker(
            script_name=script,
            script_args=[
                self.current_bin_path,
                out_csv,
            ],
            desc="Export Partner Table",
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
                    "Partner Table Exported",
                    f"Partner table was exported to {out_csv} on your Desktop.",
                )
                try:
                    self.populate_table_from_csv(out_csv)
                    self.save_edits_btn.setEnabled(True)
                except Exception as e:
                    QtWidgets.QMessageBox.warning(
                        self,
                        "Table load warning",
                        f"Export worked, but table load failed:\n{e}",
                    )
            else:
                QtWidgets.QMessageBox.critical(self, "Export Partner Table Error", msg)

        worker.finished.connect(done)
        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()

    def on_import_clicked(self):
        if not self.require_all():
            return

        in_csv, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select partner_table.csv",
            "",
            "CSV files (*.csv);;All files (*)",
        )
        if not in_csv:
            return

        self.run_import_script(in_csv, reload_after=True)

    def on_load_table_clicked(self):
        if not self.require_all():
            return

        tmp_dir = tempfile.mkdtemp(prefix="partner_table_gui_")
        tmp_csv = os.path.join(tmp_dir,"partner_table_tmp.csv")

        script = self.export_script_name()
        script_path = os.path.join(SCRIPT_DIR, script)
        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.critical(self, "Missing script", f"{script} not found next to this GUI.")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        dlg = BusyDialog("Refresh", "Please wait...\nLoading partner table from D3.bin.", self)

        worker = InternalScriptWorker(
            script_name=script,
            script_args=[
                self.current_bin_path,
                tmp_csv,
            ],
            desc="Refresh",
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
                    if self.is_digivice():
                        self.name_map = self.build_digivice_name_map_from_bin()
                    else:
                        self.name_map = self.build_name_map_from_bin()
                    self.populate_table_from_csv(tmp_csv)
                    self.save_edits_btn.setEnabled(True)
                    self.status_label.setText("Partner table loaded.")
                except Exception as e:
                    QtWidgets.QMessageBox.critical(self, "CSV error", f"Failed to Refresh:\n{e}")
                    self.status_label.setText("Partner table load failed.")
            else:
                self.status_label.setText(self._short_status(msg))
                QtWidgets.QMessageBox.critical(self, "Refresh Error", msg)

            shutil.rmtree(tmp_dir, ignore_errors=True)

        worker.finished.connect(done)
        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()

    # ---------------- table population ----------------

    def populate_table_from_csv(self, csv_path):

        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))

        if self.is_digivice():
            self.populate_digivice_table(rows)
            return

        display_order = self.get_partner_ui_order(len(rows))
        display_rows = [rows[i] for i in display_order]

        headers = [
            "digimon_id",
            "string_index",
            "stage",
            "jogress_win_partner_id",
            "sprite_index",
            "win_requirement_for_next_evo",
            "evo_animation1_id",
            "evo_animation2_id",
            "evo_animation3_id",
            "evo_animation4_id",
            "evo_animation5_id",
            "background_music_during_battle_id",
            "attack_voice_sound_id",
            "attack_shot_sprite_index",
            "attack_shot_sound_id",
        ]

        pretty = [
            "digimon_id",
            "Name",
            "stage",
            "slot_type",
            "sprite_index",
            "wins_to_evo",
            "evo_animation1_id",
            "evo_animation2_id",
            "evo_animation3_id",
            "evo_animation4_id",
            "evo_animation5_id",
            "background_music_during_battle_id",
            "attack_voice_sound_id",
            "attack_shot_sprite_index",
            "attack_shot_sound_id",
        ]

        self.table.clear()
        self.table.setRowCount(len(display_rows))
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(pretty)
        self.partner_hidden_rows = {}

        self.partner_ui_to_csv_index = {}

        for r_idx, row in enumerate(display_rows):
            csv_idx = display_order[r_idx]
            self.partner_ui_to_csv_index[r_idx] = csv_idx
            # Show original slot number in the row header, not UI row order
            self.table.setVerticalHeaderItem(
                r_idx,
                QtWidgets.QTableWidgetItem(str(csv_idx + 1))
            )
            self.table.setCellWidget(
                r_idx,
                0,
                self.make_spin(row.get("digimon_id", 0)),
            )

            self.partner_hidden_rows[r_idx] = {
                "special_unlock": str(row.get("special_unlock", "0")),
            }

            # name / string_index dropdown
            self.table.setCellWidget(
                r_idx,
                1,
                self.make_combo(self.name_map, row.get("string_index", "")),
            )

            self.table.setCellWidget(r_idx, 2, self.make_spin(row.get("stage", 0)))

            self.table.setCellWidget(
                r_idx,
                3,
                self.make_combo(self.jogress_map, row.get("jogress_win_partner_id", "")),
            )

            self.table.setCellWidget(
                r_idx,
                4,
                self.make_combo(self.sprite_map, row.get("sprite_index", "")),
            )

            self.table.setCellWidget(
                r_idx,
                5,
                self.make_spin(row.get("win_requirement_for_next_evo", 0)),
            )

            for i in range(5):
                key = f"evo_animation{i + 1}_id"
                self.table.setCellWidget(
                    r_idx,
                    6 + i,
                    self.make_combo(self.evo_map, row.get(key, "")),
                )

            self.table.setCellWidget(
                r_idx,
                11,
                self.make_combo(self.bgm_map, row.get("background_music_during_battle_id", "")),
            )

            self.table.setCellWidget(
                r_idx,
                12,
                self.make_combo(self.voice_map, row.get("attack_voice_sound_id", "")),
            )

            self.table.setCellWidget(
                r_idx,
                13,
                self.make_spin(row.get("attack_shot_sprite_index", 0)),
            )

            self.table.setCellWidget(
                r_idx,
                14,
                self.make_combo(self.shot_sound_map, row.get("attack_shot_sound_id", "")),
            )

        self.table.resizeColumnsToContents()

        # Keep Name column smaller
        self.table.setColumnWidth(1, 160)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.Fixed
        )

    def populate_digivice_table(self, rows):
        headers = [
            "digimon_id",
            "string_index",
            "stage",
            "jogress_win_partner_id",
            "sprite_index",
            "win_requirement_for_next_evo",
            "evo_animation1_id",
            "evo_animation2_id",
            "attack_voice_sound_id",
            "attack_shot_sprite_index",
            "attack_shot_sound_id",
            "attack_led_color_id",
            "unknown_column",
        ]

        pretty = [
            "digimon_id",
            "Name",
            "stage",
            "slot_type",
            "sprite_index",
            "wins_to_evo",
            "evo_animation1_id",
            "evo_animation2_id",
            "attack_voice_sound_id",
            "attack_shot_sprite_index",
            "attack_shot_sound_id",
            "attack_led_color_id",
            "unknown_column",
        ]

        self.table.clear()
        self.table.setRowCount(len(rows))
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(pretty)

        self.partner_ui_to_csv_index = {}

        for r_idx, row in enumerate(rows):
            self.partner_ui_to_csv_index[r_idx] = r_idx

            self.table.setVerticalHeaderItem(
                r_idx,
                QtWidgets.QTableWidgetItem(str(r_idx + 1))
            )

            self.table.setCellWidget(r_idx, 0, self.make_spin(row.get("digimon_id", 0)))

            self.table.setCellWidget(
                r_idx,
                1,
                self.make_combo(self.name_map, row.get("string_index", "")),
            )

            self.table.setCellWidget(r_idx, 2, self.make_spin(row.get("stage", 0)))

            self.table.setCellWidget(
                r_idx,
                3,
                self.make_combo(self.jogress_map, row.get("jogress_win_partner_id", "")),
            )

            self.table.setCellWidget(
                r_idx,
                4,
                self.make_combo(self.sprite_map, row.get("sprite_index", "")),
            )

            self.table.setCellWidget(
                r_idx,
                5,
                self.make_spin(row.get("win_requirement_for_next_evo", 0)),
            )

            self.table.setCellWidget(
                r_idx,
                6,
                self.make_combo(self.evo_map, row.get("evo_animation1_id", "")),
            )

            self.table.setCellWidget(
                r_idx,
                7,
                self.make_combo(self.evo_map, row.get("evo_animation2_id", "")),
            )

            self.table.setCellWidget(
                r_idx,
                8,
                self.make_combo(self.voice_map, row.get("attack_voice_sound_id", "")),
            )

            self.table.setCellWidget(
                r_idx,
                9,
                self.make_spin(row.get("attack_shot_sprite_index", 0)),
            )

            self.table.setCellWidget(
                r_idx,
                10,
                self.make_combo(self.shot_sound_map, row.get("attack_shot_sound_id", "")),
            )

            self.table.setCellWidget(
                r_idx,
                11,
                self.make_spin(row.get("attack_led_color_id", 0)),
            )

            self.table.setCellWidget(
                r_idx,
                12,
                self.make_spin(row.get("unknown_column", 0)),
            )

        self.table.resizeColumnsToContents()

        self.table.setColumnWidth(1, 160)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.Fixed
        )

    # ---------------- save/import ----------------
    def save_digivice_partner_table(self):
        if self.table.rowCount() == 0:
            QtWidgets.QMessageBox.information(
                self,
                "No data",
                "There is no Digivice partner table loaded."
            )
            return

        rows_out = []

        rows_by_csv_index = {}

        for r in range(self.table.rowCount()):

            row = {}

            row["offset"] = ""

            row["digimon_id"] = self.table.cellWidget(r, 0).value()

            row["string_index"] = self.table.cellWidget(
                r,
                1
            ).currentData()

            row["stage"] = self.table.cellWidget(r, 2).value()

            row["jogress_win_partner_id"] = self.table.cellWidget(
                r,
                3
            ).currentData()

            row["sprite_index"] = self.table.cellWidget(
                r,
                4
            ).currentData()

            row["win_requirement_for_next_evo"] = self.table.cellWidget(
                r,
                5
            ).value()

            row["evo_animation1_id"] = self.table.cellWidget(
                r,
                6
            ).currentData()

            row["evo_animation2_id"] = self.table.cellWidget(
                r,
                7
            ).currentData()

            row["attack_voice_sound_id"] = self.table.cellWidget(
                r,
                8
            ).currentData()

            row["attack_shot_sprite_index"] = self.table.cellWidget(
                r,
                9
            ).value()

            row["attack_shot_sound_id"] = self.table.cellWidget(
                r,
                10
            ).currentData()

            row["attack_led_color_id"] = self.table.cellWidget(
                r,
                11
            ).value()

            row["unknown_column"] = self.table.cellWidget(
                r,
                12
            ).value()

            csv_idx = self.partner_ui_to_csv_index.get(r, r)

            rows_by_csv_index[csv_idx] = row

        rows_out = [
            rows_by_csv_index[i]
            for i in sorted(rows_by_csv_index.keys())
        ]

        fieldnames = [
            "offset",
            "stage",
            "digimon_id",
            "jogress_win_partner_id",
            "win_requirement_for_next_evo",
            "sprite_index",
            "string_index",
            "evo_animation1_id",
            "evo_animation2_id",
            "attack_voice_sound_id",
            "attack_shot_sprite_index",
            "attack_shot_sound_id",
            "attack_led_color_id",
            "unknown_column",
        ]

        tmp_dir = tempfile.mkdtemp(
            prefix="digivice_partner_table_save_"
        )

        tmp_csv = os.path.join(
            tmp_dir,
            "digivice_partner_table_edit.csv"
        )

        try:

            with open(
                tmp_csv,
                "w",
                encoding="utf-8-sig",
                newline=""
            ) as f:

                writer = csv.DictWriter(
                    f,
                    fieldnames=fieldnames
                )

                writer.writeheader()
                writer.writerows(rows_out)

        except Exception as e:

            shutil.rmtree(
                tmp_dir,
                ignore_errors=True
            )

            QtWidgets.QMessageBox.critical(
                self,
                "CSV error",
                f"Failed to write temp CSV:\n{e}"
            )

            return

        self.run_import_script(
            tmp_csv,
            reload_after=True,
            cleanup_dir=tmp_dir
        )

    def on_save_edits_clicked(self):
        if self.is_digivice():
            self.save_digivice_partner_table()
            return
        if not self.require_all():
            return

        if self.table.rowCount() == 0:
            QtWidgets.QMessageBox.information(self, "No data", "There is no partner table loaded.")
            return

        rows_out = []

        rows_by_csv_index = {}
        for r in range(self.table.rowCount()):
            row = {}

            row["meta_offset"] = ""
            row["data_offset"] = ""

            row["digimon_id"] = self.table.cellWidget(r, 0).value()
            row["string_index"] = self.table.cellWidget(r, 1).currentData()
            row["stage"] = self.table.cellWidget(r, 2).value()

            hidden = self.partner_hidden_rows.get(r, {})
            row["jogress_win_partner_id"] = self.table.cellWidget(r, 3).currentData()
            row["sprite_index"] = self.table.cellWidget(r, 4).currentData()
            row["special_unlock"] = hidden.get("special_unlock", "0")

            row["win_requirement_for_next_evo"] = self.table.cellWidget(r, 5).value()

            for i in range(5):
                row[f"evo_animation{i + 1}_id"] = self.table.cellWidget(r, 6 + i).currentData()

            row["background_music_during_battle_id"] = self.table.cellWidget(r, 11).currentData()
            row["attack_voice_sound_id"] = self.table.cellWidget(r, 12).currentData()
            row["attack_shot_sprite_index"] = self.table.cellWidget(r, 13).value()
            row["attack_shot_sound_id"] = self.table.cellWidget(r, 14).currentData()

            csv_idx = self.partner_ui_to_csv_index.get(r, r)
            rows_by_csv_index[csv_idx] = row
        
        rows_out = [
            rows_by_csv_index[i]
            for i in sorted(rows_by_csv_index.keys())
        ]

        fieldnames = [
            "meta_offset",
            "data_offset",
            "stage",
            "digimon_id",
            "jogress_win_partner_id",
            "win_requirement_for_next_evo",
            "sprite_index",
            "string_index",
            "evo_animation1_id",
            "evo_animation2_id",
            "evo_animation3_id",
            "evo_animation4_id",
            "evo_animation5_id",
            "background_music_during_battle_id",
            "attack_voice_sound_id",
            "attack_shot_sprite_index",
            "attack_shot_sound_id",
            "special_unlock",
        ]

        tmp_dir = tempfile.mkdtemp(prefix="d3_partner_table_save_")
        tmp_csv = os.path.join(tmp_dir, "d3_partner_table_edit.csv")

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

    def on_reset_clicked(self):
        if not self.require_all():
            return

        original_csv = self.partner_original_csv()

        if not os.path.isfile(original_csv):
            QtWidgets.QMessageBox.critical(
                self,
                "Missing file",
                "d3_partner_table_original.csv not found next to this GUI."
            )
            return

        res = QtWidgets.QMessageBox.warning(
            self,
            "Reset to Original?",
            (
                "This will overwrite ALL partner table data in the BIN\n"
                "with the original .bin file values.\n\n"
                "You will not lose game progress. But partner table modding changes will be lost.\n\n"
                "Continue?"
            ),
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )

        if res != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        # Reuse existing import pipeline
        self.run_import_script(original_csv, reload_after=True)

    def run_import_script(self, csv_path, reload_after=False, cleanup_dir=None):
        script = self.import_script_name()
        script_path = os.path.join(SCRIPT_DIR, script)

        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.critical(self, "Missing script", f"{script} not found next to this GUI.")
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
            return

        dlg = BusyDialog("Import Partner Table", "Please wait...\nApplying partner table changes to BIN.", self)

        worker = InternalScriptWorker(
            script_name=script,
            script_args=[
                self.current_bin_path,
                csv_path,
                self.current_bin_path,
            ],
            desc="Import Partner Table",
        )

        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        def done(ok, msg):
            dlg.accept()
            thread.quit()
            thread.wait()

            self.status_label.setText(self._short_status(msg))

            if ok:
                QtWidgets.QMessageBox.information(self, "Partner Table Imported", msg)
                if reload_after:
                    self.on_load_table_clicked()
            else:
                QtWidgets.QMessageBox.critical(self, "Partner Table Import Error", msg)

            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)

        worker.finished.connect(done)
        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()