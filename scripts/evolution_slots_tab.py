#!/usr/bin/env python3
"""
Evolution Slots tab for Digimon BIN Tool.

Supported:
    D-3 25th Color
    D-Ark 25th Color

The UI edits complete evolution-line membership/order using the existing
validated CSV backends:

D-3:
    export_d3_evolution_slots.py
    import_d3_evolution_slots.py
    d3_evolution_slots_original.csv

D-Ark:
    export_d_ark_evolution_slots.py
    import_d_ark_evolution_slots.py
    d_ark_evolution_slots_original.csv

Slot cells are dropdowns:
    display text = current Digimon name from the selected BIN's Partner Table
    stored value = numeric digimon_id
    "-"          = blank / FFFF

Names are built dynamically by joining:
    Partner Table digimon_id -> string_index
with:
    Names table string_index -> decoded name
"""

from PyQt5 import QtCore, QtGui, QtWidgets

import contextlib
import csv
import io
import os
import runpy
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Optional

from common import *


SLOT_COLUMNS = [f"slot_{i}" for i in range(1, 11)]

D3_LINE_NAMES = {
    0: "Vmon Line",
    1: "Wormmon Line",
    2: "Hawkmon Line",
    3: "Tailmon Line",
    4: "Armadimon Line",
    5: "Patamon Line",
    6: "Terriermon Line",
}

D3_SHARED_ID_LINES = {
    6: {0, 1},
    7: {0, 1},
    8: {0, 1},
    9: {0, 1},
    13: {2, 3},
    20: {4, 5},
}

DARK_LINE_NAMES = {
    0: "Guilmon",
    1: "Terriermon",
    2: "Renamon",
    3: "Impmon",
    4: "Cyberdramon",
}

DARK_LINE_OFFSETS = {
    0: "0x000D7584",
    1: "0x000D7598",
    2: "0x000D75AC",
    3: "0x000D75C0",
    4: "0x000D75D4",
}


class EvolutionSlotsTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.current_bin_type_key: Optional[str] = None
        self.current_bin_path: Optional[str] = None

        # digimon_id -> display name from current Partner Table/string table
        self.digimon_names = {}

        # Per-row fields not shown in editable widgets, e.g. D-Ark line_offset.
        self.hidden_rows = {}

        self._populating = False
        self._table_loaded = False

        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)

        # BIN Selection -- same general layout as Partner Table.
        top_box = QtWidgets.QGroupBox("BIN Selection")
        top_layout = QtWidgets.QHBoxLayout(top_box)

        self.bin_type_combo = NoWheelComboBox()
        self.bin_type_combo.addItem("Select BIN type...")

        for key in ("D-3", "D-ark"):
            info = BIN_TYPES.get(key)
            self.bin_type_combo.addItem(info["label"] if info else key, key)

        self.bin_path_edit = QtWidgets.QLineEdit()
        self.bin_path_edit.setReadOnly(True)
        self.bin_browse_btn = QtWidgets.QPushButton("Select .bin file...")

        top_layout.addWidget(QtWidgets.QLabel("Type of .bin file:"))
        top_layout.addWidget(self.bin_type_combo)
        top_layout.addSpacing(20)
        top_layout.addWidget(QtWidgets.QLabel("Selected .bin:"))
        top_layout.addWidget(self.bin_path_edit, 1)
        top_layout.addWidget(self.bin_browse_btn)

        main_layout.addWidget(top_box)

        # CSV / editing controls.
        io_box = QtWidgets.QGroupBox("Evolution Slots CSV & In-App Editing")
        io_layout = QtWidgets.QGridLayout(io_box)

        self.export_csv_edit = QtWidgets.QLineEdit(
            os.path.join(os.path.expanduser("~"), "Desktop", "evolution_slots.csv")
        )

        self.export_btn = QtWidgets.QPushButton("Export Evolution Slots to CSV")
        self.export_btn.setStyleSheet(
            "background-color:#0006b1;color:white;font-weight:600;font-size:14pt;"
        )

        self.import_btn = QtWidgets.QPushButton("Import Evolution Slots from CSV")
        self.import_btn.setStyleSheet(
            "background-color:#0006b1;color:white;font-weight:600;font-size:14pt;"
        )

        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.refresh_btn.setStyleSheet(
            "background-color:#008000;color:white;font-weight:600;font-size:14pt;"
        )

        self.reset_btn = QtWidgets.QPushButton("Reset to Original ?")
        self.reset_btn.setStyleSheet(
            "background-color:#960202;color:white;font-weight:600;font-size:14pt;"
        )

        self.save_edits_btn = QtWidgets.QPushButton(
            "Save Evolution Slot Edits to BIN"
        )
        self.save_edits_btn.setStyleSheet(
            "background-color:#008000;color:white;font-weight:600;font-size:14pt;"
        )
        self.save_edits_btn.setEnabled(False)

        io_layout.addWidget(QtWidgets.QLabel("Export CSV path:"), 0, 0)
        io_layout.addWidget(self.export_csv_edit, 0, 1)
        io_layout.addWidget(self.export_btn, 0, 2)

        io_layout.addWidget(self.refresh_btn, 1, 0)
        io_layout.addWidget(self.reset_btn, 1, 1)
        io_layout.addWidget(self.save_edits_btn, 1, 2)
        io_layout.addWidget(self.import_btn, 1, 3)

        main_layout.addWidget(io_box)

        hint = QtWidgets.QLabel(
            "Each slot dropdown shows the current Digimon name from the selected "
            "BIN's Partner Table, but stores/writes the numeric digimon_id. "
            "Choose '-' for a blank slot. Non-empty slots must remain contiguous "
            "from slot_1."
        )
        hint.setWordWrap(True)
        main_layout.addWidget(hint)

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

        self.bin_type_combo.currentIndexChanged.connect(self.on_bin_type_changed)
        self.bin_browse_btn.clicked.connect(self.on_select_bin_file)
        self.export_btn.clicked.connect(self.on_export_clicked)
        self.import_btn.clicked.connect(self.on_import_clicked)
        self.refresh_btn.clicked.connect(self.on_refresh_clicked)
        self.save_edits_btn.clicked.connect(self.on_save_edits_clicked)
        self.reset_btn.clicked.connect(self.on_reset_clicked)

    # ------------------------------------------------------------------
    # Device / file helpers
    # ------------------------------------------------------------------

    def is_d3(self):
        return self.current_bin_type_key == "D-3"

    def is_d_ark(self):
        return self.current_bin_type_key == "D-ark"

    def export_script_name(self):
        return (
            "export_d3_evolution_slots.py"
            if self.is_d3()
            else "export_d_ark_evolution_slots.py"
        )

    def import_script_name(self):
        return (
            "import_d3_evolution_slots.py"
            if self.is_d3()
            else "import_d_ark_evolution_slots.py"
        )

    def partner_export_script_name(self):
        return (
            "export_d3_partner_table.py"
            if self.is_d3()
            else "export_d_ark_partner_table.py"
        )

    def names_export_script_name(self):
        return "export_d3_names.py" if self.is_d3() else "export_d_ark_names.py"

    def original_csv_path(self):
        name = (
            "d3_evolution_slots_original.csv"
            if self.is_d3()
            else "d_ark_evolution_slots_original.csv"
        )
        return os.path.join(SCRIPT_DIR, name)

    def default_export_csv_path(self):
        if self.is_d3():
            filename = "d3_evolution_slots.csv"
        elif self.is_d_ark():
            filename = "d_ark_evolution_slots.csv"
        else:
            filename = "evolution_slots.csv"

        return os.path.join(os.path.expanduser("~"), "Desktop", filename)

    def expected_partner_ids(self):
        return range(38) if self.is_d3() else range(21)

    def expected_line_count(self):
        return 7 if self.is_d3() else 5

    def require_all(self):
        if not (self.is_d3() or self.is_d_ark()):
            QtWidgets.QMessageBox.warning(
                self,
                "Type required",
                "Please select D-3 or D-Ark BIN type first.",
            )
            return False

        if not self.current_bin_path or not os.path.isfile(self.current_bin_path):
            QtWidgets.QMessageBox.warning(
                self,
                "BIN required",
                "Please select a valid .bin file.",
            )
            return False

        return True

    def _short_status(self, msg):
        msg = str(msg)
        return msg if len(msg) <= 120 else msg[:117] + "..."

    def on_bin_type_changed(self, index):
        self.current_bin_type_key = (
            None if index <= 0 else self.bin_type_combo.itemData(index)
        )

        self.current_bin_path = None
        self.bin_path_edit.clear()
        self.export_csv_edit.setText(self.default_export_csv_path())

        self.digimon_names = {}
        self.hidden_rows = {}
        self._table_loaded = False
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self.save_edits_btn.setEnabled(False)
        self.status_label.setText("Select a BIN to load evolution slots.")

    def on_select_bin_file(self):
        if not (self.is_d3() or self.is_d_ark()):
            QtWidgets.QMessageBox.warning(
                self,
                "Type required",
                "Please select D-3 or D-Ark BIN type first.",
            )
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
        self.on_refresh_clicked()

    # ------------------------------------------------------------------
    # Internal script helper used only for building the name dropdown map
    # ------------------------------------------------------------------

    def _run_script_sync(self, script_name, script_args):
        script_path = os.path.join(SCRIPT_DIR, script_name)
        if not os.path.isfile(script_path):
            raise RuntimeError(f"{script_name} not found in the scripts folder.")

        old_argv = sys.argv
        output = io.StringIO()

        try:
            sys.argv = [script_name] + [str(x) for x in script_args]
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                try:
                    runpy.run_path(script_path, run_name="__main__")
                except SystemExit as exc:
                    code = exc.code
                    if code not in (None, 0):
                        raise RuntimeError(
                            f"{script_name} exited with code {code}.\n"
                            + output.getvalue()
                        )
        finally:
            sys.argv = old_argv

        return output.getvalue()

    def build_digimon_name_map_from_bin(self):
        """
        Build:
            digimon_id -> current decoded partner name

        This deliberately derives the labels from the selected BIN instead of
        hardcoding the stock names, so Partner Table/name edits are reflected.
        """
        if not self.current_bin_path or not os.path.isfile(self.current_bin_path):
            return {}

        tmp_dir = tempfile.mkdtemp(prefix="evolution_slots_names_")
        partner_csv = os.path.join(tmp_dir, "partner.csv")
        names_csv = os.path.join(tmp_dir, "names.csv")

        try:
            replace_map = os.path.join(SCRIPT_DIR, "replace_map.csv")
            if not os.path.isfile(replace_map):
                raise RuntimeError(
                    "replace_map.csv was not found in the scripts folder. "
                    "It is required to decode current Digimon names."
                )

            self._run_script_sync(
                self.partner_export_script_name(),
                [self.current_bin_path, partner_csv],
            )
            self._run_script_sync(
                self.names_export_script_name(),
                [self.current_bin_path, replace_map, names_csv],
            )

            names_by_string = {}
            with open(names_csv, "r", encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    string_index = str(row.get("string_index", "")).strip()
                    name = str(row.get("name", "")).strip()
                    if string_index:
                        names_by_string[string_index] = name

            result = {}
            with open(partner_csv, "r", encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    digimon_text = str(row.get("digimon_id", "")).strip()
                    string_index = str(row.get("string_index", "")).strip()

                    if not digimon_text:
                        continue

                    try:
                        digimon_id = int(digimon_text, 0)
                    except Exception:
                        continue

                    if digimon_id not in self.expected_partner_ids():
                        continue

                    name = names_by_string.get(string_index, "").strip()
                    if not name:
                        raise RuntimeError(
                            f"Could not resolve a decoded name for Digimon ID "
                            f"{digimon_id} (string_index {string_index})."
                        )

                    result[digimon_id] = name

            expected = set(self.expected_partner_ids())
            missing = sorted(expected - set(result))
            if missing:
                raise RuntimeError(
                    "Could not build Partner Table names for Digimon ID(s): "
                    + ", ".join(str(x) for x in missing)
                )

            return result

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Slot dropdowns / table
    # ------------------------------------------------------------------

    def make_slot_combo(self, current_value):
        combo = NoWheelComboBox()

        # Blank/FFFF value.
        combo.addItem("-", "")

        for digimon_id in sorted(self.digimon_names):
            combo.addItem(self.digimon_names[digimon_id], digimon_id)

        text = str(current_value or "").strip()
        if text.upper() == "FFFF" or text.lower() == "0xffff":
            text = ""

        if text == "":
            combo.setCurrentIndex(0)
        else:
            try:
                value = int(text, 0)
            except Exception:
                value = None

            index = combo.findData(value)
            if index < 0:
                # The CSV should never contain this after backend validation,
                # but preserve visibility instead of silently choosing another ID.
                combo.insertItem(0, f"(current invalid value: {text})", value)
                combo.setCurrentIndex(0)
            else:
                combo.setCurrentIndex(index)

        combo.currentIndexChanged.connect(self.on_table_changed)
        return combo

    def _make_readonly_item(self, text):
        item = QtWidgets.QTableWidgetItem(str(text))
        item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
        item.setBackground(QtGui.QColor(70, 70, 70))
        item.setForeground(QtGui.QColor(220, 220, 220))
        return item

    def populate_table_from_csv(self, csv_path):
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))

        expected_rows = self.expected_line_count()
        if len(rows) != expected_rows:
            raise RuntimeError(
                f"Evolution Slots CSV must contain exactly {expected_rows} line "
                f"rows for the selected device; found {len(rows)}."
            )

        # Ensure the selected BIN's current names are what the combos display.
        self.digimon_names = self.build_digimon_name_map_from_bin()

        headers = ["line_id", "line_name"] + SLOT_COLUMNS

        self._populating = True
        try:
            self.table.clear()
            self.table.setRowCount(len(rows))
            self.table.setColumnCount(len(headers))
            self.table.setHorizontalHeaderLabels(headers)
            self.hidden_rows = {}

            seen_line_ids = set()

            for r_idx, row in enumerate(rows):
                try:
                    line_id = int(str(row.get("line_id", "")).strip(), 0)
                except Exception:
                    raise RuntimeError(
                        f"CSV row {r_idx + 2}: invalid line_id "
                        f"{row.get('line_id')!r}."
                    )

                if line_id in seen_line_ids:
                    raise RuntimeError(f"CSV contains duplicate line_id {line_id}.")
                seen_line_ids.add(line_id)

                expected_name = (
                    D3_LINE_NAMES.get(line_id)
                    if self.is_d3()
                    else DARK_LINE_NAMES.get(line_id)
                )
                if expected_name is None:
                    raise RuntimeError(
                        f"CSV row {r_idx + 2}: line_id {line_id} is invalid "
                        f"for the selected device."
                    )

                line_name = str(row.get("line_name", expected_name)).strip()
                if not line_name:
                    line_name = expected_name

                self.table.setItem(r_idx, 0, self._make_readonly_item(line_id))
                self.table.setItem(r_idx, 1, self._make_readonly_item(line_name))

                if self.is_d_ark():
                    self.hidden_rows[r_idx] = {
                        "line_offset": str(
                            row.get(
                                "line_offset",
                                DARK_LINE_OFFSETS.get(line_id, ""),
                            )
                        ).strip()
                    }
                else:
                    self.hidden_rows[r_idx] = {}

                for slot_index, slot_name in enumerate(SLOT_COLUMNS, start=2):
                    self.table.setCellWidget(
                        r_idx,
                        slot_index,
                        self.make_slot_combo(row.get(slot_name, "")),
                    )

            expected_line_ids = set(range(expected_rows))
            if seen_line_ids != expected_line_ids:
                raise RuntimeError(
                    f"CSV must contain line_id values "
                    f"{sorted(expected_line_ids)} exactly once."
                )

            self.table.resizeColumnsToContents()
            header = self.table.horizontalHeader()
            header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Interactive)

            self.table.setColumnWidth(0, 80)
            self.table.setColumnWidth(1, 160)
            header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
            header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Fixed)

            # Make every dropdown wide enough for typical partner names.
            for col in range(2, 12):
                self.table.setColumnWidth(col, max(150, self.table.columnWidth(col)))

            self._table_loaded = True
            self.save_edits_btn.setEnabled(True)

        finally:
            self._populating = False

    def on_table_changed(self, *args):
        if self._populating:
            return

        if self._table_loaded:
            self.save_edits_btn.setEnabled(True)
            self.status_label.setText(
                "Evolution slot edits changed in the table. "
                "Press Save Evolution Slot Edits to BIN to apply them."
            )

    # ------------------------------------------------------------------
    # Table -> CSV
    # ------------------------------------------------------------------

    def collect_table_rows(self):
        if not self._table_loaded or self.table.rowCount() == 0:
            raise RuntimeError("There is no Evolution Slots table loaded.")

        rows = []

        for r in range(self.table.rowCount()):
            line_id_item = self.table.item(r, 0)
            line_name_item = self.table.item(r, 1)

            if line_id_item is None or line_name_item is None:
                raise RuntimeError(f"Table row {r + 1} is incomplete.")

            line_id = int(line_id_item.text(), 0)
            line_name = line_name_item.text().strip()

            row = {
                "line_id": line_id,
                "line_name": line_name,
            }

            if self.is_d_ark():
                row["line_offset"] = self.hidden_rows.get(r, {}).get(
                    "line_offset",
                    DARK_LINE_OFFSETS.get(line_id, ""),
                )

            hit_blank = False
            for i, slot_name in enumerate(SLOT_COLUMNS, start=2):
                combo = self.table.cellWidget(r, i)
                if combo is None:
                    raise RuntimeError(
                        f"Table row {r + 1}, {slot_name} has no dropdown."
                    )

                value = combo.currentData()

                if value is None or str(value).strip() == "":
                    row[slot_name] = ""
                    hit_blank = True
                else:
                    if hit_blank:
                        raise RuntimeError(
                            f"{line_name}: {slot_name} is filled after an earlier "
                            "blank slot. Slots must be contiguous from slot_1."
                        )
                    row[slot_name] = int(value)

            rows.append(row)

        self.validate_table_membership(rows)
        return rows

    def validate_table_membership(self, rows):
        occurrences = defaultdict(list)

        for row in rows:
            line_id = int(row["line_id"])
            nonempty = 0

            for slot_name in SLOT_COLUMNS:
                value = row.get(slot_name, "")
                if value == "":
                    continue
                value = int(value)
                occurrences[value].append(line_id)
                nonempty += 1

            if nonempty == 0:
                raise RuntimeError(
                    f"{row['line_name']} cannot be completely empty."
                )

        if self.is_d_ark():
            expected = set(range(21))
            used = set(occurrences)

            missing = sorted(expected - used)
            duplicate = sorted(
                digimon_id
                for digimon_id, lines in occurrences.items()
                if len(lines) != 1
            )

            if missing or duplicate:
                parts = []
                if missing:
                    parts.append(
                        "Missing Digimon: "
                        + ", ".join(
                            self.digimon_names.get(x, str(x)) for x in missing
                        )
                    )
                if duplicate:
                    parts.append(
                        "Digimon appearing more than once: "
                        + ", ".join(
                            self.digimon_names.get(x, str(x)) for x in duplicate
                        )
                    )
                raise RuntimeError("\n".join(parts))

            total = sum(len(v) for v in occurrences.values())
            if total != 21:
                raise RuntimeError(
                    f"D-Ark requires exactly 21 non-empty evolution slots; found {total}."
                )

            return

        # D-3 complete membership constraints.
        expected_ids = set(range(38))

        missing = sorted(expected_ids - set(occurrences))
        if missing:
            raise RuntimeError(
                "Every D-3 partner Digimon ID 0..37 must remain represented. "
                "Missing: "
                + ", ".join(self.digimon_names.get(x, str(x)) for x in missing)
            )

        for digimon_id, expected_lines in D3_SHARED_ID_LINES.items():
            actual_lines = set(occurrences.get(digimon_id, []))
            if actual_lines != expected_lines:
                expected_names = [D3_LINE_NAMES[x] for x in sorted(expected_lines)]
                actual_names = [D3_LINE_NAMES[x] for x in sorted(actual_lines)]
                raise RuntimeError(
                    f"{self.digimon_names.get(digimon_id, str(digimon_id))} "
                    f"is a protected shared evolution and must remain in "
                    f"{expected_names}; found {actual_names}."
                )

        for digimon_id in range(38):
            if digimon_id in D3_SHARED_ID_LINES:
                continue

            actual = occurrences.get(digimon_id, [])
            if len(actual) != 1:
                raise RuntimeError(
                    f"{self.digimon_names.get(digimon_id, str(digimon_id))} "
                    f"must appear exactly once; found {len(actual)} occurrence(s)."
                )

        total = sum(len(v) for v in occurrences.values())
        if total != 44:
            raise RuntimeError(
                f"D-3 requires exactly 44 total complete-line occurrences; found {total}."
            )

    def write_rows_to_csv(self, rows, path):
        if self.is_d3():
            fieldnames = ["line_id", "line_name"] + SLOT_COLUMNS
        else:
            fieldnames = ["line_id", "line_name", "line_offset"] + SLOT_COLUMNS

        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    # ------------------------------------------------------------------
    # Export / Refresh
    # ------------------------------------------------------------------

    def on_export_clicked(self):
        if not self.require_all():
            return

        out_csv = self.export_csv_edit.text().strip()
        if not out_csv:
            QtWidgets.QMessageBox.warning(
                self,
                "CSV path required",
                "Please specify an export CSV path.",
            )
            return

        script = self.export_script_name()
        script_path = os.path.join(SCRIPT_DIR, script)
        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.critical(
                self,
                "Missing script",
                f"{script} not found in the scripts folder.",
            )
            return

        dlg = BusyDialog(
            "Export Evolution Slots",
            "Please wait...\nExporting evolution slots from the selected BIN.",
            self,
        )

        worker = InternalScriptWorker(
            script_name=script,
            script_args=[self.current_bin_path, out_csv],
            desc="Export Evolution Slots",
        )

        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        def done(ok, msg):
            dlg.accept()
            thread.quit()
            thread.wait()

            self.status_label.setText(self._short_status(msg))

            if ok:
                try:
                    self.populate_table_from_csv(out_csv)
                except Exception as exc:
                    QtWidgets.QMessageBox.warning(
                        self,
                        "Table load warning",
                        f"Evolution Slots export succeeded, but the UI table "
                        f"could not be loaded:\n\n{exc}",
                    )
                else:
                    QtWidgets.QMessageBox.information(
                        self,
                        "Evolution Slots Exported",
                        f"Evolution slots were exported to:\n{out_csv}",
                    )
                    self.status_label.setText("Evolution slots exported and loaded.")
            else:
                QtWidgets.QMessageBox.critical(
                    self,
                    "Export Evolution Slots Error",
                    msg,
                )

        worker.finished.connect(done)
        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()

    def on_refresh_clicked(self):
        if not self.require_all():
            return

        tmp_dir = tempfile.mkdtemp(prefix="evolution_slots_gui_")
        tmp_csv = os.path.join(tmp_dir, "evolution_slots_tmp.csv")

        script = self.export_script_name()
        script_path = os.path.join(SCRIPT_DIR, script)

        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.critical(
                self,
                "Missing script",
                f"{script} not found in the scripts folder.",
            )
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        dlg = BusyDialog(
            "Refresh Evolution Slots",
            "Please wait...\nLoading evolution slots and current Partner names.",
            self,
        )

        worker = InternalScriptWorker(
            script_name=script,
            script_args=[self.current_bin_path, tmp_csv],
            desc="Refresh Evolution Slots",
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
                    self.status_label.setText("Evolution slots loaded.")
                except Exception as exc:
                    self._table_loaded = False
                    self.save_edits_btn.setEnabled(False)
                    self.status_label.setText("Evolution Slots load failed.")
                    QtWidgets.QMessageBox.critical(
                        self,
                        "Evolution Slots Refresh Error",
                        str(exc),
                    )
            else:
                self._table_loaded = False
                self.save_edits_btn.setEnabled(False)
                self.status_label.setText(self._short_status(msg))
                QtWidgets.QMessageBox.critical(
                    self,
                    "Evolution Slots Refresh Error",
                    msg,
                )

            shutil.rmtree(tmp_dir, ignore_errors=True)

        worker.finished.connect(done)
        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()

    # ------------------------------------------------------------------
    # Import / Save / Reset
    # ------------------------------------------------------------------

    def on_import_clicked(self):
        if not self.require_all():
            return

        in_csv, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select evolution slots CSV",
            "",
            "CSV files (*.csv);;All files (*)",
        )
        if not in_csv:
            return

        self.run_import_script(
            in_csv,
            reload_after=True,
            success_title="Evolution Slots Imported",
        )

    def on_save_edits_clicked(self):
        if not self.require_all():
            return

        try:
            rows = self.collect_table_rows()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid Evolution Slots",
                str(exc),
            )
            self.status_label.setText("Evolution Slot table needs attention.")
            return

        response = QtWidgets.QMessageBox.warning(
            self,
            "Save Evolution Slot Edits to BIN?",
            (
                "This will update the selected BIN in place.\n\n"
                "The evolution-line order/membership structures and Partner "
                "Table line assignment will be synchronized by the validated "
                "importer.\n\n"
                "Continue?"
            ),
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
        )

        if response != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        tmp_dir = tempfile.mkdtemp(prefix="evolution_slots_save_")
        tmp_csv = os.path.join(tmp_dir, "evolution_slots_edit.csv")

        try:
            self.write_rows_to_csv(rows, tmp_csv)
        except Exception as exc:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            QtWidgets.QMessageBox.critical(
                self,
                "Evolution Slots CSV Error",
                f"Failed to prepare the temporary CSV:\n\n{exc}",
            )
            return

        self.run_import_script(
            tmp_csv,
            reload_after=True,
            cleanup_dir=tmp_dir,
            success_title="Evolution Slot Edits Saved",
        )

    def on_reset_clicked(self):
        if not self.require_all():
            return

        original_csv = self.original_csv_path()

        if not os.path.isfile(original_csv):
            QtWidgets.QMessageBox.critical(
                self,
                "Missing file",
                f"{os.path.basename(original_csv)} was not found in the scripts folder.",
            )
            return

        response = QtWidgets.QMessageBox.warning(
            self,
            "Reset Evolution Slots to Original?",
            (
                "This will overwrite ALL Evolution Slots data in the selected BIN "
                "using:\n"
                f"{os.path.basename(original_csv)}\n\n"
                "Partner Table line assignments will also be synchronized.\n"
                "Game progress is not intentionally reset, but Evolution Slots "
                "modding changes will be lost.\n\n"
                "Continue?"
            ),
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
        )

        if response != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        self.run_import_script(
            original_csv,
            reload_after=True,
            success_title="Evolution Slots Reset",
        )

    def run_import_script(
        self,
        csv_path,
        reload_after=False,
        cleanup_dir=None,
        success_title="Evolution Slots Imported",
    ):
        script = self.import_script_name()
        script_path = os.path.join(SCRIPT_DIR, script)

        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.critical(
                self,
                "Missing script",
                f"{script} not found in the scripts folder.",
            )
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
            return

        dlg = BusyDialog(
            "Apply Evolution Slots",
            "Please wait...\nValidating and applying Evolution Slots to the BIN.",
            self,
        )

        worker = InternalScriptWorker(
            script_name=script,
            script_args=[
                self.current_bin_path,
                csv_path,
                self.current_bin_path,
            ],
            desc="Apply Evolution Slots",
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
                    success_title,
                    msg,
                )
                if reload_after:
                    self.on_refresh_clicked()
            else:
                QtWidgets.QMessageBox.critical(
                    self,
                    "Evolution Slots Import Error",
                    msg,
                )

            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)

        worker.finished.connect(done)
        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()


if __name__ == "__main__":
    import sys

    app = QtWidgets.QApplication(sys.argv)
    w = EvolutionSlotsTab()
    w.resize(1250, 760)
    w.show()
    sys.exit(app.exec())
