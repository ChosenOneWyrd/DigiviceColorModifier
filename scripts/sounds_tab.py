from PyQt5 import QtCore, QtWidgets
import os
from typing import Optional

from common import *

# ----------------- Sounds Tab -----------------

class SoundsTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.current_bin_type_key: Optional[str] = None
        self.current_bin_path: Optional[str] = None
        self.input_sounds_dir: Optional[str] = None

        # Will be set when BIN type is chosen
        self.sound_map_csv: Optional[str] = None

        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        # -------- BIN selection --------
        bin_box = QtWidgets.QGroupBox("BIN Selection")
        bin_layout = QtWidgets.QHBoxLayout(bin_box)

        self.bin_type_combo = NoWheelComboBox()
        self.bin_type_combo.addItem("Select BIN type...")
        for key, info in BIN_TYPES.items():
            self.bin_type_combo.addItem(info["label"], key)

        self.bin_path_edit = QtWidgets.QLineEdit()
        self.bin_path_edit.setReadOnly(True)
        self.bin_browse_btn = QtWidgets.QPushButton("Select .bin file")

        bin_layout.addWidget(QtWidgets.QLabel("Type:"))
        bin_layout.addWidget(self.bin_type_combo)
        bin_layout.addSpacing(20)
        bin_layout.addWidget(QtWidgets.QLabel("BIN File:"))
        bin_layout.addWidget(self.bin_path_edit)
        bin_layout.addWidget(self.bin_browse_btn)

        layout.addWidget(bin_box)

        # -------- Input folder --------
        input_box = QtWidgets.QGroupBox("Import Sounds")
        input_layout = QtWidgets.QVBoxLayout(input_box)

        h = QtWidgets.QHBoxLayout()
        self.sounds_dir_edit = QtWidgets.QLineEdit()
        self.sounds_dir_edit.setReadOnly(True)
        self.sounds_dir_btn = QtWidgets.QPushButton("Select input_sounds folder")
        h.addWidget(self.sounds_dir_edit)
        h.addWidget(self.sounds_dir_btn)
        
        self.import_btn = QtWidgets.QPushButton("Import Sounds into BIN")
        self.import_btn.setStyleSheet("background-color: #008000; color: white; font-weight:600; font-size:14pt;")

        input_layout.addLayout(h)
        input_layout.addWidget(self.import_btn)

        layout.addWidget(input_box)

        # -------- Export box --------
        export_box = QtWidgets.QGroupBox("Export Sounds")
        export_layout = QtWidgets.QVBoxLayout(export_box)

        self.export_btn = QtWidgets.QPushButton("Export Sounds to Desktop/exported_sounds")
        self.export_btn.setStyleSheet("background-color: #0006b1; color: white; font-weight:600; font-size:14pt;")

        export_layout.addWidget(self.export_btn)
        layout.addWidget(export_box)

        # -------- Status --------
        self.status_label = QtWidgets.QLabel("Ready.")
        layout.addWidget(self.status_label)

        # -------- Connections --------
        self.bin_type_combo.currentIndexChanged.connect(self.on_type_changed)
        self.bin_browse_btn.clicked.connect(self.on_pick_bin)
        self.sounds_dir_btn.clicked.connect(self.on_pick_sounds_dir)
        self.import_btn.clicked.connect(self.on_import_sounds)
        self.export_btn.clicked.connect(self.on_export_sounds)

    # --------------------------------------------------------------
    # Validation
    # --------------------------------------------------------------

    def require_bin(self):
        if not self.current_bin_type_key:
            QtWidgets.QMessageBox.warning(self, "Missing type", "Please select BIN type first.")
            return False
        if not self.current_bin_path:
            QtWidgets.QMessageBox.warning(self, "Missing BIN", "Please select a .bin file.")
            return False
        return True

    def require_sounds(self):
        if not self.input_sounds_dir or not os.path.isdir(self.input_sounds_dir):
            QtWidgets.QMessageBox.warning(self, "Missing sounds folder", "Please select input_sounds folder first.")
            return False
        return True

    # --------------------------------------------------------------
    # Events
    # --------------------------------------------------------------

    def on_type_changed(self, index):
        if index <= 0:
            self.current_bin_type_key = None
            self.sound_map_csv = None
            self.status_label.setText("Please select a BIN type.")
            return

        self.current_bin_type_key = self.bin_type_combo.itemData(index)

        # Pick the correct sound map CSV depending on BIN type
        if self.current_bin_type_key == "D-3":
            self.sound_map_csv = os.path.join(SCRIPT_DIR, "d3_sound_map.csv")
        elif self.current_bin_type_key == "Digivice":
            self.sound_map_csv = os.path.join(SCRIPT_DIR, "digivice_sound_map.csv")
        else:
            self.sound_map_csv = None

        # Optional: small status hint
        if self.sound_map_csv and os.path.isfile(self.sound_map_csv):
            self.status_label.setText(f"Using sound map: {os.path.basename(self.sound_map_csv)}")
        else:
            self.status_label.setText("Sound map CSV not found for this BIN type.")

    def on_pick_bin(self):
        if not self.current_bin_type_key:
            QtWidgets.QMessageBox.warning(self, "Missing type", "Please select BIN type first.")
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select .bin", "", "BIN files (*.bin)")
        if path:
            self.current_bin_path = path
            self.bin_path_edit.setText(path)

    def on_pick_sounds_dir(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select input_sounds folder")
        if d:
            self.input_sounds_dir = d
            self.sounds_dir_edit.setText(d)

    # --------------------------------------------------------------
    # Import Sounds
    # --------------------------------------------------------------

    def on_import_sounds(self):
        if not (self.require_bin() and self.require_sounds()):
            return

        script = "import_sounds.py"

        dlg = BusyDialog("Importing Sounds", "Working...\nThis may take a while.", self)

        worker = InternalScriptWorker(
            script_name=script,
            script_args=[
                self.current_bin_path,
                self.current_bin_path,
                self.sound_map_csv,
                self.input_sounds_dir,
            ],
            desc="Sound import"
        )

        t = QtCore.QThread(self)
        worker.moveToThread(t)

        worker.finished.connect(lambda ok, msg: self._import_done(ok, msg, dlg, t))

        t.started.connect(worker.run)
        t.start()
        dlg.exec()

    def _import_done(self, ok, msg, dlg, thread):
        dlg.accept()
        thread.quit()
        thread.wait()
        self.status_label.setText(msg)
        if ok:
            QtWidgets.QMessageBox.information(self, "Sounds Imported", msg)
        else:
            QtWidgets.QMessageBox.critical(self, "Sound Import Error", msg)

    # --------------------------------------------------------------
    # Export Sounds
    # --------------------------------------------------------------

    def on_export_sounds(self):
        if not self.require_bin():
            return

        out_dir = os.path.join(os.path.expanduser("~"), "Desktop", "exported_sounds")
        os.makedirs(out_dir, exist_ok=True)

        script = "export_sounds.py"

        dlg = BusyDialog("Exporting Sounds", "Working...\nThis may take a while.", self)

        worker = InternalScriptWorker(
            script_name=script,
            script_args=[
                self.current_bin_path,
                out_dir,
                self.sound_map_csv,
            ],
            desc="Sound export"
        )

        t = QtCore.QThread(self)
        worker.moveToThread(t)
        worker.finished.connect(lambda ok, msg: self._export_done(ok, msg, dlg, t))

        t.started.connect(worker.run)
        t.start()
        dlg.exec()

    def _export_done(self, ok, msg, dlg, thread):
        dlg.accept()
        thread.quit()
        thread.wait()
        self.status_label.setText(msg)
        if ok:
            QtWidgets.QMessageBox.information(
                self, "Sounds Exported",
                'Sounds were exported to "exported_sounds" on your Desktop.'
            )
        else:
            QtWidgets.QMessageBox.critical(self, "Sound Export Error", msg)