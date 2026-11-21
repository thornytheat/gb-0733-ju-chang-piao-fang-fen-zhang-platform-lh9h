from collections import defaultdict

from app.modules.m4_allocation.rules import split_cents


def allocate(tickets, shows, rights):
    eligible = defaultdict(dict)
    for right in rights:
        if right['eligible']:
            eligible[right['ticket']][right['show']] = right
    allocations, unassigned = [], []
    for ticket, receipt in sorted(tickets.items()):
        entries = eligible[ticket]
        if not entries:
            unassigned.append({'ticket': ticket, 'unsettled_cents': receipt['paid_cents'],
                               'unsettled_minor': receipt['paid_minor'], 'currency': receipt['currency'],
                               'sale_line': receipt['line'], 'code': 'NO_ELIGIBLE_RIGHTS'})
            continue
        weights = {show: right['weight'] for show, right in entries.items()}
        cny_shares = {show: share for show, share in split_cents(receipt['paid_cents'], weights)}
        minor_shares = {show: share for show, share in split_cents(receipt['paid_minor'], weights)}
        for show, share in cny_shares.items():
            allocations.append({'ticket': ticket, 'show': show, 'venue': shows[show]['venue'],
                                'weight': weights[show], **share,
                                'allocated_minor': minor_shares[show]['allocated_cents'],
                                'currency': receipt['currency'], 'locked_rate': receipt['locked_rate'],
                                'sale_line': receipt['line'],
                                'right_line': entries[show]['line'], 'show_line': shows[show]['line']})
    allocations.sort(key=lambda row: (row['ticket'], row['show']))
    return allocations, unassigned
