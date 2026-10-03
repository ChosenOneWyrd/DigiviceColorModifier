#!/usr/bin/env python3
"""Install beside an existing digimon_tool_gui.py without replacing other tabs."""
import argparse
import os
from pathlib import Path
import tempfile

FILES=('d3_evolution_core.py','d3_paging_codegen.py','d3_paging_legacy.py',
       'd3_transfer_evolution_lines.py','transfer_evolution_lines_tab.py',
       'import_d3_evolution_slots.py','import_d3_partner_table.py')


def integrate(text):
    edits=(('from evolution_slots_tab import EvolutionSlotsTab',
            'from transfer_evolution_lines_tab import TransferEvolutionLinesTab',
            'from evolution_slots_tab import EvolutionSlotsTab\nfrom transfer_evolution_lines_tab import TransferEvolutionLinesTab'),
           ('        self.evolution_slots_tab = EvolutionSlotsTab(self)',
            'self.transfer_evolution_lines_tab = TransferEvolutionLinesTab(self)',
            '        self.evolution_slots_tab = EvolutionSlotsTab(self)\n        self.transfer_evolution_lines_tab = TransferEvolutionLinesTab(self)'),
           ('        tabs.addTab(self.evolution_slots_tab, "Evolution Slots")',
            'tabs.addTab(self.transfer_evolution_lines_tab, "Transfer Evolution Lines")',
            '        tabs.addTab(self.evolution_slots_tab, "Evolution Slots")\n        tabs.addTab(self.transfer_evolution_lines_tab, "Transfer Evolution Lines")'))
    for anchor,marker,replacement in edits:
        if marker in text:continue
        if text.count(anchor)!=1:raise ValueError('Cannot identify the Evolution Slots integration point in this GUI. No files changed.')
        text=text.replace(anchor,replacement)
    compile(text,'digimon_tool_gui.py','exec')
    return text


def install(gui):
    gui=Path(gui).resolve();folder=gui.parent;root=Path(__file__).resolve().parent
    original=gui.read_text(encoding='utf-8');patched=integrate(original)
    # Validate every file before replacing any file. No .bak generation.
    outputs={folder/name:(root/name).read_bytes() for name in FILES}
    outputs[gui]=patched.encode()
    for path,content in outputs.items():compile(content,str(path),'exec')
    staged=[]
    try:
        for path,content in outputs.items():
            fd,temp=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=folder)
            with os.fdopen(fd,'wb') as handle:handle.write(content);handle.flush();os.fsync(handle.fileno())
            staged.append((path,temp))
        if gui.read_text(encoding='utf-8')!=original:raise ValueError('GUI changed during installation. No files replaced.')
        for path,temp in staged:os.replace(temp,path)
    finally:
        for _,temp in staged:
            if os.path.exists(temp):os.unlink(temp)
    print('Installed Transfer Evolution Lines. Restart the GUI. No BIN files were changed.')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('gui',type=Path,help='Path to your existing digimon_tool_gui.py');args=p.parse_args();install(args.gui)
if __name__=='__main__':main()
