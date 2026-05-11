import sys
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from scripts.ingest_amc_fmr import map_company_to_symbol, COMPANY_TO_SYMBOL

print('faysal bank limited in dict?', 'faysal bank limited' in COMPANY_TO_SYMBOL)
print('mari energies limited in dict?', 'mari energies limited' in COMPANY_TO_SYMBOL)
print('engro holdings limited in dict?', 'engro holdings limited' in COMPANY_TO_SYMBOL)
print('engro corporation limited in dict?', 'engro corporation limited' in COMPANY_TO_SYMBOL)
print()

test = [
    'AMC Rating AM1 Faysal Bank Limited',
    'Auditor Yousuf Adil Chartered Accountants Mari Energies Limited',
    'Subscription | Redemption Days As per Market hours Maple Leaf Cement Factory Limited',
    'Listing Pakistan Stock Exchange (PSX) Engro Holdings Limited',
    'Trustee Central Depository Company of Pakistan Limited Fauji Fertilizer Company Limited',
    'Investment Committee Imtiaz Gadar, CFA | Muhammad Asad |Ahmed Hassan, Pakistan Petroleum Limited',
]
for s in test:
    sym, conf = map_company_to_symbol(s)
    print(f'"{s[:65]}..." -> {sym!r} (conf={conf})')
