"""Transfer Evolution Lines: immediate Save/Refresh, no queued replacements."""
import csv
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path
from PyQt5 import QtWidgets
import d3_evolution_core as core
import d3_transfer_evolution_lines as transfer
import d3_additional_partners as additional

ROOT=Path(__file__).resolve().parent
GREEN='QPushButton {background:#278447;color:white;padding:8px 24px;min-width:130px;border-radius:4px;} QPushButton:hover {background:#319c57;} QPushButton:disabled {background:#505950;color:#a0a0a0;}'


def read_names(path):
    script=ROOT/'export_d3_names.py';mapping=ROOT/'replace_map.csv'
    if not script.is_file() or not mapping.is_file():
        raise RuntimeError('export_d3_names.py and replace_map.csv are required in the GUI scripts folder.')
    with tempfile.TemporaryDirectory(prefix='d3_transfer_names_') as tmp:
        target=Path(tmp)/'names.csv'
        run=subprocess.run([sys.executable,str(script),str(path),str(mapping),str(target)],capture_output=True,text=True,cwd=str(ROOT))
        if run.returncode:raise RuntimeError((run.stderr or run.stdout).strip())
        with target.open(encoding='utf-8-sig',newline='') as f:
            return {int(row['string_index'],0):row['name'].strip() for row in csv.DictReader(f)}


class TransferEvolutionLinesTab(QtWidgets.QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.path=None;self.data=None;self.digest=None;self.mappings={};self.names={};self.lines={};self.loading=False
        layout=QtWidgets.QVBoxLayout(self)
        row=QtWidgets.QHBoxLayout();self.path_edit=QtWidgets.QLineEdit();self.path_edit.setReadOnly(True)
        browse=QtWidgets.QPushButton('Select D-3 .bin file…');browse.clicked.connect(self.browse)
        row.addWidget(QtWidgets.QLabel('D-3 BIN:'));row.addWidget(self.path_edit,1);row.addWidget(browse);layout.addLayout(row)
        text=QtWidgets.QLabel('Add overflow pages to a source line’s map-battle selector. Move past either end to switch pages. Digivolution Viewer membership stays the same.');text.setWordWrap(True);layout.addWidget(text)
        form=QtWidgets.QFormLayout();self.source=QtWidgets.QComboBox();self.donor=QtWidgets.QComboBox()
        form.addRow('Source line (receives extra choices):',self.source)
        form.addRow('Donor line (provides extra choices):',self.donor);layout.addLayout(form)
        note=QtWidgets.QLabel('The donor’s stage-0 entries and first non-stage-0 Digimon are excluded. Forms already on the source page are omitted. Existing unlock requirements still apply.');note.setWordWrap(True);layout.addWidget(note)
        self.preview=QtWidgets.QTableWidget(0,2);self.preview.setHorizontalHeaderLabels(['Page 1 — source choices','Page 2 — additional choices'])
        self.preview.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.preview.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers);layout.addWidget(self.preview,1)
        self.saved=QtWidgets.QLabel();saved_font=self.saved.font();saved_font.setBold(True);self.saved.setFont(saved_font);self.saved.setWordWrap(True);layout.addWidget(self.saved)
        actions=QtWidgets.QHBoxLayout();self.save_button=QtWidgets.QPushButton('Save');self.refresh_button=QtWidgets.QPushButton('Refresh')
        for button in (self.save_button,self.refresh_button):button.setStyleSheet(GREEN);actions.addWidget(button)
        actions.addStretch();layout.addLayout(actions)
        self.status=QtWidgets.QLabel('Select a compatible D-3 BIN to begin.');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.save_button.setEnabled(False)
        self.source.currentIndexChanged.connect(self.source_changed);self.donor.currentIndexChanged.connect(self.update_preview)
        self.save_button.clicked.connect(self.save);self.refresh_button.clicked.connect(self.refresh)

    def browse(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self,'Select D-3 BIN','','BIN files (*.bin);;All files (*)')
        if path:self.path=Path(path);self.path_edit.setText(path);self.refresh()

    def name(self,record):
        index=self.records[record].words[5]
        return f'{self.names.get(index, "Unnamed")}  [record {record}]'

    def line_label(self,line):
        active=[i for i in self.lines[line] if self.records[i].stage>0]
        name=self.names.get(self.records[active[0]].words[5],core.LINE_NAMES[line]) if active else core.LINE_NAMES[line]
        return f'{name} (line {line})'

    def refresh(self):
        if self.path is None:return
        selected=self.source.currentData()
        self.save_button.setEnabled(False);self.data=None;self.loading=True
        try:
            data=self.path.read_bytes();mappings=transfer.read_state(data)
            normalized=bytearray(data)
            if transfer.is_probe_c(normalized):transfer.strip_probe_c(normalized)
            transfer.check_layout(normalized)
            self.lines=core.read_source_lines(normalized)[0]
            self.records=core.read_partner_records(normalized)
            self.extra_entries=additional.read_state(data)['entries']
            self.records.extend(core.PartnerRecord(r['key'],tuple(r['words'])) for r in self.extra_entries)
            self.names=read_names(self.path)
            self.data=data;self.digest=hashlib.sha256(data).hexdigest();self.mappings=mappings
            self.source.clear()
            for line in range(7):self.source.addItem(self.line_label(line),line)
            self.source.setCurrentIndex(self.source.findData(selected if selected in range(7) else 1))
            self.saved.setText('Saved transfers: '+('; '.join(f'{self.line_label(a)} → {self.line_label(b)}' for a,b in sorted(mappings.items())) or 'None'))
        except Exception as ex:
            self.source.clear();self.donor.clear();self.preview.setRowCount(0);self.saved.clear();self.status.setText(f'Could not load: {ex}')
        finally:self.loading=False
        if self.data is not None:self.source_changed()

    def source_changed(self,*_):
        if self.loading or self.data is None:return
        self.loading=True;source=self.source.currentData();self.donor.clear();self.donor.addItem('No donor line — keep added partners',None)
        for line in range(7):
            if line!=source:self.donor.addItem(self.line_label(line),line)
        target=self.mappings.get(source);index=self.donor.findData(target);self.donor.setCurrentIndex(max(index,0));self.loading=False;self.update_preview()

    def update_preview(self,*_):
        if self.loading or self.data is None:return
        source=self.source.currentData();donor=self.donor.currentData();self.save_button.setEnabled(False)
        host=[i for i in self.lines[source] if self.records[i].stage>0];choices=[host]
        try:
            mappings=dict(self.mappings)
            if donor is None:mappings.pop(source,None)
            else:mappings[source]=donor
            slots=additional.read_state(self.data).get('slots')
            pages=additional.derive_pages(self.data,self.extra_entries,mappings,self.lines,slots)
            if source in pages:choices=pages[source]
            self.status.setText(f'{len(choices)} battle page(s). Save applies this source’s setting immediately.')
            self.save_button.setEnabled(True)
        except Exception as ex:self.status.setText(str(ex))
        self.preview.setColumnCount(len(choices))
        self.preview.setHorizontalHeaderLabels([f'Battle page {i+1}' for i in range(len(choices))])
        self.preview.setRowCount(max(map(len,choices)))
        for row in range(self.preview.rowCount()):
            for col,values in enumerate(choices):
                self.preview.setItem(row,col,QtWidgets.QTableWidgetItem(self.name(values[row]) if row<len(values) else ''))

    def save(self):
        if self.path is None or self.data is None:return
        try:
            transfer.save_transfer(self.path,self.source.currentData(),self.donor.currentData(),self.digest)
            self.refresh()
            if self.data is not None:self.status.setText('Saved and refreshed. The BIN contains the transfer settings; no .bak file was created.')
        except Exception as ex:
            QtWidgets.QMessageBox.warning(self,'Transfer Evolution Lines',str(ex))
            self.status.setText(f'Not saved: {ex}')
