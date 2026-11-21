from datetime import datetime as _dt

from decimal import Decimal

from app.config import BASE_CURRENCY
from app.contracts import cents, currency as parse_currency, key, timestamp, to_cny_minor
from app.deduplicate import unique_records
from app.errors import Problem, RowError
from app.modules.m5_refunds.rules import check_refund
from app.modules.m8_fx.service import lookup as lookup_rate

PRESERVE_FIELDS = ('ticket', 'show', 'refunded_at', 'reversal_cents', 'revalued_cents',
                   'fx_difference_cents', 'publication_id', 'cancelled_at')


def refund_records(rows, rate_book, isolates):
    for row in rows:
        values = row.values
        try:
            code = parse_currency(values['currency']) if values.get('currency', '').strip() else BASE_CURRENCY
            requested_minor = cents(values['amount'], values['unit'])
            entry = {field: key(values[field]) for field in ('refund_id', 'ticket', 'show')}
            entry.update(refunded_at=timestamp(values['refunded_at']), requested_minor=requested_minor,
                         currency=code)
            if code == BASE_CURRENCY:
                entry['requested_cents'] = requested_minor
            else:
                rate_date = _dt.fromisoformat(entry['refunded_at']).date().isoformat()
                fx_rate = lookup_rate(rate_book, code, rate_date)
                if fx_rate is None:
                    raise RowError('FX_FACT_MISSING')
                entry['refund_rate'] = str(fx_rate)
                entry['rate_date'] = rate_date
                entry['requested_cents'] = to_cny_minor(requested_minor, fx_rate)
        except RowError as exc:
            isolates.add(row, exc.code, values['refund_id'])
            continue
        yield entry, row


def reverse(rows, tickets, shows, publication, rate_book, isolates):
    originals = {(row['ticket'], row['show']): row for row in publication['allocations']}
    running_cny, running_minor, approved = {}, {}, []
    candidates = unique_records(refund_records(rows, rate_book, isolates), lambda item: item['refund_id'],
                                'REFUND_ID_DUPLICATE', 'REFUND_ID_CONFLICT', isolates)
    for event in sorted(candidates, key=lambda item: (item['refunded_at'], item['refund_id'])):
        code, detail = check_refund(event, tickets, shows, originals, running_cny, running_minor)
        if code:
            isolates.add_record(event, code, event['refund_id'], **detail)
            continue
        pair = (event['ticket'], event['show'])
        original = originals[pair]
        if event['currency'] == BASE_CURRENCY:
            locked_reversal = -event['requested_minor']
            revalued_reversal = locked_reversal
            refund_rate = '1'
        else:
            locked_rate = Decimal(original['locked_rate'])
            new_total_minor = detail['used_minor'] + event['requested_minor']
            cumulative_locked = to_cny_minor(new_total_minor, locked_rate)
            locked_reversal = -(cumulative_locked - detail['used_cents'])
            revalued_reversal = -event['requested_cents']
            refund_rate = event['refund_rate']
        fx_difference = -revalued_reversal - (-locked_reversal)
        running_cny[pair] = detail['used_cents'] + (-locked_reversal)
        running_minor[pair] = detail['used_minor'] + event['requested_minor']
        approved.append({
            **event, **detail,
            'reversal_cents': locked_reversal, 'revalued_cents': revalued_reversal,
            'fx_difference_cents': fx_difference,
            'locked_rate': original['locked_rate'], 'refund_rate': refund_rate,
            'cumulative_cents': running_cny[pair], 'cumulative_minor': running_minor[pair],
            'remaining_cents': detail['original_cents'] - running_cny[pair],
            'remaining_minor': detail['original_minor'] - running_minor[pair],
            'publication_id': publication['publication_id'], 'right_line': original['right_line'],
            'show_line': shows[event['show']]['line'],
            'cancelled_at': shows[event['show']]['cancelled_at']})
    return approved


def preserve_approved(prior, proposed):
    by_id = {row['refund_id']: tuple(row[field] for field in PRESERVE_FIELDS) for row in proposed}
    for event in prior:
        if by_id.get(event['refund_id']) != tuple(event[field] for field in PRESERVE_FIELDS):
            raise Problem(409, 'REFUND_CHANGED', {'refund_id': event['refund_id']})
