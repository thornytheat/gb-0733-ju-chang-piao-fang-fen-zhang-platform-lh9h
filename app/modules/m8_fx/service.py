from app.config import BASE_CURRENCY
from app.contracts import currency, day, rate, text
from app.deduplicate import unique_records
from app.errors import RowError


def rate_records(rows, isolates):
    for source_row in rows:
        values = source_row.values
        try:
            record = {
                'currency': currency(values['currency']), 'rate_date': day(values['rate_date']),
                'rate_to_cny': rate(values['rate_to_cny']), 'source': text(values['source']),
            }
            if record['currency'] == BASE_CURRENCY:
                raise RowError('FX_BASE_CURRENCY')
        except RowError as exc:
            isolates.add(source_row, exc.code, values['currency'])
            continue
        yield record, source_row


def build_rate_book(rows, isolates):
    """汇率事实去重：同币种同日期完全相同留最早，矛盾组全隔离。"""
    records = unique_records(rate_records(rows, isolates),
                             lambda item: (item['currency'], item['rate_date']),
                             'FX_RATE_DUPLICATE', 'FX_RATE_CONFLICT', isolates)
    book = {}
    for record in records:
        book[(record['currency'], record['rate_date'])] = record['rate_to_cny']
    return book


def lookup(book, code, rate_date):
    return book.get((code, rate_date))
