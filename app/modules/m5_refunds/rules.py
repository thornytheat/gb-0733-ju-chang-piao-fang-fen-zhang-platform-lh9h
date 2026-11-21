def check_refund(event, tickets, shows, originals, running_cny, running_minor):
    ticket, show = event['ticket'], event['show']
    pair = (ticket, show)
    if ticket not in tickets:
        return 'TICKET_UNKNOWN', {}
    if show not in shows:
        return 'SHOW_UNKNOWN', {}
    original = originals.get(pair)
    if not original:
        return 'REFUND_NO_ORIGINAL', {}
    if event['currency'] != tickets[ticket]['currency']:
        return 'REFUND_CURRENCY_MISMATCH', {'ticket_currency': tickets[ticket]['currency'],
                                            'refund_currency': event['currency']}
    cancellation = shows[show]['cancelled_at']
    if cancellation is None:
        return 'REFUND_NOT_CANCELLED', {}
    if event['refunded_at'] < max(cancellation, tickets[ticket]['paid_at']):
        return 'REFUND_TOO_EARLY', {'cancelled_at': cancellation}
    cap_cny = original['allocated_cents']
    cap_minor = original['allocated_minor']
    used_cny = running_cny.get(pair, 0)
    used_minor = running_minor.get(pair, 0)
    detail = {'original_cents': cap_cny, 'used_cents': used_cny, 'available_cents': cap_cny - used_cny,
              'requested_cents': event['requested_cents'],
              'original_minor': cap_minor, 'used_minor': used_minor,
              'available_minor': cap_minor - used_minor, 'requested_minor': event['requested_minor']}
    if event['requested_minor'] > detail['available_minor']:
        # 人民币票 minor 即分，沿用原限额码；外币票用外币最小单位硬限额码。
        code = 'REFUND_CAP_EXCEEDED' if event['currency'] == 'CNY' else 'REFUND_FOREIGN_CAP_EXCEEDED'
        return code, detail
    return None, detail
