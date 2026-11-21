from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / 'sample_data'
SAMPLE_BOOK = 'tour-demo'
PREVIEW = 'sample'
RULE_VERSION = 'theatre-1.0.0'
REPORT_ZONE = 'Asia/Shanghai'
MAX_SOURCE_BYTES = 512 * 1024
MAX_SOURCE_ROWS = 12_000
MAX_CENTS = 9_999_999_999
MAX_RATE = 999_999
BASE_CURRENCY = 'CNY'
ALLOWED_CURRENCIES = ('CNY', 'USD', 'HKD')
FX_POLICY = {
    'lock': 'ticket_paid_at_rate',
    'revaluation': 'refund_at_rate',
    'rounding': 'decimal_half_up_to_cents',
    'fx_slot': 'separate_attributed_to_venue',
}

SOURCE_COLUMNS = {
    'tickets.csv': ('ticket', 'paid_at', 'amount', 'unit', 'status', 'note', 'currency'),
    'shows.csv': ('show', 'title', 'venue', 'publication_status', 'opens', 'closes', 'cancelled_at'),
    'rights.csv': ('ticket', 'show', 'weight'),
    'scans.csv': ('scan_id', 'ticket', 'show', 'scanned_at', 'gate'),
    'refunds.csv': ('refund_id', 'ticket', 'show', 'refunded_at', 'amount', 'unit', 'currency'),
    'fx_rates.csv': ('currency', 'rate_date', 'rate_to_cny', 'source', 'note'),
}

# 旧版六列报表缺省本币列，允许仅缺这一列。
OPTIONAL_COLUMNS = {'tickets.csv': 'currency', 'refunds.csv': 'currency'}
