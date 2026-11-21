from collections import Counter
from time import perf_counter

from app.config import FX_POLICY, REPORT_ZONE, RULE_VERSION
from app.errors import Problem
from app.manifest import input_manifest, publication_digest, utc_now
from app.modules.m1_intake.service import intake
from app.modules.m2_rights.service import resolve
from app.modules.m3_scans.service import admit
from app.modules.m4_allocation.service import allocate
from app.modules.m5_refunds.service import preserve_approved, reverse
from app.modules.m6_reconcile.service import count_sources, reconcile
from app.modules.m8_fx.service import build_rate_book
from app.records import Isolations
from app.sources import SourceSet

PUBLICATION_INPUTS = {'tickets.csv', 'shows.csv', 'rights.csv'}


def compute(book, run_id, files, publication=None, head=None):
    started = perf_counter()
    inputs = input_manifest(files)
    isolates = Isolations()
    sources = SourceSet(files, isolates)
    rate_book = build_rate_book(sources.rows('fx_rates.csv'), isolates)
    tickets = intake(sources.rows('tickets.csv'), rate_book, isolates)
    shows, rights = resolve(sources.rows('shows.csv'), sources.rows('rights.csv'), tickets, isolates)
    digest = publication_digest(tickets, shows, rights)
    if publication is None:
        allocations, unassigned = allocate(tickets, shows, rights)
        publication = {'publication_id': 'pub_' + digest[:20], 'digest': digest, 'origin_run': run_id,
                       'allocations': allocations, 'unassigned': unassigned,
                       'inputs': [item for item in inputs if item['source'] in PUBLICATION_INPUTS]}
    elif publication['digest'] != digest:
        raise Problem(409, 'PUBLICATION_CHANGED', {'publication_id': publication['publication_id']})
    scans = admit(sources.rows('scans.csv'), tickets, shows, rights, isolates)
    refunds = reverse(sources.rows('refunds.csv'), tickets, shows, publication, rate_book, isolates)
    if head:
        preserve_approved(head['refunds'], refunds)
    fx_rows = [{'currency': code, 'rate_date': day, 'rate_to_cny': str(value)}
               for (code, day), value in sorted(rate_book.items())]
    accepted = {'tickets.csv': len(tickets), 'shows.csv': len(shows), 'rights.csv': len(rights),
                'scans.csv': len(scans), 'refunds.csv': len(refunds), 'fx_rates.csv': len(rate_book)}
    quarantine = sorted(isolates.rows, key=lambda row: (row['source'], row['line']))
    missing_fx = [row for row in quarantine if row['code'] == 'FX_FACT_MISSING']
    if missing_fx:
        raise Problem(422, 'FX_FACT_MISSING',
                      {'count': len(missing_fx),
                       'keys': sorted({row['key'] for row in missing_fx})[:20]})
    counts = count_sources(sources.stats, accepted, quarantine)
    accounts = reconcile(tickets, shows, scans, refunds, publication, counts)
    if not accounts['balanced']:
        raise Problem(409, 'RECONCILE_FAILED', accounts['checks'])
    manifest = {
        'run_id': run_id, 'book': book, 'rule_version': RULE_VERSION, 'inputs': inputs,
        'publication_id': publication['publication_id'], 'origin_run': publication['origin_run'],
        'parameters': {'time_zone': REPORT_ZONE, 'currency': 'CNY', 'money_unit': '分',
                       'tie_break': 'show_ascending', 'scan_window': '[opens,closes)', 'fx_policy': FX_POLICY},
        'generated_at': utc_now(), 'duration_ms': round((perf_counter() - started) * 1000),
        'previous_run': head['manifest']['run_id'] if head else None,
    }
    return {'manifest': manifest, 'publication': publication, 'quarantine': quarantine, 'funding': accounts,
            'tickets': list(tickets.values()), 'shows': list(shows.values()), 'rights': rights, 'scans': scans,
            'refunds': refunds, 'fx': fx_rows,
            'reason_counts': dict(sorted(Counter(row['code'] for row in quarantine).items()))}
