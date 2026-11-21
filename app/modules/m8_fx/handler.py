from fastapi import APIRouter, Depends, Request

from app.browse import Browse, isolated, snapshot_for
from app.config import FX_POLICY

router = APIRouter(prefix='/api/fx')


@router.get('/{run_id}')
def fx_facts(request: Request, run_id: str, browse: Browse = Depends()):
    snapshot = snapshot_for(request, run_id)
    rows = browse.choose({'accepted': snapshot['fx'],
                          'isolated': isolated(snapshot, 'fx_rates.csv')})
    return browse.page(rows, policy=FX_POLICY, totals=snapshot['funding']['totals'])
