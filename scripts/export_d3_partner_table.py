import sys
from d3_partner_tables import export_partners
if __name__=='__main__':export_partners(sys.argv[1] if len(sys.argv)>1 else 'D3.bin',sys.argv[2] if len(sys.argv)>2 else 'd3_partner_table.csv')
