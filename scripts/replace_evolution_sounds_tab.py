"""D3 evolution sound references: queue several isolated edits, then save."""
import csv
from pathlib import Path
from PyQt5 import QtCore, QtWidgets
from common import SCRIPT_DIR
import replace_d3_evo_sound as backend
import d3_evo_sound_batch as batch

class Job(QtCore.QThread):
    done=QtCore.pyqtSignal(object)
    failed=QtCore.pyqtSignal(str)
    def __init__(self,fn,parent=None):super().__init__(parent);self.fn=fn
    def run(self):
        try:self.done.emit(self.fn())
        except Exception as exc:self.failed.emit(str(exc))

class ReplaceEvolutionSoundsTab(QtWidgets.QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.path=None;self.snapshot=None;self.edits=[];self.rows=[];self.job=None
        self.names={};self.evo_names={}
        main=QtWidgets.QVBoxLayout(self)
        self.controls=QtWidgets.QWidget();main.addWidget(self.controls)
        layout=QtWidgets.QVBoxLayout(self.controls);layout.setContentsMargins(0,0,0,0)
        line=QtWidgets.QHBoxLayout();layout.addLayout(line)
        line.addWidget(QtWidgets.QLabel('D-3 25th Color — selected BIN (input and output):'))
        self.path_edit=QtWidgets.QLineEdit();self.path_edit.setReadOnly(True);line.addWidget(self.path_edit,1)
        self.browse=QtWidgets.QPushButton('Select .bin file…');line.addWidget(self.browse)
        self.refresh=QtWidgets.QPushButton('Refresh');self.refresh.setToolTip('Reload the BIN and clear queued edits.');line.addWidget(self.refresh)
        row=QtWidgets.QHBoxLayout();layout.addLayout(row)
        row.addWidget(QtWidgets.QLabel('Evolution:'));self.evo=QtWidgets.QComboBox();row.addWidget(self.evo,1)
        self.table=QtWidgets.QTableWidget(0,4);self.table.setHorizontalHeaderLabels(['Occurrence','Sound chunk','Sound name','Isolation'])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(2,QtWidgets.QHeaderView.Stretch)
        for column in (0,1,3):self.table.horizontalHeader().setSectionResizeMode(column,QtWidgets.QHeaderView.ResizeToContents)
        self.table.verticalHeader().hide()
        layout.addWidget(self.table,2)
        row=QtWidgets.QHBoxLayout();layout.addLayout(row)
        row.addWidget(QtWidgets.QLabel('Replace selected occurrence with:'))
        self.destination=QtWidgets.QComboBox();self.destination.setEditable(True)
        self.destination.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        row.addWidget(self.destination,1)
        self.add=QtWidgets.QPushButton('Queue Replacement');row.addWidget(self.add)
        layout.addWidget(QtWidgets.QLabel('Queued edits — changes across several evolutions are saved together:'))
        self.queue=QtWidgets.QTableWidget(0,4);self.queue.setHorizontalHeaderLabels(['Evolution','Occurrence','Source','Replacement'])
        self.queue.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.queue.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.queue.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.queue.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.queue.horizontalHeader().setSectionResizeMode(1,QtWidgets.QHeaderView.ResizeToContents)
        self.queue.verticalHeader().hide()
        layout.addWidget(self.queue,1)
        row=QtWidgets.QHBoxLayout();layout.addLayout(row)
        self.remove=QtWidgets.QPushButton('Remove Selected');row.addWidget(self.remove)
        self.clear=QtWidgets.QPushButton('Clear Queue');row.addWidget(self.clear);row.addStretch()
        self.save=QtWidgets.QPushButton('Save Queued Edits to BIN');row.addWidget(self.save)
        note=QtWidgets.QLabel('A backup is created before saving. Audio data is preserved. Same-root aliases share edits; shared records across different roots are blocked.')
        note.setWordWrap(True);layout.addWidget(note)
        self.status=QtWidgets.QLabel('Select a D-3 BIN to begin.');self.status.setWordWrap(True);main.addWidget(self.status)
        try:
            self.names=backend.load_sound_map(Path(SCRIPT_DIR)/'d3_sound_map.csv')
            with (Path(SCRIPT_DIR)/'d3_evo_animation_map.csv').open(encoding='utf-8-sig',newline='') as f:
                self.evo_names={int(r['value']):r['key'] for r in csv.DictReader(f) if int(r['value'])!=0}
            self.evo.addItem('Select evolution…',None)
            for i,name in sorted(self.evo_names.items(),key=lambda p:p[1]):self.evo.addItem(f'{name} ({i})',i)
            self.destination.addItem('Select replacement sound…',None)
            for i,name in sorted(self.names.items()):self.destination.addItem(f'chunk_{i:04}.a18.wav — {name}',i)
            self.destination.completer().setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
            self.destination.completer().setCaseSensitivity(QtCore.Qt.CaseInsensitive)
            self.destination.completer().setFilterMode(QtCore.Qt.MatchContains)
        except Exception as exc:
            self.status.setText('Could not load sound/evolution maps: '+str(exc));self.controls.setEnabled(False)
        self.browse.clicked.connect(self.choose_bin);self.refresh.clicked.connect(self.reload)
        self.evo.currentIndexChanged.connect(self.show_sounds)
        self.table.itemSelectionChanged.connect(self.update_buttons)
        self.destination.currentTextChanged.connect(self.update_buttons)
        self.add.clicked.connect(self.queue_edit);self.remove.clicked.connect(self.remove_edit)
        self.clear.clicked.connect(self.clear_queue);self.save.clicked.connect(self.save_edits)
        self.update_buttons()
        if parent is not None:parent.window().installEventFilter(self)

    def eventFilter(self,watched,event):
        if event.type()==QtCore.QEvent.Close and self.job is not None:
            event.ignore();self.status.setText("Please wait for the sound operation to finish before closing.");return True
        return super().eventFilter(watched,event)

    def closeEvent(self,event):
        if self.job is not None:event.ignore()
        else:super().closeEvent(event)

    def run_job(self,fn,success,message):
        if self.job is not None:return
        self.controls.setEnabled(False);self.status.setText(message)
        job=Job(fn,self);self.job=job
        job.done.connect(success);job.failed.connect(self.error)
        job.finished.connect(self.job_finished);job.start()

    def job_finished(self):
        job=self.job;self.job=None
        if job:job.deleteLater()
        self.controls.setEnabled(bool(self.names));self.update_buttons()

    def error(self,message):
        self.status.setText(message)
        QtWidgets.QMessageBox.warning(self,'Evolution sound replacement',message)

    def choose_bin(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self,'Select D-3 BIN',self.path or '', 'BIN files (*.bin);;All files (*)')
        if path:self.load_path(path)

    def load_path(self,path):
        self.path=str(Path(path).resolve());self.path_edit.setText(self.path)
        self.snapshot=None;self.clear_queue();self.show_sounds()
        self.run_job(lambda:batch.load(self.path,self.names),self.loaded,'Reading animation and sound references…')

    def reload(self):
        if self.path:self.load_path(self.path)

    def loaded(self,result):
        self.snapshot=result;self.show_sounds();self.status.setText('BIN loaded. Select an evolution and sound occurrence.')

    def show_sounds(self):
        self.rows=[];self.table.setRowCount(0)
        evo=self.evo.currentData()
        if self.snapshot is not None and evo is not None:
            try:
                archive=self.snapshot['archive'];self.rows=archive.sounds(evo)
                self.table.setRowCount(len(self.rows))
                for i,r in enumerate(self.rows):
                    exclusive=r['root_owners']==[archive.roots[evo]]
                    vals=[str(r['occurrence']),f"chunk_{r['chunk']:04}.a18.wav",self.names.get(r['chunk'],'Unknown sound'), 'Selected root only' if exclusive else 'Shared — blocked']
                    for j,v in enumerate(vals):
                        item=QtWidgets.QTableWidgetItem(v);item.setToolTip(v);self.table.setItem(i,j,item)
                aliases=sorted(e for e,r in archive.roots.items() if r==archive.roots[evo])
                self.status.setText(f'{len(self.rows)} sound reference(s). Same-root layout aliases: {aliases}. Select one occurrence to replace.')
            except ValueError as exc:self.status.setText(str(exc))
        self.update_buttons()

    def destination_id(self):
        text=self.destination.currentText().strip()
        idx=self.destination.currentIndex()
        if idx>=0 and text==self.destination.itemText(idx):
            value=self.destination.itemData(idx)
            if value is None:raise ValueError('Choose a replacement sound.')
            return value
        return backend.sound_id(text,self.names)

    def update_buttons(self,*args):
        valid=self.snapshot is not None and 0<=self.table.currentRow()<len(self.rows)
        if valid:
            r=self.rows[self.table.currentRow()];evo=self.evo.currentData()
            try:dest=self.destination_id();valid=dest!=r['chunk'] and r['root_owners']==[self.snapshot['archive'].roots[evo]]
            except ValueError:valid=False
        self.add.setEnabled(valid)
        self.save.setEnabled(self.snapshot is not None and bool(self.edits))
        self.refresh.setEnabled(self.path is not None)
        self.remove.setEnabled(bool(self.edits));self.clear.setEnabled(bool(self.edits))

    def queue_edit(self):
        try:
            r=self.rows[self.table.currentRow()];evo=self.evo.currentData();dest=self.destination_id()
            if dest==r['chunk']:raise ValueError('Choose a different replacement sound.')
            if r['root_owners']!=[self.snapshot['archive'].roots[evo]]:raise ValueError('This sound command is shared by different roots.')
            if any(e['offset']==r['offset'] for e in self.edits):raise ValueError('This sound occurrence is already queued. Remove it first to change the replacement.')
            self.edits.append({'evo':evo,'occurrence':r['occurrence'],'source':r['chunk'],'destination':dest,'offset':r['offset']})
            self.render_queue();self.destination.setCurrentIndex(0);self.table.clearSelection()
            self.status.setText(f'{len(self.edits)} replacement(s) queued. Choose another evolution or save the batch.')
        except (ValueError,IndexError) as exc:self.error(str(exc))

    def render_queue(self):
        self.queue.setRowCount(len(self.edits))
        for i,e in enumerate(self.edits):
            vals=[f"{self.evo_names.get(e['evo'],e['evo'])} ({e['evo']})",str(e['occurrence']),f"{e['source']:04} — {self.names[e['source']]}",f"{e['destination']:04} — {self.names[e['destination']]}"]
            for j,v in enumerate(vals):
                item=QtWidgets.QTableWidgetItem(v);item.setToolTip(v);self.queue.setItem(i,j,item)
        self.update_buttons()

    def remove_edit(self):
        i=self.queue.currentRow()
        if 0<=i<len(self.edits):self.edits.pop(i);self.render_queue()

    def clear_queue(self):self.edits=[];self.render_queue()

    def save_edits(self):
        if not self.snapshot or not self.edits:return
        path=self.path;snapshot=self.snapshot;edits=[dict(e) for e in self.edits]
        self.run_job(lambda:batch.save(path,snapshot,edits,self.names),self.saved,'Validating and saving queued sound replacements…')

    def saved(self,result):
        count=len(self.edits);self.snapshot=result;self.clear_queue();self.destination.setCurrentIndex(0)
        self.show_sounds();self.table.clearSelection()
        self.status.setText(f"Saved {count} replacement(s), changing {len(result['changed'])} byte(s). Backup: {result['backup']}")
