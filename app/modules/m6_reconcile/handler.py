from fastapi import APIRouter, Depends, Request, Response

from app.browse import Browse, snapshot_for
from app.modules.m7_evidence.csv_out import make_csv

router = APIRouter(prefix='/api/reconcile')

STATEMENT_FIELDS = ('venue', 'shows', 'scan_count', 'allocated_cents', 'refunded_cents', 'net_cents',
                    'fx_difference_cents', 'revalued_net_cents', 'payable_cents', 'unsettled_cents')


@router.get('/{run_id}')
def reconcile(request: Request, run_id: str, browse: Browse = Depends()):
    funding = snapshot_for(request, run_id)['funding']
    rows = browse.choose({'accepted': funding['ledger'], 'tickets': funding['tickets'], 'venues': funding['venues'],
                          'shows': funding['shows'], 'sources': funding['sources']})
    return browse.page(rows, totals=funding['totals'], checks=funding['checks'], balanced=funding['balanced'])


@router.get('/{run_id}/statement')
def venue_statement(request: Request, run_id: str):
    funding = snapshot_for(request, run_id)['funding']
    data = make_csv(funding['venues'], STATEMENT_FIELDS)
    return Response(data, media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="venue-statement-{run_id}.csv"'})
