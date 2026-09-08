#!/usr/bin/env python3
"""
Replace Evolution Images tab for Digimon BIN Tool.

Supported:
    D-3 25th Color
    D-Ark 25th Color

This tab intentionally edits ONE evolution animation at a time.

Backends:
    replace_d3_evo_image.py
    replace_d_ark_evo_image.py

Sprite previews:
    export_sprites_safe_fast.py

CSV format (one active edit per exported/imported CSV):
    evo_animation_id,evo_animation_name,source_image,destination_image,
    d3_scene_group,d_ark_max_group_hops,d_ark_all_matches,
    match_offsets,subimage_offsets

Image identifiers use:
    IMAGE_SUBIMAGE_BANK.png
Example:
    338_0_0.png
"""

import csv
import os
import re
import struct
from pathlib import Path
from typing import Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from common import *
import replace_d3_evo_image as d3_backend
import replace_d_ark_evo_image as dark_backend
import export_sprites_safe_fast as sprite_exporter


RE_PNG = re.compile(r"^(\d+)_(\d+)_(\d+)\.png$", re.IGNORECASE)

CSV_FIELDS = [
    "evo_animation_id",
    "evo_animation_name",
    "source_image",
    "destination_image",
    "d3_scene_group",
    "d_ark_max_group_hops",
    "d_ark_all_matches",
    "match_offsets",
    "subimage_offsets",
]


class ReplaceEvolutionImagesTab(QtWidgets.QWidget):
    """Replace one evolution-animation image reference at a time."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.current_bin_type_key: Optional[str] = None
        self.current_bin_path: Optional[str] = None

        self.evo_map = {}
        self.evo_value_to_key = {}

        self._cache_key = None
        self._cache = None

        self._loading_form = False
        self._source_exists = False
        self._source_status = ""
        self._destination_status = ""
        self._match_offsets = []
        self._subimage_offsets = []

        self.validation_timer = QtCore.QTimer(self)
        self.validation_timer.setSingleShot(True)
        self.validation_timer.setInterval(250)
        self.validation_timer.timeout.connect(self.validate_current_edit)

        self._build_ui()
        self._connect_signals()
        self._update_device_widgets()
        self.update_save_enabled()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)

        # BIN Selection -- intentionally mirrors Partner Table.
        top_box = QtWidgets.QGroupBox("BIN Selection")
        top_layout = QtWidgets.QHBoxLayout(top_box)

        self.bin_type_combo = NoWheelComboBox()
        self.bin_type_combo.addItem("Select BIN type...")
        for key in ("D-3", "D-ark"):
            info = BIN_TYPES.get(key)
            if info:
                self.bin_type_combo.addItem(info["label"], key)
            else:
                self.bin_type_combo.addItem(key, key)

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

        # CSV + in-app editing controls.
        io_box = QtWidgets.QGroupBox("Evolution Images CSV & In-App Editing")
        io_layout = QtWidgets.QGridLayout(io_box)

        # self.export_csv_edit = QtWidgets.QLineEdit()

        # self.export_btn = QtWidgets.QPushButton("Export Evolution Images to CSV")
        # self.export_btn.setStyleSheet(
        #     "background-color:#0006b1;color:white;font-weight:600;font-size:14pt;"
        # )

        # self.import_btn = QtWidgets.QPushButton("Import Evolution Images from CSV")
        # self.import_btn.setStyleSheet(
        #     "background-color:#0006b1;color:white;font-weight:600;font-size:14pt;"
        # )

        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.refresh_btn.setStyleSheet(
            "background-color:#008000;color:white;font-weight:600;font-size:14pt;"
        )

        # self.reset_btn = QtWidgets.QPushButton("Reset to Original ?")
        # self.reset_btn.setStyleSheet(
        #     "background-color:#960202;color:white;font-weight:600;font-size:14pt;"
        # )

        self.save_edits_btn = QtWidgets.QPushButton("Save Evolution Image Edits to BIN")
        self.save_edits_btn.setStyleSheet(
            "background-color:#008000;color:white;font-weight:600;font-size:14pt;"
        )
        self.save_edits_btn.setEnabled(False)

        # io_layout.addWidget(QtWidgets.QLabel("Export CSV path:"), 0, 0)
        # io_layout.addWidget(self.export_csv_edit, 0, 1)
        # io_layout.addWidget(self.export_btn, 0, 2)

        io_layout.addWidget(self.refresh_btn, 1, 0)
        # io_layout.addWidget(self.reset_btn, 1, 1)
        io_layout.addWidget(self.save_edits_btn, 1, 2)
        # io_layout.addWidget(self.import_btn, 1, 3)

        main_layout.addWidget(io_box)

        # One active edit only.
        edit_box = QtWidgets.QGroupBox("Replace One Evolution Image")
        edit_layout = QtWidgets.QGridLayout(edit_box)
        edit_layout.setColumnStretch(1, 1)
        edit_layout.setColumnStretch(3, 1)

        edit_layout.addWidget(QtWidgets.QLabel("Evolution animation:"), 0, 0)
        self.animation_combo = NoWheelComboBox()
        edit_layout.addWidget(self.animation_combo, 0, 1, 1, 3)

        example = QtWidgets.QLabel(
            "Image format: IMAGE_SUBIMAGE_BANK.png    Example: 338_0_0.png"
        )
        example.setStyleSheet("color:#f6c85f;")
        example.setWordWrap(True)
        edit_layout.addWidget(example, 1, 0, 1, 4)

        edit_layout.addWidget(QtWidgets.QLabel("Source image:"), 2, 0)
        self.source_edit = QtWidgets.QLineEdit()
        self.source_edit.setPlaceholderText("e.g. 338_0_0.png")
        edit_layout.addWidget(self.source_edit, 2, 1)

        edit_layout.addWidget(QtWidgets.QLabel("Destination image:"), 2, 2)
        self.destination_edit = QtWidgets.QLineEdit()
        self.destination_edit.setPlaceholderText("e.g. 487_0_0.png")
        edit_layout.addWidget(self.destination_edit, 2, 3)

        # D-3 option.
        self.d3_group_label = QtWidgets.QLabel("D-3 scene group override:")
        self.d3_group_edit = QtWidgets.QLineEdit()
        self.d3_group_edit.setPlaceholderText("blank = automatic mapping when known")
        self.d3_group_edit.setToolTip(
            "The current D-3 backend automatically maps 409→77, 410→79, "
            "411→81, 412→83, and 413→85. Other animation IDs need a proven "
            "scene-group override."
        )
        edit_layout.addWidget(self.d3_group_label, 3, 0)
        edit_layout.addWidget(self.d3_group_edit, 3, 1, 1, 3)

        # D-Ark options.
        self.dark_hops_label = QtWidgets.QLabel("D-Ark max group hops:")
        self.dark_hops_spin = QtWidgets.QSpinBox()
        self.dark_hops_spin.setRange(1, 512)
        self.dark_hops_spin.setValue(32)

        self.dark_all_matches_check = QtWidgets.QCheckBox("Replace all exact matches")
        self.dark_all_matches_check.setChecked(True)
        self.dark_all_matches_check.setToolTip(
            "If the D-Ark tracer finds multiple exact source references, "
            "saving is refused unless this is enabled."
        )

        edit_layout.addWidget(self.dark_hops_label, 4, 0)
        edit_layout.addWidget(self.dark_hops_spin, 4, 1)
        edit_layout.addWidget(self.dark_all_matches_check, 4, 2, 1, 2)

        self.source_validation_label = QtWidgets.QLabel(
            "Select a BIN, animation, and source image."
        )
        self.source_validation_label.setWordWrap(True)
        edit_layout.addWidget(self.source_validation_label, 5, 0, 1, 4)

        preview_layout = QtWidgets.QHBoxLayout()
        self.source_preview = self._make_preview_group("Source Preview")
        self.destination_preview = self._make_preview_group("Destination Preview")
        preview_layout.addWidget(self.source_preview[0])
        preview_layout.addWidget(self.destination_preview[0])
        edit_layout.addLayout(preview_layout, 6, 0, 1, 4)

        main_layout.addWidget(edit_box, 1)

        self.status_label = QtWidgets.QLabel("Ready.")
        self.status_label.setWordWrap(True)
        main_layout.addWidget(self.status_label)

    def _make_preview_group(self, title):
        box = QtWidgets.QGroupBox(title)
        layout = QtWidgets.QVBoxLayout(box)

        label = QtWidgets.QLabel("No preview")
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setMinimumSize(220, 220)
        label.setStyleSheet(
            "QLabel { background:#202020; border:1px solid #666; color:#aaa; }"
        )

        caption = QtWidgets.QLabel("")
        caption.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        caption.setWordWrap(True)

        layout.addWidget(label, 1)
        layout.addWidget(caption)
        return box, label, caption

    def _connect_signals(self):
        self.bin_type_combo.currentIndexChanged.connect(self.on_bin_type_changed)
        self.bin_browse_btn.clicked.connect(self.on_select_bin_file)

        # self.export_btn.clicked.connect(self.on_export_clicked)
        # self.import_btn.clicked.connect(self.on_import_clicked)
        self.refresh_btn.clicked.connect(self.on_refresh_clicked)
        # self.reset_btn.clicked.connect(self.on_reset_clicked)
        self.save_edits_btn.clicked.connect(self.on_save_edits_clicked)

        self.animation_combo.currentIndexChanged.connect(self.on_form_changed)
        self.source_edit.textChanged.connect(self.on_form_changed)
        self.destination_edit.textChanged.connect(self.on_form_changed)
        self.d3_group_edit.textChanged.connect(self.on_form_changed)
        self.dark_hops_spin.valueChanged.connect(self.on_form_changed)
        self.dark_all_matches_check.toggled.connect(self.on_form_changed)

    # ------------------------------------------------------------------
    # Device / mapping / path helpers
    # ------------------------------------------------------------------

    def is_d3(self):
        return self.current_bin_type_key == "D-3"

    def is_d_ark(self):
        return self.current_bin_type_key == "D-ark"

    def map_filename(self):
        return (
            "d3_evo_animation_map.csv"
            if self.is_d3()
            else "d_ark_evo_animation_map.csv"
        )

    def original_csv_path(self):
        name = (
            "d3_evolution_images_original.csv"
            if self.is_d3()
            else "d_ark_evolution_images_original.csv"
        )
        return os.path.join(SCRIPT_DIR, name)

    def default_export_csv_path(self):
        if self.is_d3():
            name = "d3_evolution_images.csv"
        elif self.is_d_ark():
            name = "d_ark_evolution_images.csv"
        else:
            name = "evolution_images.csv"
        return os.path.join(os.path.expanduser("~"), "Desktop", name)

    def load_evo_map(self):
        self.evo_map = {}
        self.evo_value_to_key = {}

        if not (self.is_d3() or self.is_d_ark()):
            return

        path = os.path.join(SCRIPT_DIR, self.map_filename())
        if not os.path.isfile(path):
            QtWidgets.QMessageBox.critical(
                self,
                "Missing animation map",
                f"{self.map_filename()} was not found in the scripts folder.",
            )
            return

        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                key = str(row.get("key", "")).strip()
                value_text = str(row.get("value", "")).strip()
                if not key or not value_text:
                    continue
                try:
                    value = int(value_text, 0)
                except Exception:
                    continue
                self.evo_map[key] = value
                self.evo_value_to_key[value] = key

    def populate_animation_combo(self, current_value=None):
        self._loading_form = True
        try:
            self.animation_combo.clear()
            self.animation_combo.addItem("Select evolution animation...", None)

            for key, value in self.evo_map.items():
                self.animation_combo.addItem(key, value)

            if current_value is not None:
                index = self.animation_combo.findData(int(current_value))
                if index >= 0:
                    self.animation_combo.setCurrentIndex(index)
        finally:
            self._loading_form = False

    def animation_name(self, animation_id):
        return self.evo_value_to_key.get(int(animation_id), f"animation_{animation_id}")

    def _update_device_widgets(self):
        d3 = self.is_d3()
        dark = self.is_d_ark()

        self.d3_group_label.setVisible(d3)
        self.d3_group_edit.setVisible(d3)

        self.dark_hops_label.setVisible(dark)
        self.dark_hops_spin.setVisible(dark)
        self.dark_all_matches_check.setVisible(dark)

    def on_bin_type_changed(self, index):
        self.current_bin_type_key = (
            None if index <= 0 else self.bin_type_combo.itemData(index)
        )
        self.current_bin_path = None
        self.bin_path_edit.clear()
        self._clear_cache()

        self.load_evo_map()
        self.populate_animation_combo()
        # self.export_csv_edit.setText(
        #     self.default_export_csv_path() if self.current_bin_type_key else ""
        # )
        self._update_device_widgets()

        self._loading_form = True
        try:
            self.source_edit.clear()
            self.destination_edit.clear()
            self.d3_group_edit.clear()
            self.dark_hops_spin.setValue(32)
            self.dark_all_matches_check.setChecked(True)
        finally:
            self._loading_form = False

        self.clear_previews()
        self._reset_validation_state()
        self.update_save_enabled()

    def on_select_bin_file(self):
        if not (self.is_d3() or self.is_d_ark()):
            QtWidgets.QMessageBox.warning(
                self, "Type required", "Please select D-3 or D-Ark BIN type first."
            )
            return

        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select .bin file", "", "BIN files (*.bin);;All files (*)"
        )
        if not path:
            return

        self.current_bin_path = path
        self.bin_path_edit.setText(path)
        self._clear_cache()
        self.validate_current_edit(show_dialog=False)

    def require_bin(self):
        if not (self.is_d3() or self.is_d_ark()):
            QtWidgets.QMessageBox.warning(
                self, "Type required", "Please select D-3 or D-Ark BIN type first."
            )
            return False

        if not self.current_bin_path or not os.path.isfile(self.current_bin_path):
            QtWidgets.QMessageBox.warning(
                self, "BIN required", "Please select a valid .bin file."
            )
            return False

        return True

    def require_bin_silent(self):
        return bool(
            (self.is_d3() or self.is_d_ark())
            and self.current_bin_path
            and os.path.isfile(self.current_bin_path)
        )

    # ------------------------------------------------------------------
    # Current form state
    # ------------------------------------------------------------------

    def current_animation_id(self):
        data = self.animation_combo.currentData()
        return int(data) if data is not None else 0

    def current_row(self):
        animation_id = self.current_animation_id()
        return {
            "evo_animation_id": animation_id,
            "evo_animation_name": self.animation_name(animation_id),
            "source_image": self.source_edit.text().strip(),
            "destination_image": self.destination_edit.text().strip(),
            "d3_scene_group": self.d3_group_edit.text().strip(),
            "d_ark_max_group_hops": self.dark_hops_spin.value(),
            "d_ark_all_matches": self.dark_all_matches_check.isChecked(),
            "match_offsets": list(self._match_offsets),
            "subimage_offsets": list(self._subimage_offsets),
        }

    def apply_row_to_form(self, row):
        self._loading_form = True
        try:
            animation_id = int(row.get("evo_animation_id", 0) or 0)
            index = self.animation_combo.findData(animation_id)
            if index >= 0:
                self.animation_combo.setCurrentIndex(index)
            else:
                raise RuntimeError(
                    f"Animation ID {animation_id} is not present in {self.map_filename()}."
                )

            self.source_edit.setText(str(row.get("source_image", "")).strip())
            self.destination_edit.setText(str(row.get("destination_image", "")).strip())
            self.d3_group_edit.setText(str(row.get("d3_scene_group", "")).strip())

            try:
                hops = int(row.get("d_ark_max_group_hops", 32) or 32)
            except Exception:
                hops = 32
            self.dark_hops_spin.setValue(max(1, min(512, hops)))

            all_matches = str(row.get("d_ark_all_matches", "")).strip().lower()
            self.dark_all_matches_check.setChecked(
                all_matches in ("1", "true", "yes", "on")
                if not isinstance(row.get("d_ark_all_matches"), bool)
                else bool(row.get("d_ark_all_matches"))
            )
        finally:
            self._loading_form = False

        self._match_offsets = self._parse_offset_list(row.get("match_offsets", ""))
        self._subimage_offsets = self._parse_nullable_offset_list(
            row.get("subimage_offsets", "")
        )

        self.validate_current_edit(show_dialog=False)

    def on_form_changed(self, *args):
        if self._loading_form:
            return
        self._reset_validation_state()
        self.validation_timer.start()

    def _reset_validation_state(self):
        self._source_exists = False
        self._source_status = ""
        self._destination_status = ""
        self._match_offsets = []
        self._subimage_offsets = []
        self.source_validation_label.setText("Checking..." if self.require_bin_silent() else "Select a BIN.")
        self.update_save_enabled()
        self.update_previews()

    # ------------------------------------------------------------------
    # Filename parsing and previews
    # ------------------------------------------------------------------

    def parse_image_name(self, text):
        # Parse exactly what is in the field. Do not rebuild a table or mutate
        # the editor during validation.
        name = str(text).strip()
        m = RE_PNG.fullmatch(name)
        if not m:
            raise RuntimeError(
                f"{name!r} must use IMAGE_SUBIMAGE_BANK.png format, "
                "for example 338_0_0.png."
            )

        image, subimage, bank = (int(m.group(i)) for i in range(1, 4))
        if bank < 0 or bank > 15:
            raise RuntimeError("Palette bank must be 0..15.")
        return image, subimage, bank

    def _pil_to_pixmap(self, image):
        image = image.convert("RGBA")
        raw = image.tobytes("raw", "RGBA")
        qimg = QtGui.QImage(
            raw,
            image.width,
            image.height,
            image.width * 4,
            QtGui.QImage.Format_RGBA8888,
        ).copy()
        return QtGui.QPixmap.fromImage(qimg)

    def _sprite_subimage_count(self, pkg, image_index):
        if not 0 <= image_index < len(pkg["images"]):
            return 0

        idef = pkg["images"][image_index]
        sprites_per_sub = idef.width * idef.height
        if sprites_per_sub <= 0:
            return 0

        if image_index + 1 < len(pkg["images"]):
            total = (
                pkg["images"][image_index + 1].sprite_start_index
                - idef.sprite_start_index
            )
        else:
            total = len(pkg["sprites"]) - idef.sprite_start_index

        if total < 0 or total % sprites_per_sub:
            return 0

        return total // sprites_per_sub

    def _validate_sprite_identifier(self, pkg, image_index, subimage_index):
        if not 0 <= image_index < len(pkg["images"]):
            raise RuntimeError(
                f"Image {image_index} is outside 0..{len(pkg['images']) - 1}."
            )

        count = self._sprite_subimage_count(pkg, image_index)
        if not 0 <= subimage_index < count:
            raise RuntimeError(
                f"Image {image_index} has {count} subimage(s); "
                f"subimage {subimage_index} is invalid."
            )

    def render_sprite_preview(self, image_name):
        if not self.require_bin_silent():
            raise RuntimeError("Select a valid BIN first.")

        image_index, subimage_index, bank = self.parse_image_name(image_name)
        cache = self.get_bin_cache()
        pkg = cache["sprite"]

        self._validate_sprite_identifier(pkg, image_index, subimage_index)

        image = sprite_exporter.compose_subimage(
            pkg["block"],
            pkg["images"],
            pkg["sprites"],
            pkg["palette_words"],
            pkg["chars_offset"],
            image_index,
            subimage_index,
            bank,
            alpha_mode="auto",
            palette_step_mode="colors",
            use_attr_palette=False,
        )

        if image is None:
            raise RuntimeError(f"Could not render {image_name}.")

        return self._pil_to_pixmap(image), image.size

    def _show_preview(self, preview_tuple, image_name, *, allowed=True, error=None):
        _box, label, caption = preview_tuple

        if error:
            label.clear()
            label.setText("Preview unavailable")
            caption.setText(str(error))
            caption.setStyleSheet("color:#ff7777;")
            return

        if not image_name:
            label.clear()
            label.setText("No preview")
            caption.clear()
            caption.setStyleSheet("")
            return

        if not allowed:
            label.clear()
            label.setText("Source not in selected animation")
            caption.setText(
                "Source preview is disabled because this image is not referenced "
                "by the selected evolution animation."
            )
            caption.setStyleSheet("color:#ff7777;")
            return

        try:
            pixmap, size = self.render_sprite_preview(image_name)
            scaled = pixmap.scaled(
                label.size(),
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.FastTransformation,
            )
            label.setPixmap(scaled)
            label.setText("")
            caption.setText(f"{image_name}   {size[0]}×{size[1]}")
            caption.setStyleSheet("")
        except Exception as exc:
            label.clear()
            label.setText("Preview unavailable")
            caption.setText(str(exc))
            caption.setStyleSheet("color:#ff7777;")

    def clear_previews(self):
        self._show_preview(self.source_preview, "")
        self._show_preview(self.destination_preview, "")

    def update_previews(self):
        source_text = self.source_edit.text().strip()
        destination_text = self.destination_edit.text().strip()

        # Source is deliberately hidden unless scene validation succeeded.
        if source_text:
            self._show_preview(
                self.source_preview,
                source_text,
                allowed=self._source_exists,
                error=None if self._source_exists else self._source_status or None,
            )
        else:
            self._show_preview(self.source_preview, "")

        # Destination only needs to be a valid sprite in the BIN.
        if destination_text:
            if self._destination_status:
                self._show_preview(
                    self.destination_preview,
                    destination_text,
                    error=self._destination_status,
                )
            else:
                self._show_preview(self.destination_preview, destination_text)
        else:
            self._show_preview(self.destination_preview, "")

    # ------------------------------------------------------------------
    # BIN cache / backend validation
    # ------------------------------------------------------------------

    def _clear_cache(self):
        self._cache_key = None
        self._cache = None

    def get_bin_cache(self):
        if not self.require_bin_silent():
            raise RuntimeError("Please select a valid BIN first.")

        stat = os.stat(self.current_bin_path)
        key = (
            self.current_bin_path,
            stat.st_mtime_ns,
            stat.st_size,
            self.current_bin_type_key,
        )

        if self._cache_key == key and self._cache is not None:
            return self._cache

        data = Path(self.current_bin_path).read_bytes()

        if not data.startswith(b"GP-SPIF-HEADER"):
            raise RuntimeError(
                "Selected file does not look like a compatible GP-SPIF BIN."
            )

        if self.is_d3():
            sections = d3_backend.parse_sections(data)
            records = d3_backend.parse_animation_records(data, sections)
            groups = d3_backend.parse_groups(data, sections)
            membership = d3_backend.build_membership(groups)
            package_offset = d3_backend.SPRITE_PACKAGE_BASE
        else:
            sections = dark_backend.parse_sections(data)
            records = dark_backend.parse_animation_records(data, sections)
            groups = dark_backend.parse_groups(data, sections)
            membership = dark_backend.build_membership(groups)
            package_offset = dark_backend.SPRITE_PACKAGE_BASE

        _pkg_off, parsed, block = sprite_exporter.parse_package_at(
            data, package_offset
        )
        (
            _img_defs_off,
            _spr_defs_off,
            _palettes_off,
            chars_offset,
            images,
            sprites,
            palette_words,
        ) = parsed

        self._cache = {
            "data": data,
            "sections": sections,
            "records": records,
            "groups": groups,
            "membership": membership,
            "sprite": {
                "block": block,
                "chars_offset": chars_offset,
                "images": images,
                "sprites": sprites,
                "palette_words": palette_words,
            },
        }
        self._cache_key = key
        return self._cache

    def _find_source_matches(self, animation_id, source_image, source_subimage):
        cache = self.get_bin_cache()
        pkg = cache["sprite"]

        self._validate_sprite_identifier(pkg, source_image, source_subimage)

        if self.is_d3():
            group_text = self.d3_group_edit.text().strip()

            if group_text:
                try:
                    scene_group = int(group_text, 0)
                except Exception:
                    raise RuntimeError("D-3 scene group override must be an integer.")
            else:
                if animation_id not in d3_backend.KNOWN_EVO_SCENE_GROUPS:
                    raise RuntimeError(
                        f"D-3 animation {animation_id} has no automatic scene-group "
                        "mapping in replace_d3_evo_image.py. Enter a proven D-3 "
                        "scene group override."
                    )
                scene_group = d3_backend.KNOWN_EVO_SCENE_GROUPS[animation_id]

            if not 0 <= scene_group < len(cache["groups"]):
                raise RuntimeError(
                    f"D-3 scene group {scene_group} is outside "
                    f"0..{len(cache['groups']) - 1}."
                )

            matches = d3_backend.find_exact_matches(
                cache["groups"][scene_group],
                cache["records"],
                len(pkg["images"]),
                lambda image_index: self._sprite_subimage_count(pkg, image_index),
                source_image,
                source_subimage,
            )

            if not matches:
                raise RuntimeError(
                    f"{self.source_edit.text().strip()} is not referenced by "
                    f"{self.animation_name(animation_id)} ({animation_id}) "
                    f"in D-3 scene group {scene_group}."
                )

            return matches, f"D-3 scene group {scene_group}"

        traced, seeds = dark_backend.traced_groups_for_animation(
            animation_id,
            cache["records"],
            cache["groups"],
            cache["membership"],
            self.dark_hops_spin.value(),
        )

        matches = dark_backend.find_scene_matches(
            source_image,
            source_subimage,
            traced,
            cache["records"],
            cache["groups"],
        )

        if not matches:
            raise RuntimeError(
                f"{self.source_edit.text().strip()} is not referenced by "
                f"{self.animation_name(animation_id)} ({animation_id}) in D-Ark."
            )

        if len(matches) > 1 and not self.dark_all_matches_check.isChecked():
            raise RuntimeError(
                f"D-Ark found {len(matches)} exact source references. Enable "
                "'Replace all exact matches' before saving."
            )

        return matches, f"D-Ark seed group(s) {seeds}, traced {len(traced)} group(s)"

    def validate_current_edit(self, show_dialog=False):
        self._source_exists = False
        self._source_status = ""
        self._destination_status = ""
        self._match_offsets = []
        self._subimage_offsets = []

        if not self.require_bin_silent():
            self.source_validation_label.setText("Select a valid BIN first.")
            self.source_validation_label.setStyleSheet("color:#ff7777;")
            self.update_save_enabled()
            self.update_previews()
            return False

        animation_id = self.current_animation_id()
        source_text = self.source_edit.text().strip()
        destination_text = self.destination_edit.text().strip()

        source_error = None
        destination_error = None

        # Validate source independently so its preview can work even when the
        # destination field is still blank or invalid.
        if animation_id == 0:
            source_error = "Please choose an evolution animation other than '-'."
        elif not source_text:
            source_error = "Enter a source image, for example 338_0_0.png."
        else:
            try:
                source_image, source_subimage, _source_bank = self.parse_image_name(
                    source_text
                )
                matches, detail = self._find_source_matches(
                    animation_id, source_image, source_subimage
                )

                offsets = []
                sub_offsets = []
                for match in matches:
                    if self.is_d3():
                        ref = match["ref"]
                    else:
                        ref = match["hit"]
                    record = match["record"]

                    offsets.append(
                        record["start"] + ref["image_word_index"] * 2
                    )

                    sub_index = ref.get("subimage_word_index")
                    if sub_index is None:
                        sub_offsets.append(None)
                    else:
                        sub_offsets.append(record["start"] + sub_index * 2)

                self._match_offsets = offsets
                self._subimage_offsets = sub_offsets
                self._source_exists = True
                self._source_status = (
                    f"Source is valid: found {len(matches)} exact reference(s); {detail}."
                )
            except Exception as exc:
                source_error = str(exc)

        # Destination only has to exist in the sprite package.
        if not destination_text:
            destination_error = (
                "Enter a destination image, for example 487_0_0.png."
            )
        else:
            try:
                dest_image, dest_subimage, _dest_bank = self.parse_image_name(
                    destination_text
                )
                pkg = self.get_bin_cache()["sprite"]
                self._validate_sprite_identifier(pkg, dest_image, dest_subimage)

                if self._source_exists:
                    source_image, source_subimage, _source_bank = self.parse_image_name(
                        source_text
                    )
                    if (source_image, source_subimage) == (dest_image, dest_subimage):
                        destination_error = (
                            "Destination image/subimage is the same as the source. "
                            "Choose a different destination."
                        )
            except Exception as exc:
                destination_error = str(exc)

        self._source_status = source_error or self._source_status
        self._destination_status = destination_error or ""

        overall_valid = self._source_exists and not destination_error

        if overall_valid:
            self.source_validation_label.setText(self._source_status)
            self.source_validation_label.setStyleSheet("color:#7CFC90;")
            self.status_label.setText("Ready to save this evolution-image edit.")
        else:
            parts = []
            if source_error:
                parts.append(f"Source: {source_error}")
            elif self._source_status:
                parts.append(self._source_status)
            if destination_error:
                parts.append(f"Destination: {destination_error}")

            self.source_validation_label.setText("\n".join(parts))
            self.source_validation_label.setStyleSheet("color:#ff7777;")
            self.status_label.setText("Edit needs attention. Save is disabled.")

        self.update_save_enabled()
        self.update_previews()

        if show_dialog and not overall_valid:
            QtWidgets.QMessageBox.warning(
                self,
                "Evolution Image Validation",
                self.source_validation_label.text(),
            )

        return overall_valid

    def update_save_enabled(self):
        valid = bool(
            self.require_bin_silent()
            and self._source_exists
            and not self._destination_status
            and self.destination_edit.text().strip()
            and self.current_animation_id() != 0
        )
        self.save_edits_btn.setEnabled(valid)

    def clear_edit_form_after_success(self):
        # Reload the modified BIN and return the editor to a clean state.
        self.validation_timer.stop()

        # The previous cache represents the pre-save BIN. Drop it and parse the
        # just-written BIN once so the next edit starts from the current bytes.
        self._clear_cache()
        try:
            self.get_bin_cache()
        except Exception as exc:
            # Save itself already succeeded. Keep the editor clean; a later
            # Refresh/validation can surface a reload problem if necessary.
            print(
                f"[WARN] BIN was saved, but post-save cache refresh failed: {exc}"
            )

        self._loading_form = True
        try:
            if self.animation_combo.count() > 0:
                self.animation_combo.setCurrentIndex(0)

            self.source_edit.clear()
            self.destination_edit.clear()
            self.d3_group_edit.clear()
            self.dark_hops_spin.setValue(32)
            self.dark_all_matches_check.setChecked(True)
        finally:
            self._loading_form = False

        self._source_exists = False
        self._source_status = ""
        self._destination_status = ""
        self._match_offsets = []
        self._subimage_offsets = []

        self.clear_previews()

        self.source_validation_label.setText(
            "Select an evolution animation and enter source/destination images."
        )
        self.source_validation_label.setStyleSheet("")

        self.save_edits_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # CSV helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_offset_list(values):
        return ";".join(f"0x{x:08X}" for x in values)

    @staticmethod
    def _format_nullable_offset_list(values):
        return ";".join(
            "" if value is None else f"0x{value:08X}"
            for value in values
        )

    @staticmethod
    def _parse_offset_list(value):
        text = str(value or "").strip()
        if not text:
            return []
        return [
            int(part.strip(), 0)
            for part in text.split(";")
            if part.strip()
        ]

    @staticmethod
    def _parse_nullable_offset_list(value):
        text = str(value or "")
        if not text:
            return []
        result = []
        for part in text.split(";"):
            part = part.strip()
            result.append(None if not part else int(part, 0))
        return result

    def row_for_csv(self):
        row = self.current_row()
        row["match_offsets"] = self._format_offset_list(self._match_offsets)
        row["subimage_offsets"] = self._format_nullable_offset_list(
            self._subimage_offsets
        )
        row["d_ark_all_matches"] = (
            "1" if self.dark_all_matches_check.isChecked() else "0"
        )
        return row

    def read_csv_rows(self, path):
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))

    # def on_export_clicked(self):
    #     if not self.require_bin():
    #         return

    #     if not self.validate_current_edit(show_dialog=True):
    #         return

    #     path = self.export_csv_edit.text().strip()
    #     if not path:
    #         QtWidgets.QMessageBox.warning(
    #             self, "CSV path required", "Please specify an export CSV path."
    #         )
    #         return

    #     try:
    #         os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    #         with open(path, "w", encoding="utf-8-sig", newline="") as f:
    #             writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
    #             writer.writeheader()
    #             writer.writerow(self.row_for_csv())
    #     except Exception as exc:
    #         QtWidgets.QMessageBox.critical(
    #             self, "Export Evolution Images Error", str(exc)
    #         )
    #         return

    #     QtWidgets.QMessageBox.information(
    #         self,
    #         "Evolution Images Exported",
    #         f"The current single evolution-image edit was exported to:\n{path}",
    #     )
    #     self.status_label.setText("Current edit exported to CSV.")

    # def on_import_clicked(self):
    #     path, _ = QtWidgets.QFileDialog.getOpenFileName(
    #         self,
    #         "Select evolution_images.csv",
    #         "",
    #         "CSV files (*.csv);;All files (*)",
    #     )
    #     if not path:
    #         return

    #     try:
    #         rows = self.read_csv_rows(path)
    #         if len(rows) != 1:
    #             raise RuntimeError(
    #                 f"This tab edits one evolution animation at a time. "
    #                 f"The CSV must contain exactly 1 data row; found {len(rows)}."
    #             )
    #         self.apply_row_to_form(rows[0])
    #     except Exception as exc:
    #         QtWidgets.QMessageBox.critical(
    #             self,
    #             "Import Evolution Images Error",
    #             str(exc),
    #         )
    #         return

    #     self.status_label.setText(f"Imported edit from {path}.")

    # ------------------------------------------------------------------
    # Refresh / save
    # ------------------------------------------------------------------

    def on_refresh_clicked(self):
        if not self.require_bin():
            return

        self._clear_cache()
        try:
            self.get_bin_cache()
            self.validate_current_edit(show_dialog=True)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Refresh Error", str(exc))
            self.status_label.setText("Refresh failed.")

    def on_save_edits_clicked(self):
        if not self.require_bin():
            return

        if not self.validate_current_edit(show_dialog=True):
            return

        animation_id = self.current_animation_id()
        source_text = self.source_edit.text().strip()
        destination_text = self.destination_edit.text().strip()

        response = QtWidgets.QMessageBox.warning(
            self,
            "Save Evolution Image Edit to BIN?",
            "This will modify the selected BIN in place.\n\n"
            f"Animation: {self.animation_name(animation_id)} ({animation_id})\n"
            f"Source: {source_text}\n"
            f"Destination: {destination_text}\n\n"
            "Only animation image/subimage references are changed; the sprite "
            "package itself is not modified.\n\nContinue?",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
        )

        if response != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        src_path = Path(self.current_bin_path)
        original = src_path.read_bytes()
        data = bytearray(original)
        warnings = []

        try:
            source_image, source_subimage, source_bank = self.parse_image_name(
                source_text
            )
            dest_image, dest_subimage, dest_bank = self.parse_image_name(
                destination_text
            )

            # Re-find against the exact bytes about to be edited.
            if self.is_d3():
                sections = d3_backend.parse_sections(data)
                records = d3_backend.parse_animation_records(data, sections)
                groups = d3_backend.parse_groups(data, sections)

                group_text = self.d3_group_edit.text().strip()
                if group_text:
                    scene_group = int(group_text, 0)
                else:
                    if animation_id not in d3_backend.KNOWN_EVO_SCENE_GROUPS:
                        raise RuntimeError(
                            f"D-3 animation {animation_id} needs a proven scene-group override."
                        )
                    scene_group = d3_backend.KNOWN_EVO_SCENE_GROUPS[animation_id]

                num_images, sub_count_fn = d3_backend.parse_sprite_package(data)
                matches = d3_backend.find_exact_matches(
                    groups[scene_group],
                    records,
                    num_images,
                    sub_count_fn,
                    source_image,
                    source_subimage,
                )

                if not matches:
                    raise RuntimeError(
                        f"Source {source_text} is no longer present in "
                        f"D-3 animation {animation_id}."
                    )

                changes = d3_backend.patch_matches(
                    data, matches, dest_image, dest_subimage
                )
                package_base = d3_backend.SPRITE_PACKAGE_BASE
                payload = sections[d3_backend.ANIMATION_PAYLOAD_SECTION]

            else:
                sections = dark_backend.parse_sections(data)
                records = dark_backend.parse_animation_records(data, sections)
                groups = dark_backend.parse_groups(data, sections)
                membership = dark_backend.build_membership(groups)

                traced, _seeds = dark_backend.traced_groups_for_animation(
                    animation_id,
                    records,
                    groups,
                    membership,
                    self.dark_hops_spin.value(),
                )

                matches = dark_backend.find_scene_matches(
                    source_image,
                    source_subimage,
                    traced,
                    records,
                    groups,
                )

                if not matches:
                    raise RuntimeError(
                        f"Source {source_text} is no longer present in "
                        f"D-Ark animation {animation_id}."
                    )

                if (
                    len(matches) > 1
                    and not self.dark_all_matches_check.isChecked()
                ):
                    raise RuntimeError(
                        f"D-Ark found {len(matches)} exact matches. Enable "
                        "'Replace all exact matches' before saving."
                    )

                to_patch = (
                    matches
                    if self.dark_all_matches_check.isChecked()
                    else [matches[0]]
                )

                changes = []
                for match in to_patch:
                    one_changes, experimental_zero = dark_backend.patch_match(
                        data,
                        match,
                        dest_image,
                        dest_subimage,
                    )
                    changes.extend(one_changes)
                    if experimental_zero:
                        warnings.append(
                            "D-Ark kept explicit A001 encoding while changing "
                            "to subimage 0. This fixed-size encoding is experimental "
                            "and should be hardware-tested."
                        )

                package_base = dark_backend.SPRITE_PACKAGE_BASE
                payload = sections[dark_backend.ANIMATION_PAYLOAD_SECTION]

            if source_bank != dest_bank:
                warnings.append(
                    f"Palette-bank filename changed {source_bank} → {dest_bank}. "
                    "Animation commands do not encode palette bank, so no bank "
                    "field was changed."
                )

            # Hard safety checks.
            if data[package_base:] != original[package_base:]:
                raise RuntimeError(
                    "Safety check failed: sprite-package bytes would change. "
                    "No BIN was written."
                )

            diffs = [
                i
                for i, (a, b) in enumerate(zip(original, data))
                if a != b
            ]
            outside = [
                off
                for off in diffs
                if not (payload["start"] <= off < payload["end"])
            ]
            if outside:
                raise RuntimeError(
                    "Safety check failed: bytes outside the animation payload "
                    "would change: "
                    + ", ".join(f"0x{x:08X}" for x in outside[:20])
                )

            if self.is_d3():
                d3_backend.safe_write(src_path, src_path, data)
            else:
                dark_backend.safe_write(src_path, src_path, data)

        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "Save Evolution Image Edit Error",
                f"The BIN was not written.\n\n{exc}",
            )
            self.status_label.setText("Save failed.")
            self._clear_cache()
            self.validate_current_edit(show_dialog=False)
            return

        # Save succeeded. Refresh from the newly written BIN and clear the
        # editor instead of copying destination back into source/destination.
        self.clear_edit_form_after_success()

        message = (
            f"Evolution image edit was saved to:\n{self.current_bin_path}\n\n"
            f"Changed {len(changes)} animation field(s)."
        )

        if warnings:
            QtWidgets.QMessageBox.warning(
                self,
                "Evolution Image Saved with Warning",
                message + "\n\n" + "\n\n".join(sorted(set(warnings))),
            )
        else:
            QtWidgets.QMessageBox.information(
                self, "Evolution Image Saved", message
            )

        self.status_label.setText("Evolution image edit saved to BIN. Ready for another edit.")

    # ------------------------------------------------------------------
    # Reset to Original
    # ------------------------------------------------------------------

    def _select_original_row(self, rows):
        if not rows:
            raise RuntimeError("Original CSV contains no data rows.")

        animation_id = self.current_animation_id()

        # Prefer an exact current-animation match. This also lets a user keep
        # several historical rows in an original CSV without turning the GUI
        # itself into a multi-rule editor.
        matches = []
        for row in rows:
            try:
                row_id = int(str(row.get("evo_animation_id", "")).strip(), 0)
            except Exception:
                continue
            if row_id == animation_id:
                matches.append(row)

        if len(matches) == 1:
            return matches[0]

        if len(matches) > 1:
            raise RuntimeError(
                f"Original CSV contains more than one row for animation ID {animation_id}."
            )

        if len(rows) == 1:
            return rows[0]

        raise RuntimeError(
            f"Original CSV has {len(rows)} rows but none matches the currently "
            f"selected animation ID {animation_id}."
        )

    # def on_reset_clicked(self):
    #     if not self.require_bin():
    #         return

    #     original_csv = self.original_csv_path()
    #     if not os.path.isfile(original_csv):
    #         QtWidgets.QMessageBox.critical(
    #             self,
    #             "Missing file",
    #             f"{os.path.basename(original_csv)} was not found in the scripts folder.",
    #         )
    #         return

    #     try:
    #         rows = self.read_csv_rows(original_csv)
    #         row = self._select_original_row(rows)

    #         source_text = str(row.get("source_image", "")).strip()
    #         source_image, source_subimage, _source_bank = self.parse_image_name(
    #             source_text
    #         )

    #         offsets = self._parse_offset_list(row.get("match_offsets", ""))
    #         sub_offsets = self._parse_nullable_offset_list(
    #             row.get("subimage_offsets", "")
    #         )

    #         if not offsets:
    #             raise RuntimeError(
    #                 "The selected original CSV row has no match_offsets. "
    #                 "Export the row from a clean BIN after it validates, then save "
    #                 "that CSV as the *_evolution_images_original.csv file."
    #             )

    #         if sub_offsets and len(sub_offsets) != len(offsets):
    #             raise RuntimeError(
    #                 "subimage_offsets count does not match match_offsets."
    #             )
    #         if not sub_offsets:
    #             sub_offsets = [None] * len(offsets)

    #     except Exception as exc:
    #         QtWidgets.QMessageBox.critical(
    #             self, "Reset Evolution Images Error", str(exc)
    #         )
    #         return

    #     response = QtWidgets.QMessageBox.warning(
    #         self,
    #         "Reset to Original?",
    #         "This will restore the exact animation reference(s) recorded in:\n"
    #         f"{os.path.basename(original_csv)}\n\n"
    #         f"Animation ID: {row.get('evo_animation_id')}\n"
    #         f"Original source: {source_text}\n\n"
    #         "The selected BIN will be modified in place. Continue?",
    #         QtWidgets.QMessageBox.StandardButton.Yes
    #         | QtWidgets.QMessageBox.StandardButton.No,
    #     )

    #     if response != QtWidgets.QMessageBox.StandardButton.Yes:
    #         return

    #     src_path = Path(self.current_bin_path)
    #     original_bin = src_path.read_bytes()
    #     data = bytearray(original_bin)

    #     try:
    #         if self.is_d3():
    #             sections = d3_backend.parse_sections(data)
    #             package_base = d3_backend.SPRITE_PACKAGE_BASE
    #             payload = sections[d3_backend.ANIMATION_PAYLOAD_SECTION]
    #             num_images, sub_count_fn = d3_backend.parse_sprite_package(data)
    #         else:
    #             sections = dark_backend.parse_sections(data)
    #             package_base = dark_backend.SPRITE_PACKAGE_BASE
    #             payload = sections[dark_backend.ANIMATION_PAYLOAD_SECTION]
    #             num_images, sub_count_fn = dark_backend.parse_sprite_package(data)

    #         if not 0 <= source_image < num_images:
    #             raise RuntimeError(
    #                 f"Original source image {source_image} is invalid for this BIN."
    #             )
    #         if not 0 <= source_subimage < sub_count_fn(source_image):
    #             raise RuntimeError(
    #                 f"Original source subimage {source_subimage} is invalid "
    #                 f"for image {source_image}."
    #             )

    #         for image_off in offsets:
    #             if not (payload["start"] <= image_off <= payload["end"] - 2):
    #                 raise RuntimeError(
    #                     f"Image offset 0x{image_off:08X} is outside the animation payload."
    #                 )

    #         for sub_off in sub_offsets:
    #             if sub_off is not None and not (
    #                 payload["start"] <= sub_off <= payload["end"] - 2
    #             ):
    #                 raise RuntimeError(
    #                     f"Subimage offset 0x{sub_off:08X} is outside the animation payload."
    #                 )

    #         changed_fields = 0

    #         for image_off in offsets:
    #             old = struct.unpack_from("<H", data, image_off)[0]
    #             if old != source_image:
    #                 struct.pack_into("<H", data, image_off, source_image)
    #                 changed_fields += 1

    #         for sub_off in sub_offsets:
    #             if sub_off is None:
    #                 continue
    #             old = struct.unpack_from("<H", data, sub_off)[0]
    #             if old != source_subimage:
    #                 struct.pack_into("<H", data, sub_off, source_subimage)
    #                 changed_fields += 1

    #         if data[package_base:] != original_bin[package_base:]:
    #             raise RuntimeError(
    #                 "Safety check failed: Reset would modify the sprite package."
    #             )

    #         diffs = [
    #             i
    #             for i, (a, b) in enumerate(zip(original_bin, data))
    #             if a != b
    #         ]
    #         outside = [
    #             off
    #             for off in diffs
    #             if not (payload["start"] <= off < payload["end"])
    #         ]
    #         if outside:
    #             raise RuntimeError(
    #                 "Safety check failed: Reset would modify bytes outside "
    #                 "the animation payload."
    #             )

    #         if self.is_d3():
    #             d3_backend.safe_write(src_path, src_path, data)
    #         else:
    #             dark_backend.safe_write(src_path, src_path, data)

    #     except Exception as exc:
    #         QtWidgets.QMessageBox.critical(
    #             self, "Reset Evolution Images Error", str(exc)
    #         )
    #         self.status_label.setText("Reset failed.")
    #         return

    #     # Show the reset state in the form.
    #     reset_row = dict(row)
    #     reset_row["destination_image"] = source_text
    #     self._clear_cache()

    #     try:
    #         self.apply_row_to_form(reset_row)
    #     except Exception:
    #         # The bytes are already safely restored; do not convert a UI reload
    #         # issue into a false reset failure.
    #         pass

    #     QtWidgets.QMessageBox.information(
    #         self,
    #         "Evolution Images Reset",
    #         f"Restored {changed_fields} animation field(s) using:\n{original_csv}",
    #     )
    #     self.status_label.setText("Evolution image reset to original CSV state.")


if __name__ == "__main__":
    import sys

    app = QtWidgets.QApplication(sys.argv)
    w = ReplaceEvolutionImagesTab()
    w.resize(1100, 760)
    w.show()
    sys.exit(app.exec())
