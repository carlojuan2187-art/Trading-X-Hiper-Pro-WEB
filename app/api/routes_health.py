from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter(tags=['health'])


@router.get('/health')
def healthcheck() -> dict:
    return {
        'status': 'ok',
        'service': 'trading-x-hiper-pro-miniapp-api',
        'utc_time': datetime.now(timezone.utc).isoformat(),
    }
