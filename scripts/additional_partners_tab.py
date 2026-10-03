"""Add Friend forms to selected D-3 lines, with immediate Save and Refresh."""
import csv,struct
from pathlib import Path
from PyQt5 import QtWidgets
import d3_additional_partners as backend
import d3_evolution_core as core
from transfer_evolution_lines_tab import GREEN,read_names
ROOT=Path(__file__).resolve().parent
LABELS=['Stage','Shared Partner digimon_id','Line membership','Win requirement','Sprite','Name','Evolution animation 1','Evolution animation 2','Evolution animation 3','Evolution animation 4','Evolution animation 5','Battle BGM','Attack voice','Projectile sprite','Projectile sound','Special unlock (0/1)']
MAPS={4:'d3_sprite_map.csv',6:'d3_evo_animation_map.csv',7:'d3_evo_animation_map.csv',8:'d3_evo_animation_map.csv',9:'d3_evo_animation_map.csv',10:'d3_evo_animation_map.csv',11:'d3_background_music_during_battle_id_map.csv',12:'d3_attack_voice_sound_id_map.csv',14:'d3_attack_shot_sound_id_map.csv'}
def read_map(name):
 result={};path=ROOT/name
 if path.is_file():
  with path.open(encoding='utf-8-sig',newline='') as f:
   for row in csv.DictReader(f):
    try:result[int(row['value'],0)]=row['key'].strip()
    except (ValueError,TypeError,KeyError):continue
 return result

class AdditionalPartnersTab(QtWidgets.QWidget):
 def __init__(self,parent=None):
  super().__init__(parent);self.path=None;self.data=None;self.state=None;self.digest=None;self.names={};self.maps={};self.loading=False;self.selected=None;self.clean=None
  layout=QtWidgets.QVBoxLayout(self)
  row=QtWidgets.QHBoxLayout();self.path_edit=QtWidgets.QLineEdit();self.path_edit.setReadOnly(True);browse=QtWidgets.QPushButton('Select D-3 BIN…');browse.clicked.connect(self.browse)
  row.addWidget(QtWidgets.QLabel('D-3 BIN:'));row.addWidget(self.path_edit,1);row.addWidget(browse);layout.addLayout(row)
  note=QtWidgets.QLabel('Choose a destination line, Friend assets and Partner settings; the form fills automatically. Save adds a new partner unless you select an existing added partner to edit. A successful Save resets the form for the next addition. Added partners appear on overflow battle pages and in the destination’s Digivolution Viewer section. Lines may exceed nine active Digimon. This patch reserves storage for 16 added records across all lines.');note.setWordWrap(True);layout.addWidget(note)
  self.record_selector=QtWidgets.QComboBox();layout.addWidget(self.record_selector);self.record_selector.currentIndexChanged.connect(self.select_record)
  self.editor=QtWidgets.QWidget();el=QtWidgets.QVBoxLayout(self.editor)
  setup=QtWidgets.QGroupBox('Destination and source');grid=QtWidgets.QGridLayout(setup)
  self.destination=QtWidgets.QComboBox();self.friend=QtWidgets.QComboBox();self.template=QtWidgets.QComboBox()
  grid.addWidget(QtWidgets.QLabel('Destination line:'),0,0);grid.addWidget(self.destination,0,1,1,2)
  grid.addWidget(QtWidgets.QLabel('Friend assets:'),1,0);grid.addWidget(self.friend,1,1,1,2)
  grid.addWidget(QtWidgets.QLabel('Partner settings:'),2,0);grid.addWidget(self.template,2,1,1,2)
  desc=QtWidgets.QLabel('Friend supplies the name, sprite and projectile fields. Partner supplies stage, shared ID, evolution animations, BGM, voice and requirements. These copied settings can be edited below.');desc.setWordWrap(True);grid.addWidget(desc,4,0,1,3);el.addWidget(setup)
  columns=QtWidgets.QHBoxLayout();self.fields={}
  left=QtWidgets.QGroupBox('Identity and battle');right=QtWidgets.QGroupBox('Evolution and audio');lf=QtWidgets.QFormLayout(left);rf=QtWidgets.QFormLayout(right)
  for i in [0,1,2,3,4,5,13,15,6,7,8,9,10,11,12,14]:
   if i==2:w=QtWidgets.QLabel('Set by destination line')
   elif i in MAPS or i==5:
    w=QtWidgets.QComboBox();w.setEditable(True);w.setInsertPolicy(QtWidgets.QComboBox.NoInsert);w.setMinimumWidth(220);w.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon);w.setMinimumContentsLength(22)
   else:
    w=QtWidgets.QSpinBox();w.setRange(0,65535)
    if i==0:w.setRange(1,5)
    if i==1:w.setRange(0,37)
    if i==15:w.setRange(0,1)
   self.fields[i]=w;(lf if i in (0,1,2,3,4,5,13,15) else rf).addRow(LABELS[i]+':',w)
  columns.addWidget(left);columns.addWidget(right);el.addLayout(columns)
  explanation=QtWidgets.QLabel('Shared ID reuses an existing Partner’s unlock/Link identity. Additional partners have separate assets and settings, but are not new independent Link Battle identities. Evolution animations are copied, not generated for the chosen Friend.');explanation.setWordWrap(True);el.addWidget(explanation)
  self.preview=QtWidgets.QLabel();self.preview.setWordWrap(True);el.addWidget(self.preview)
  scroll=QtWidgets.QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(self.editor);layout.addWidget(scroll,1)
  actions=QtWidgets.QHBoxLayout();self.save_button=QtWidgets.QPushButton('Save');self.refresh_button=QtWidgets.QPushButton('Refresh')
  for b in (self.save_button,self.refresh_button):b.setStyleSheet(GREEN);actions.addWidget(b)
  self.remove_button=QtWidgets.QPushButton('Remove selected partner');actions.addWidget(self.remove_button);actions.addStretch()
  self.remove_button.setStyleSheet('QPushButton {background:#FF0000;color:white;padding:8px 24px;min-width:130px;border-radius:4px;} QPushButton:hover {background:#8B0000;} QPushButton:disabled {background:#505950;color:#a0a0a0;}')
  self.export_button=QtWidgets.QPushButton('Export selected CSV');self.import_button=QtWidgets.QPushButton('Import selected CSV');actions.addWidget(self.export_button);actions.addWidget(self.import_button);layout.addLayout(actions)
  self.status=QtWidgets.QLabel('Select a compatible D-3 BIN. Save edits that file directly; no queue or .bak files.');self.status.setWordWrap(True);layout.addWidget(self.status)
  self.save_button.clicked.connect(self.save);self.refresh_button.clicked.connect(self.refresh);self.remove_button.clicked.connect(self.remove)
  self.export_button.clicked.connect(self.export_csv);self.import_button.clicked.connect(self.import_csv);self.destination.currentIndexChanged.connect(self.destination_changed)
  self.friend.currentIndexChanged.connect(self.copy_friend);self.template.currentIndexChanged.connect(self.copy_template)
  self.set_loaded(False)

 def set_loaded(self,value):
  self.editor.setEnabled(value);self.record_selector.setEnabled(value)
  for b in (self.save_button,self.export_button,self.import_button):b.setEnabled(value)
  self.remove_button.setEnabled(value and self.selected is not None)
  if value:self.update_preview()
 def browse(self):
  path,_=QtWidgets.QFileDialog.getOpenFileName(self,'Select D-3 BIN','','BIN files (*.bin);;All files (*)')
  if path:self.path=Path(path);self.selected=None;self.path_edit.setText(path);self.refresh()
 def line_label(self,line):
  active=[r for r in self.lines[line] if self.physical[r].active]
  name=self.names.get(self.physical[active[0]].words[5],core.LINE_NAMES[line]) if active else core.LINE_NAMES[line]
  return f'{name} (line {line})'
 def refresh(self):
  if self.path is None:return
  previous_line=self.destination.currentData();self.set_loaded(False);self.data=None;self.state=None;self.loading=True
  try:
   data=self.path.read_bytes();state=backend.read_state(data);warning=''
   try:self.names=read_names(self.path)
   except Exception as e:self.names={};warning=' Names unavailable; numeric IDs shown. '+str(e)
   self.data=data;self.state=state;self.digest=backend.digest(data);self.lines=core.read_source_lines(data)[0];self.physical=core.read_partner_records(data)
   self.maps={i:read_map(n) for i,n in MAPS.items()};self.maps[5]=self.names
   self.destination.clear()
   for line in range(7):self.destination.addItem(self.line_label(line),line)
   self.destination.setCurrentIndex(previous_line if previous_line in range(7) else 1)
   self.friend.clear();friends=[]
   for i in range(91):
    row=struct.unpack_from('<6H',data,0x9DEEA+12*i);friends.append((self.names.get(row[1],f'Name {row[1]}'),i))
   for name,i in sorted(friends,key=lambda item:(item[0].casefold(),item[1])):self.friend.addItem(f'{name} [Friend {i}]',i)
   self.friend.setCurrentIndex(self.friend.findData(9));self.template.clear()
   for r in self.physical:
    if r.active:self.template.addItem(f'{self.names.get(r.words[5],"Name "+str(r.words[5]))} [Partner {r.record_index}]',r.record_index)
   self.record_selector.clear();self.record_selector.addItem('--- Select an added partner to edit ----',None)
   for r in state['entries']:self.record_selector.addItem(f'{self.names.get(r["words"][5],"Name "+str(r["words"][5]))} — {self.line_label(r["line"])} [extra_{r["key"]-37}]',r['key'])
   if self.selected not in [r['key'] for r in state['entries']]:self.selected=None
   self.record_selector.setCurrentIndex(max(0,self.record_selector.findData(self.selected)))
   self.load_selected();self.set_loaded(True)
   self.status.setText(f'Loaded {len(state["entries"])} added partners. Save applies the selected form immediately.'+warning)
  except Exception as e:
   self.data=None;self.state=None;self.status.setText('Could not load: '+str(e))
  finally:self.loading=False
 def load_selected(self):
  if self.selected is None:
   line=self.destination.currentData();active=[r for r in self.lines[line] if self.physical[r].active]
   if not active:raise ValueError('Selected line has no active Partner template.')
   self.template.setCurrentIndex(self.template.findData(active[-1]));self.form_friend=self.friend.currentData();self.form_template=self.template.currentData()
   self.set_words(backend.new_entry(self.data,line,self.form_friend,self.form_template)['words'])
  else:
   r=self.state['entries'][self.selected-38];self.destination.setCurrentIndex(r['line']);self.form_friend=r['friend'];self.form_template=r['template'];self.set_words(r['words'])
   if r['friend']>=0:self.friend.setCurrentIndex(self.friend.findData(r['friend']))
   if r['template']>=0:self.template.setCurrentIndex(self.template.findData(r['template']))
  self.remove_button.setEnabled(self.selected is not None);self.clean=self.entry();self.update_preview()
 def select_record(self,index):
  if self.loading or self.data is None:return
  key=self.record_selector.itemData(index)
  if key==self.selected:return
  try:dirty=self.entry()!=self.clean
  except ValueError:dirty=True
  if dirty:
   self.loading=True;self.record_selector.setCurrentIndex(max(0,self.record_selector.findData(self.selected)));self.loading=False;self.status.setText('Save the current form, or Refresh to discard it, before switching partners.');return
  self.selected=key;self.loading=True
  try:
   self.load_selected();self.status.setText('Creating a NEW partner: choose sources, review the form and Save.' if self.selected is None else f'Editing extra_{self.selected-37}: Save updates this existing record and resets the form for a new addition.')
  except Exception as e:self.error(e)
  finally:self.loading=False
 def set_words(self,words):
  for i,w in self.fields.items():
   if i==2:continue
   if isinstance(w,QtWidgets.QComboBox):
    w.clear()
    for value,name in sorted(self.maps.get(i,{}).items(),key=lambda item:(item[1].casefold(),item[0])):w.addItem(f'{name} [{value}]',value)
    index=w.findData(words[i])
    if index<0:w.addItem(str(words[i]),words[i]);index=w.count()-1
    w.setCurrentIndex(index)
   else:w.setValue(words[i])
 def words(self):
  result=[]
  for i in range(16):
   w=self.fields[i]
   if i==2:result.append(self.destination.currentData())
   elif isinstance(w,QtWidgets.QComboBox):
    text=w.currentText().strip();index=w.findText(text)
    if index>=0:result.append(w.itemData(index))
    else:
     try:result.append(int(text,0))
     except ValueError as e:raise ValueError(f'{LABELS[i]}: select an entry or enter its numeric ID.') from e
   else:result.append(w.value())
  return result
 def entry(self):
  return dict(key=self.selected if self.selected is not None else 38+len(self.state['entries']),line=self.destination.currentData(),friend=self.form_friend,template=self.form_template,words=self.words())
 def destination_changed(self):
  if self.loading or self.data is None:return
  line=self.destination.currentData()
  active=[r for r in self.lines[line] if self.physical[r].active]
  if not active:return
  self.loading=True
  try:self.template.setCurrentIndex(self.template.findData(active[-1]))
  finally:self.loading=False
  self.copy_template()
 def update_preview(self):
  if self.data is None:return
  try:
   entry=self.entry();entries=list(self.state['entries'])
   if self.selected is None:entries.append(entry)
   else:entries[self.selected-38]=entry
   backend.validate_entries(self.data,entries)
   pages=backend.derive_pages(self.data,entries,self.state['mappings'],self.lines,backend.merge_slots(self.lines,entries,self.state.get('slots')) if self.state.get('slots') else None);choices=pages[entry['line']];host=choices[0]
   bykey={r['key']:r['words'] for r in entries}
   def name(key):
    words=bykey[key] if key>=38 else self.physical[key].words
    return self.names.get(words[5],f'Name {words[5]}')
   self.save_button.setEnabled(True)
   own=len(host)+sum(r['line']==entry['line'] for r in entries)
   self.preview.setText(f'Individual line: {own} active Digimon (transfers excluded). ' + ' | '.join(f'Battle page {i+1} ({len(page)}): '+', '.join(name(k) for k in page) for i,page in enumerate(choices))+'. A one-choice overflow page repeats its sole Digimon for navigation.')
  except Exception as e:self.save_button.setEnabled(False);self.preview.setText('Cannot save this placement: '+str(e))
 def copy_friend(self,*_):
  if self.loading or self.data is None:return
  try:self.set_words(backend.friend_assets(self.data,self.friend.currentData(),self.words()));self.form_friend=self.friend.currentData();self.update_preview();self.status.setText('Friend assets copied for the NEW partner. Save to add it.' if self.selected is None else f'Friend assets copied into extra_{self.selected-37}. Save replaces this existing partner’s assets. After Save, the form resets for a separate addition.')
  except Exception as e:self.error(e)
 def copy_template(self,*_):
  if self.loading or self.data is None:return
  try:
   old=self.words();index=self.template.currentData();words=list(self.physical[index].words)
   for i in (2,4,5,13,14):words[i]=old[i]
   backend.validate_words(self.data,words,self.destination.currentData());self.set_words(words);self.form_template=index;self.update_preview();self.status.setText('Partner settings copied. Friend assets retained. Save to apply.')
  except Exception as e:self.error(e)
 def error(self,e):self.status.setText('Not saved: '+str(e));QtWidgets.QMessageBox.warning(self,'Additional Partners',str(e))
 def save(self):
  if self.data is None:return
  try:
   creating=self.selected is None;entry=self.entry();backend.save(self.path,entry,self.digest,self.selected);self.selected=None
   self.loading=True
   try:self.destination.setCurrentIndex(-1)
   finally:self.loading=False
   self.refresh()
   if self.data is not None:self.status.setText(('Added a new partner. Form reset; the next Save adds a new record.' if creating else 'Updated the selected existing partner. Form reset; the next Save adds a new record.')+' No .bak generated.')
  except Exception as e:self.error(e)
 def remove(self):
  if self.data is None or self.selected is None:return
  if QtWidgets.QMessageBox.question(self,'Remove additional partner','Remove this added partner from the selected BIN? Original Partner and Friend records are retained.',QtWidgets.QMessageBox.Yes|QtWidgets.QMessageBox.No,QtWidgets.QMessageBox.No)!=QtWidgets.QMessageBox.Yes:return
  try:backend.remove(self.path,self.selected,self.digest);self.selected=None;self.refresh()
  except Exception as e:self.error(e)
 def export_csv(self):
  if self.data is None:return
  path,_=QtWidgets.QFileDialog.getSaveFileName(self,'Export selected Partner','additional_partner.csv','CSV files (*.csv)')
  if path:
   try:r=self.entry();backend.validate_words(self.data,r['words'],r['line']);backend.export_csv(path,r);self.status.setText('Exported selected form. BIN unchanged.')
   except Exception as e:self.loading=False;self.error(e)
 def import_csv(self):
  if self.data is None:return
  path,_=QtWidgets.QFileDialog.getOpenFileName(self,'Import selected Partner','','CSV files (*.csv)')
  if path:
   try:
    r=backend.import_csv(path,self.entry()['key']);backend.validate_words(self.data,r['words'],r['line']);self.loading=True;self.destination.setCurrentIndex(r['line']);self.form_friend=r['friend'];self.form_template=r['template'];self.set_words(r['words']);self.friend.setCurrentIndex(self.friend.findData(r['friend']));self.template.setCurrentIndex(self.template.findData(r['template']));self.loading=False;self.update_preview();self.status.setText('CSV loaded into the form. Save to apply.')
   except Exception as e:self.loading=False;self.error(e)
