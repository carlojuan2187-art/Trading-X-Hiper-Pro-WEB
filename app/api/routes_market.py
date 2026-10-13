"""EndPoints del gráfico protegidos por sesión Telegram; sin ejecución de órdenes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import require_session
from app.market import terminal
from app.market.overlays import project, project_account

router = APIRouter(prefix='/api/v1/market', tags=['market'])


def no_market_error(function):
    try:
        return function()
    except terminal.MarketError as exc:
        raise HTTPException(status_code=422 if 'inválido' in str(exc) or 'Temporalidad' in str(exc) or 'existe' in str(exc) else 503,
                            detail=str(exc)) from exc


@router.get('/instruments')
def instruments(query: str = Query(default='', max_length=48), limit: int = Query(default=100, ge=1, le=500),
                session: dict = Depends(require_session)) -> dict:
    def fetch():
        rows = terminal.catalog()
        name = query.upper().strip()
        return {'items': [row for row in rows if name in row['symbol']][:limit]}
    return no_market_error(fetch)


@router.get('/candles')
def candles(symbol: str = Query(max_length=48), timeframe: str = '5m',
            limit: int = Query(default=400, ge=20, le=500), session: dict = Depends(require_session)) -> dict:
    return no_market_error(lambda: {'items': terminal.candles(symbol, timeframe, limit)})


@router.get('/snapshot')
def snapshot(symbol: str = Query(max_length=48), session: dict = Depends(require_session)) -> dict:
    return no_market_error(lambda: terminal.snapshot(symbol))


@router.get('/overlays')
def overlays(mode: str = Query(pattern='^(DEMO|LIVE)$'), session: dict = Depends(require_session)) -> dict:
    user_id = int(session['user_id'])
    if mode == 'DEMO':
        from app.demo.ledger import read
        return project_account(read(user_id), mode=mode)
    from app.database import get_user_active_trade_snapshot, get_user_trades_limited
    active = project(get_user_active_trade_snapshot(user_id) or {}, mode=mode, closed=False)
    history = [item for row in get_user_trades_limited(user_id, 100)
               if (item := project(row, mode=mode, closed=True)) is not None]
    return {'mode': mode, 'active': active, 'history': history}
