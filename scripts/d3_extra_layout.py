"""Hardware-confirmed C layout constants, isolated from generic transfer storage."""
VIEWER_CODE_HASH = '805f7e21d974eaff457a876d886f138bd6e1c0613e6ba074f458e87eaec378b5'
ADAPTER_CODE = bytes.fromhex('08090300c4986648025e85fe8bfa099380ff4594909a84fe0ec708090300c4986648025e85fe9afa4f9688d642f0f8f44100909a82fef8f4')
ADAPTER_LABELS = {'resolve': 391808, 'stock_resolve': 391819, 'unlock': 391821, 'stock_unlock': 391834}
C_ORDER = [0, 1, 5, 7, 9, 23, 33, 34, 6, 7, 9, 14, 4, 12, 15, 38, 24, 27, 25, 26, 13, 10, 11, 3, 28, 2, 20, 8, 29, 32, 30, 31, 17, 18, 19, 21, 22, 16, 35, 36, 37, 6, 8, 13, 20]
C_WORDS = [4, 15, 1, 20, 87, 36, 405, 410, 0, 0, 0, 40965, 41146, 1338, 7, 0]
C_PAGES = {1: ([33,34,6,7,9,14,4,12,15], [3,28,2,20,8,38])}
UNLOCK_ALIAS_OFFSET = 0xad528
