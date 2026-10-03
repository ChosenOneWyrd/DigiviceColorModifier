"""CSV adapters for original and additional D-3 Partner/slot records."""
import argparse,csv,struct
from pathlib import Path
import d3_additional_partners as extra
import d3_evolution_core as core
DATA_HEADERS=list(extra.FIELDS);DATA_HEADERS[2]='jogress_win_partner_id'
HEADERS=['meta_offset','data_offset']+DATA_HEADERS

def export_partners(bin_path,csv_path):
 data=Path(bin_path).read_bytes();state=extra.read_state(data)
 words=[list(r.words) for r in core.read_partner_records(data)]+[r['words'] for r in state['entries']]
 with Path(csv_path).open('w',encoding='utf-8',newline='') as f:
  writer=csv.DictWriter(f,fieldnames=HEADERS);writer.writeheader()
  for i,w in enumerate(words):
   row=dict(zip(DATA_HEADERS,w));row['meta_offset']=hex(0x9D950+32*i) if i<38 else f'extra_{i-37}'
   row['data_offset']=hex(0x9D970+32*i) if i<38 else f'virtual_{i}'
   writer.writerow(row)
 print(f'Exported {len(words)} Partner records (including {len(state["entries"])} added).')

def import_partners(original,csv_path):
 state=extra.read_state(original)
 with Path(csv_path).open(encoding='utf-8-sig',newline='') as f:
  reader=csv.DictReader(f)
  if any(k not in (reader.fieldnames or []) for k in HEADERS):raise ValueError('Partner CSV is missing required columns.')
  rows=list(reader)
 if len(rows) not in (38,38+len(state['entries'])):raise ValueError('Partner CSV must contain the 38 original records, optionally followed by every existing added record. Use Additional Partners to create/remove records.')
 work=bytearray(original);entries=[{**r,'words':list(r['words'])} for r in state['entries']]
 for i,row in enumerate(rows):
  try:words=[int(row[k].strip(),0) for k in DATA_HEADERS]
  except (ValueError,AttributeError,TypeError) as ex:raise ValueError(f'Invalid values in Partner CSV row {i+2}.') from ex
  if any(not 0<=k<=65535 for k in words):raise ValueError('Partner fields must fit uint16.')
  if i<38:struct.pack_into('<16H',work,core.PARTNER_TABLE_OFFSET+32*i,*words)
  else:
   if row.get('meta_offset') not in ('',f'extra_{i-37}'):raise ValueError('Added Partner CSV identity/order mismatch.')
   entries[i-38]['words']=words;entries[i-38]['line']=words[2]
 lines,*_=core._synchronize_existing_bin_without_transfers(work)
 if entries:extra.install_into(work,entries,state['mappings'],lines,extra.merge_slots(lines,entries,state.get('slots')) if state.get('slots') else None)
 else:
  import d3_transfer_evolution_lines as transfer
  if state['mappings']:transfer.install_into(work,state['mappings'],lines)
 return bytes(work)

def export_slots(bin_path,csv_path):
 data=Path(bin_path).read_bytes();state=extra.read_state(data);lines=extra.combined_lines(data,state)
 n=max(10,((max(map(len,lines.values()))+9)//10)*10);columns=[f'slot_{i}' for i in range(1,n+1)]
 with Path(csv_path).open('w',encoding='utf-8',newline='') as f:
  writer=csv.writer(f);writer.writerow(['line_id','line_name']+columns)
  for line in range(7):writer.writerow([line,core.LINE_NAMES[line]]+lines[line]+['']*(n-len(lines[line])))
 print('Exported seven complete lines, including added partners.')

def read_slots(path):
 with Path(path).open(encoding='utf-8-sig',newline='') as f:
  reader=csv.DictReader(f);headers=reader.fieldnames or []
  columns=[k for k in headers if k.startswith('slot_')]
  if columns!=[f'slot_{i}' for i in range(1,len(columns)+1)] or len(columns)<10:raise ValueError('Use contiguous slot_1 through slot_10 (or later) columns.')
  rows=list(reader)
 if len(rows)!=7:raise ValueError('Evolution Slots CSV requires seven lines.')
 result={}
 for row in rows:
  line=int(row['line_id'],0)
  if line not in range(7) or line in result:raise ValueError('Duplicate/invalid line ID.')
  values=[];blank=False
  for col in columns:
   text=row[col].strip()
   if text=='':blank=True;continue
   if blank:raise ValueError('Slots must be contiguous, with no filled slot after a blank.')
   values.append(int(text,0))
  result[line]=values
 return result

def import_slots(original,csv_path):
 slots=read_slots(csv_path);state=extra.read_state(original)
 # Old original-only CSVs preserve additions; a unified CSV must include all extras.
 if state['entries'] and not any(k>=38 for row in slots.values() for k in row):slots=extra.merge_slots(slots,state['entries'],state.get('slots'))
 return extra.update_slots_bytes(original,slots)

def run_import(kind):
 p=argparse.ArgumentParser();p.add_argument('bin_in');p.add_argument('csv');p.add_argument('bin_out');p.add_argument('--dry-run',action='store_true');p.add_argument('--expected-sha256');a=p.parse_args()
 before=Path(a.bin_in).read_bytes()
 if a.expected_sha256 and extra.digest(before)!=a.expected_sha256:raise ValueError('BIN changed since Refresh. Refresh before saving table edits.')
 result=(import_partners if kind=='partners' else import_slots)(before,a.csv)
 if a.dry_run:print('Validated; no changes written.');return
 if Path(a.bin_in).read_bytes()!=before:raise ValueError('BIN changed during import; refresh and retry.')
 core.atomic_write(a.bin_out,result);print('Saved and synchronized original and added records. No .bak generated.')
