from PyQt5 import QtCore, QtGui, QtWidgets
import os
import tempfile
from PIL import Image
import imagequant

class ReduceColorCountTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.input_path = None
        self.output_image = None
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        top = QtWidgets.QHBoxLayout()
        self.path_edit = QtWidgets.QLineEdit()
        self.path_edit.setReadOnly(True)

        self.browse_btn = QtWidgets.QPushButton("Upload Image...")
        self.colors_spin = QtWidgets.QSpinBox()
        self.colors_spin.setMinimum(2)
        self.colors_spin.setMaximum(256)
        self.colors_spin.setValue(64)

        self.reduce_btn = QtWidgets.QPushButton("Reduce Color Count")
        self.reduce_btn.setStyleSheet("background-color:#0006b1;color:white;font-weight:600;font-size:14pt;")

        self.save_btn = QtWidgets.QPushButton("Save")
        self.save_btn.setEnabled(False)
        self.save_btn.setStyleSheet("background-color:#008000;color:white;font-weight:600;font-size:14pt;")

        top.addWidget(QtWidgets.QLabel("Image:"))
        top.addWidget(self.path_edit)
        top.addWidget(self.browse_btn)
        top.addWidget(QtWidgets.QLabel("Colors:"))
        top.addWidget(self.colors_spin)
        top.addWidget(self.reduce_btn)
        top.addWidget(self.save_btn)

        layout.addLayout(top)

        previews = QtWidgets.QHBoxLayout()

        before_box = QtWidgets.QGroupBox("Before")
        before_layout = QtWidgets.QVBoxLayout(before_box)
        self.before_label = QtWidgets.QLabel("No image selected")
        self.before_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        before_layout.addWidget(self.before_label)

        after_box = QtWidgets.QGroupBox("After")
        after_layout = QtWidgets.QVBoxLayout(after_box)
        self.after_label = QtWidgets.QLabel("No reduced image yet")
        self.after_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        after_layout.addWidget(self.after_label)

        previews.addWidget(before_box)
        previews.addWidget(after_box)

        layout.addLayout(previews, 1)

        self.status_label = QtWidgets.QLabel("Ready.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.browse_btn.clicked.connect(self.on_browse)
        self.reduce_btn.clicked.connect(self.on_reduce)
        self.save_btn.clicked.connect(self.on_save)

    def reduce_colors_to_image(self, input_png, colors):
        img = Image.open(input_png).convert("RGBA")
        w, h = img.size
        rgba = img.tobytes()

        result = imagequant.quantize_raw_rgba_bytes(
            rgba,
            w,
            h,
            dithering_level=0.0,
            max_colors=int(colors),
        )

        index_bytes = result[0]
        palette = result[1]

        if len(palette) > 0 and isinstance(palette[0], int):
            flat = list(palette)
            if len(flat) % 4 == 0 and len(flat) // 4 <= 256:
                flat_palette = []
                for i in range(0, len(flat), 4):
                    flat_palette.extend(flat[i:i + 3])
            else:
                flat_palette = flat
        else:
            flat_palette = []
            for color in palette:
                flat_palette.extend([color[0], color[1], color[2]])

        flat_palette = flat_palette[:768]
        flat_palette += [0] * (768 - len(flat_palette))

        out = Image.frombytes("P", (w, h), index_bytes)
        out.putpalette(flat_palette)

        # IMPORTANT:
        # Convert back to RGBA and restore the original alpha channel.
        # Without this, transparent pixels can become green/opaque.
        out_rgba = out.convert("RGBA")
        out_rgba.putalpha(img.getchannel("A"))

        return out_rgba

    def show_pixmap(self, label, path):
        pix = QtGui.QPixmap(path)
        if pix.isNull():
            label.setText("Preview failed")
            return

        pix = pix.scaled(
            160,
            160,
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        label.setPixmap(pix)

    def on_browse(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select image",
            "",
            "PNG images (*.png);;All images (*.png *.jpg *.jpeg *.bmp);;All files (*)",
        )
        if not path:
            return

        self.input_path = path
        self.path_edit.setText(path)
        self.output_image = None
        self.save_btn.setEnabled(False)

        self.show_pixmap(self.before_label, path)
        self.after_label.setText("Click Reduce Color Count")
        self.status_label.setText("Image loaded.")

    def on_reduce(self):
        if not self.input_path or not os.path.isfile(self.input_path):
            QtWidgets.QMessageBox.warning(self, "Image required", "Please upload/select an image first.")
            return

        try:
            self.output_image = self.reduce_colors_to_image(
                self.input_path,
                self.colors_spin.value(),
            )

            tmp_path = os.path.join(tempfile.gettempdir(), "digimon_tool_reduced_preview.png")
            self.output_image.save(tmp_path, "PNG", optimize=False)

            self.show_pixmap(self.after_label, tmp_path)
            self.save_btn.setEnabled(True)
            self.status_label.setText(
                f"Reduced preview generated with max {self.colors_spin.value()} colors."
            )

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Reduce Color Count Error", str(e))
            self.status_label.setText("Reduce color count failed.")

    def on_save(self):
        if self.output_image is None:
            QtWidgets.QMessageBox.warning(self, "Nothing to save", "Please reduce the color count first.")
            return

        default_name = "reduced.png"
        if self.input_path:
            base = os.path.splitext(os.path.basename(self.input_path))[0]
            default_name = f"{base}.png"

        out_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save reduced image",
            default_name,
            "PNG images (*.png);;All files (*)",
        )
        if not out_path:
            return

        try:
            self.output_image.save(out_path, "PNG", optimize=False)
            QtWidgets.QMessageBox.information(self, "Saved", f"Saved:\n{out_path}")
            self.status_label.setText(f"Saved: {out_path}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Save Error", str(e))