from datetime import datetime as _dt

from app.config import BASE_CURRENCY
from app.contracts import cents, currency as parse_currency, key, text, timestamp, to_cny_minor
from app.deduplicate import unique_records
from app.errors import Problem, RowError
from app.modules.m8_fx.service import lookup as lookup_rate


def valid_tickets(rows, rate_book, isolates):
    for row in rows:
        values = row.values
        try:
            code = parse_currency(values['currency']) if values.get('currency', '').strip() else BASE_CURRENCY
            paid_minor = cents(values['amount'], values['unit'])
            record = {
                'ticket': key(values['ticket']), 'paid_at': timestamp(values['paid_at']),
                'paid_minor': paid_minor, 'currency': code,
                'status': text(values['status']),
            }
            if code == BASE_CURRENCY:
                record['paid_cents'] = paid_minor
                record['locked_rate'] = '1'
            else:
                rate_date = _dt.fromisoformat(record['paid_at']).date().isoformat()
                fx_rate = lookup_rate(rate_book, code, rate_date)
                if fx_rate is None:
                    raise RowError('FX_FACT_MISSING')
                record['locked_rate'] = str(fx_rate)
                record['rate_date'] = rate_date
                record['paid_cents'] = to_cny_minor(paid_minor, fx_rate)
        except RowError as exc:
            isolates.add(row, exc.code, values['ticket'])
            continue
        yield record, row


def intake(rows, rate_book, isolates):
    register = unique_records(valid_tickets(rows, rate_book, isolates), lambda item: item['ticket'],
                              'TICKET_DUPLICATE', 'TICKET_CONFLICT', isolates)
    tickets = {}
    for record in register:
        if record['status'] == 'paid':
            tickets[record['ticket']] = record
        else:
            isolates.add_record(record, 'TICKET_NOT_PAID', record['ticket'], status=record['status'])
    if not tickets:
        raise Problem(422, 'NO_TICKETS')
    return tickets
