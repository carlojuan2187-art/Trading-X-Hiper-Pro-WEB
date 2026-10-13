from __future__ import annotations

import logging
import asyncio
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes_admin import router as admin_router
from app.realtime.stream import router as stream_router
from app.api.routes_auth import router as auth_router
from app.api.routes_phone import router as phone_router
from app.api.routes_billing import router as billing_router
from app.api.routes_health import router as health_router
from app.api.routes_market import router as market_router
from app.api.routes_demo import router as demo_router
from app.api.routes_user import router as user_router
from app.config import MINIAPP_ALLOWED_ORIGINS

logger = logging.getLogger(__name__)
WEB_STATIC_DIR = Path(__file__).resolve().parent.parent / 'web' / 'static'


def create_app() -> FastAPI:
    app = FastAPI(
        title='Trading X Hiper Pro Web API',
        version='1.0.0',
        docs_url='/docs',
        redoc_url='/redoc',
    )

    @app.on_event('startup')
    async def ensure_phone_auth_indexes():
        # La unicidad del teléfono y Telegram ID forma parte de la seguridad.
        from app.auth.phone_login import init_indexes
        await asyncio.to_thread(init_indexes)

    allow_origins = MINIAPP_ALLOWED_ORIGINS or ['*']
    allow_credentials = '*' not in allow_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=allow_credentials,
        allow_methods=['*'],
        allow_headers=['*'],
    )

    @app.middleware('http')
    async def add_request_context(request: Request, call_next):
        request_id = request.headers.get('x-request-id') or str(uuid.uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception('API request failed request_id=%s path=%s', request_id, request.url.path)
            raise
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers['x-request-id'] = request_id
        response.headers['x-response-time-ms'] = str(elapsed_ms)
        if request.url.path in {'/', '/app', '/favicon.ico'} or request.url.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        logger.info('API %s %s -> %s in %sms request_id=%s', request.method, request.url.path, response.status_code, elapsed_ms, request_id)
        return response

    @app.exception_handler(RuntimeError)
    async def runtime_error_handler(_: Request, exc: RuntimeError):
        logger.error('Unhandled RuntimeError request_id=%s', getattr(_.state, 'request_id', 'unknown'))
        return JSONResponse(status_code=500, content={'detail': 'Error interno del servicio'})

    if WEB_STATIC_DIR.exists():
        app.mount('/static', StaticFiles(directory=str(WEB_STATIC_DIR)), name='static')

        def _index_response() -> FileResponse:
            response = FileResponse(WEB_STATIC_DIR / 'index.html')
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
            return response

        @app.get('/', include_in_schema=False)
        def root() -> FileResponse:
            return _index_response()

        @app.get('/app', include_in_schema=False)
        def app_entry() -> FileResponse:
            return _index_response()

        @app.get('/favicon.ico', include_in_schema=False)
        def favicon() -> FileResponse:
            return FileResponse(WEB_STATIC_DIR / 'favicon.ico', media_type='image/x-icon')
    else:
        @app.get('/')
        def root() -> dict:
            return {
                'service': 'trading-x-hiper-pro-miniapp-api',
                'status': 'ok',
                'docs': '/docs',
                'health': '/health',
            }

    @app.get('/api', include_in_schema=False)
    def api_root() -> dict:
        return {
            'service': 'trading-x-hiper-pro-miniapp-api',
            'status': 'ok',
            'docs': '/docs',
            'health': '/health',
            'miniapp': '/' if WEB_STATIC_DIR.exists() else None,
        }

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(phone_router)
    app.include_router(billing_router)
    app.include_router(user_router)
    app.include_router(demo_router)
    app.include_router(market_router)
    app.include_router(stream_router)
    app.include_router(admin_router)
    return app
