# ============================================================
# DATABASE – TRADING X HIPER PRO
# Sistema profesional MongoDB Atlas
# PRODUCCIÓN REAL – BLINDADO
# + INTERÉS COMPUESTO (capital Telegram se actualiza automático)
#
# PLANES (SIN FEES / SIN REFERIDOS):
#   - PRUEBA 5 días (vence a medianoche Cuba)
#   - PREMIUM 30 días (vence a medianoche Cuba)
# ============================================================

from datetime import datetime, timedelta
from pymongo import MongoClient, ReturnDocument
import hashlib
import os
import pytz
import re
import socket
import sys

from app.crypto_utils import PrivateKeyDecryptError, decrypt_private_key, encrypt_private_key

# ============================================================
# CONEXIÓN MONGODB
# ============================================================

MONGO_URI = os.getenv("MONGO_URL")
DB_NAME = os.getenv("MONGO_DB_NAME", "TRADING_X_HIPER_PRO")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

users_col = db["users"]
trades_col = db["trades"]
settings_col = db["settings"]
admin_action_logs_col = db["admin_action_logs"]
runtime_status_col = db["runtime_status"]
runtime_components_shadow_col = db["runtime_components_shadow"]
user_runtime_col = db["user_runtime"]
user_activity_col = db["user_activity"]
strategy_router_events_col = db["strategy_router_events"]
strategy_runtime_summary_col = db["strategy_runtime_summary"]
payment_orders_col = db["payment_orders"]
payment_verification_logs_col = db["payment_verification_logs"]
subscription_events_col = db["subscription_events"]
referral_reward_events_col = db["referral_reward_events"]

# ============================================================
# ZONA HORARIA (CUBA) – vencimientos por medianoche
# ============================================================

CUBA_TZ = pytz.timezone("America/Havana")

def _now_utc() -> datetime:
    return datetime.utcnow()

def _now_cuba() -> datetime:
    return datetime.now(CUBA_TZ)

def _midnight_cuba_after_days(days: int) -> datetime:
    """
    Retorna la medianoche (00:00) de Cuba para (hoy + days), convertida a UTC naive.
    Ej:
      si hoy es 2026-02-04 (Cuba), days=5 => 2026-02-09 00:00 (Cuba).
    """
    now_cuba = _now_cuba()
    target_date = now_cuba.date() + timedelta(days=int(days))
    midnight_local = CUBA_TZ.localize(datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0))
    # Guardamos en UTC (naive) para consistencia
    return midnight_local.astimezone(pytz.UTC).replace(tzinfo=None)

def _parse_dt(x):
    if not x:
        return None
    if isinstance(x, datetime):
        return x
    try:
        return datetime.fromisoformat(str(x))
    except Exception:
        return None


_PROCESS_BOOTED_AT = _now_utc()
_HOSTNAME = socket.gethostname() or 'unknown-host'
_HOSTNAME_SHORT = _HOSTNAME.split('.', 1)[0] or _HOSTNAME
_PROCESS_PID = int(os.getpid())


def _runtime_hash(value: str | None, length: int = 10) -> str | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[: max(6, int(length))]


def _extract_mongo_target(uri: str | None) -> str | None:
    raw = str(uri or '').strip()
    if not raw:
        return None
    raw = re.sub(r'^[a-zA-Z0-9+.-]+://', '', raw)
    if '@' in raw:
        raw = raw.split('@', 1)[1]
    raw = raw.split('/', 1)[0]
    raw = raw.split('?', 1)[0]
    return raw or None


def _infer_process_role() -> str:
    explicit = str(os.getenv('PROCESS_ROLE') or os.getenv('APP_RUNTIME_ROLE') or '').strip().lower()
    if explicit:
        return explicit
    argv = ' '.join(sys.argv).lower()
    if 'uvicorn' in argv or 'web_main:app' in argv or 'web_main.py' in argv:
        return 'web_api'
    if 'main.py' in argv:
        return 'bot_worker'
    return 'runtime'


def get_runtime_identity() -> dict:
    mongo_target = _extract_mongo_target(MONGO_URI)
    process_role = _infer_process_role()
    runtime_instance = f"{process_role}:{_HOSTNAME_SHORT}:{_PROCESS_PID}"
    return {
        'process_role': process_role,
        'runtime_instance': runtime_instance,
        'runtime_instance_fingerprint': _runtime_hash(runtime_instance),
        'host': _HOSTNAME,
        'host_short': _HOSTNAME_SHORT,
        'pid': _PROCESS_PID,
        'db_name': DB_NAME,
        'db_fingerprint': _runtime_hash(DB_NAME.lower()),
        'mongo_target': mongo_target,
        'mongo_target_fingerprint': _runtime_hash(str(mongo_target or '').lower()),
        'booted_at': _PROCESS_BOOTED_AT,
        'argv_hint': ' '.join(sys.argv[:3]),
    }


def _runtime_identity_metadata() -> dict:
    identity = get_runtime_identity()
    return {
        'writer_process_role': identity.get('process_role'),
        'writer_runtime_instance': identity.get('runtime_instance'),
        'writer_runtime_instance_fingerprint': identity.get('runtime_instance_fingerprint'),
        'writer_host': identity.get('host_short') or identity.get('host'),
        'writer_pid': identity.get('pid'),
        'writer_db_name': identity.get('db_name'),
        'writer_db_fingerprint': identity.get('db_fingerprint'),
        'writer_mongo_target': identity.get('mongo_target'),
        'writer_mongo_target_fingerprint': identity.get('mongo_target_fingerprint'),
        'writer_booted_at': _serialize_runtime_dt(identity.get('booted_at')),
    }


def describe_runtime_identity() -> str:
    identity = get_runtime_identity()
    parts = [
        f"rol={identity.get('process_role')}",
        f"instancia={identity.get('runtime_instance')}",
        f"db={identity.get('db_name')}",
    ]
    if identity.get('mongo_target'):
        parts.append(f"mongo={identity.get('mongo_target')}")
    return ' · '.join(parts)

# ============================================================
# LOG EN VIVO (SERVIDOR)
# ============================================================

def db_log(msg: str):
    ts = _now_utc().isoformat()
    print(f"[DB {ts}] {msg}", file=sys.stdout, flush=True)


try:
    payment_orders_col.create_index('order_id', unique=True)
    payment_orders_col.create_index('user_id')
    payment_orders_col.create_index('status')
    payment_orders_col.create_index('expires_at')

    # Limpieza defensiva: documentos viejos con matched_tx_hash=None rompen la unicidad
    # porque Mongo considera null como valor indexable.
    try:
        payment_orders_col.update_many({'matched_tx_hash': None}, {'$unset': {'matched_tx_hash': ''}})
    except Exception as _payment_null_cleanup_exc:
        print(f"[DB {_now_utc().isoformat()}] ⚠ payment null cleanup error: {_payment_null_cleanup_exc}", file=sys.stdout, flush=True)

    try:
        payment_orders_col.drop_index('matched_tx_hash_1')
    except Exception:
        pass

    payment_orders_col.create_index(
        'matched_tx_hash',
        unique=True,
        partialFilterExpression={'matched_tx_hash': {'$type': 'string'}},
    )

    payment_verification_logs_col.create_index('order_id')
    payment_verification_logs_col.create_index('user_id')
    strategy_router_events_col.create_index('expires_at', expireAfterSeconds=0, name='router_event_retention')
    strategy_router_events_col.create_index([('user_id', 1), ('created_at', -1)])
    strategy_router_events_col.create_index([('event_type', 1), ('created_at', -1)])
    strategy_router_events_col.create_index([('user_id', 1), ('symbol', 1), ('created_at', -1)])
    strategy_runtime_summary_col.create_index([('user_id', 1), ('execution_mode', 1), ('last_seen_at', -1)])
    strategy_runtime_summary_col.create_index([('user_id', 1), ('symbol', 1), ('strategy_id', 1), ('regime_id', 1), ('execution_mode', 1)], unique=True)
    subscription_events_col.create_index('user_id')
    subscription_events_col.create_index('order_id')
    referral_reward_events_col.create_index('referrer_id')
    referral_reward_events_col.create_index('referred_user_id')
    referral_reward_events_col.create_index('source_order_id', unique=True)
except Exception as _payment_index_exc:
    print(f"[DB {_now_utc().isoformat()}] ⚠ payment index init error: {_payment_index_exc}", file=sys.stdout, flush=True)

# ============================================================
# UTILIDADES (blindaje)
# ============================================================


REFERRAL_PREMIUM_REWARD_TABLE = {15: 7, 30: 15}


def get_referral_reward_days_for_purchase(days: int) -> int:
    try:
        return int(REFERRAL_PREMIUM_REWARD_TABLE.get(int(days), 0) or 0)
    except Exception:
        return 0



def _sanitize_admin_reason(reason: str | None) -> str | None:
    value = str(reason or '').strip()
    if not value:
        return None
    return value[:300]


def _serialize_runtime_dt(value: datetime | None) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


def _runtime_shadow_key(component: str) -> str:
    return f"runtime_component::{str(component or '').strip().lower()}"


def publish_runtime_component(
    component: str,
    state: str = 'online',
    *,
    metadata: dict | None = None,
    verify_readback: bool = False,
    readback_max_age_seconds: int = 180,
) -> dict:
    name = str(component or '').strip().lower()
    if not name:
        return {
            'ok': False,
            'healthy': False,
            'component': '',
            'state': str(state or 'online').strip().lower() or 'online',
            'primary_ok': False,
            'shadow_ok': False,
            'verified': False,
            'readback_source': None,
            'error': 'component_empty',
            'identity': get_runtime_identity(),
        }

    now = _now_utc()
    meta_payload = dict(_runtime_identity_metadata())
    meta_payload.update(dict(metadata or {}))
    payload = {
        'component': name,
        'state': str(state or 'online').strip().lower() or 'online',
        'last_seen_at': now,
        'updated_at': now,
        'metadata': meta_payload,
    }

    primary_ok = False
    shadow_ok = False
    primary_error = None
    shadow_error = None

    try:
        runtime_status_col.update_one(
            {'component': name},
            {'$set': payload, '$setOnInsert': {'created_at': now}},
            upsert=True,
        )
        primary_ok = True
    except Exception as e:
        primary_error = e

    try:
        runtime_components_shadow_col.update_one(
            {'component': name},
            {'$set': payload, '$setOnInsert': {'created_at': now, 'key': _runtime_shadow_key(name)}},
            upsert=True,
        )
        shadow_ok = True
    except Exception as e:
        shadow_error = e

    verified = False
    readback_source = None
    readback_error = None
    readback_doc = None
    if primary_ok or shadow_ok:
        if not primary_ok:
            try:
                db_log(f"⚠ runtime primary write failed component={name}; usando shadow: {primary_error}")
            except Exception:
                pass
        if verify_readback:
            try:
                readback_doc = get_runtime_component(name)
                readback_source = ((readback_doc or {}).get('metadata') or {}).get('runtime_source')
                last_seen = (readback_doc or {}).get('last_seen_at')
                age_seconds = None
                if isinstance(last_seen, datetime):
                    age_seconds = max(0, int((now - last_seen).total_seconds()))
                verified = bool(
                    readback_doc
                    and str((readback_doc or {}).get('state') or '').strip().lower() == payload['state']
                    and age_seconds is not None
                    and age_seconds <= max(1, int(readback_max_age_seconds or 180))
                )
                if not verified:
                    readback_error = f'readback_mismatch age={age_seconds}'
            except Exception as e:
                readback_error = str(e)
                verified = False

    if not (primary_ok or shadow_ok):
        try:
            db_log(f"⚠ touch_runtime_component error component={name}: primary={primary_error} shadow={shadow_error}")
        except Exception:
            pass

    ok = bool(primary_ok or shadow_ok)
    healthy = bool(ok and (not verify_readback or verified))
    return {
        'ok': ok,
        'healthy': healthy,
        'component': name,
        'state': payload['state'],
        'primary_ok': primary_ok,
        'shadow_ok': shadow_ok,
        'verified': verified,
        'readback_source': readback_source,
        'readback_state': (readback_doc or {}).get('state') if readback_doc else None,
        'primary_error': str(primary_error) if primary_error else None,
        'shadow_error': str(shadow_error) if shadow_error else None,
        'readback_error': readback_error,
        'identity': get_runtime_identity(),
        'metadata': meta_payload,
    }


def touch_runtime_component(component: str, state: str = 'online', *, metadata: dict | None = None) -> bool:
    return bool(publish_runtime_component(component, state, metadata=metadata).get('ok'))


def get_runtime_component(component: str) -> dict | None:
    name = str(component or '').strip().lower()
    if not name:
        return None

    doc = None
    source = None
    try:
        doc = runtime_status_col.find_one({'component': name}, {'_id': 0})
        if doc:
            source = 'runtime_status'
    except Exception as e:
        try:
            db_log(f"⚠ get_runtime_component primary read error component={name}: {e}")
        except Exception:
            pass

    if not doc:
        try:
            doc = runtime_components_shadow_col.find_one({'component': name}, {'_id': 0})
            if doc:
                source = 'runtime_components_shadow'
        except Exception as e:
            try:
                db_log(f"⚠ get_runtime_component shadow read error component={name}: {e}")
            except Exception:
                pass

    if not doc:
        return None
    last_seen = _parse_dt(doc.get('last_seen_at'))
    updated_at = _parse_dt(doc.get('updated_at'))
    metadata = dict(doc.get('metadata') or {})
    metadata.setdefault('runtime_source', source)
    return {
        'component': doc.get('component') or name,
        'state': doc.get('state') or 'unknown',
        'last_seen_at': last_seen,
        'updated_at': updated_at,
        'metadata': metadata,
        'created_at': _parse_dt(doc.get('created_at')),
    }


def _runtime_component_snapshot(component: str, *, warning_after_seconds: int, critical_after_seconds: int) -> dict:
    now = _now_utc()
    doc = get_runtime_component(component)
    if not doc:
        return {
            'component': component,
            'status': 'offline',
            'last_seen_at': None,
            'freshness_seconds': None,
            'state': 'missing',
            'metadata': {},
            'message': 'Sin heartbeat registrado todavía.',
        }

    last_seen = doc.get('last_seen_at')
    freshness = None
    if isinstance(last_seen, datetime):
        freshness = max(0, int((now - last_seen).total_seconds()))

    explicit_state = str(doc.get('state') or 'online').lower()
    if explicit_state in {'error', 'failed'}:
        status = 'error'
        message = 'El componente reportó error en el último heartbeat.'
    elif freshness is None:
        status = 'offline'
        message = 'Heartbeat inválido o sin timestamp.'
    elif freshness <= int(warning_after_seconds):
        status = 'online'
        message = 'Heartbeat reciente.'
    elif freshness <= int(critical_after_seconds):
        status = 'stale'
        message = 'Heartbeat atrasado; revisar proceso.'
    else:
        status = 'offline'
        message = 'Heartbeat demasiado antiguo o proceso detenido.'

    return {
        'component': component,
        'status': status,
        'last_seen_at': last_seen,
        'freshness_seconds': freshness,
        'state': explicit_state,
        'metadata': doc.get('metadata') or {},
        'message': message,
    }


def _safe_collection_count(collection, query: dict | None = None) -> int | None:
    try:
        return int(collection.count_documents(query or {}))
    except Exception:
        return None


def _safe_latest_runtime_doc(collection, query: dict | None = None) -> dict | None:
    try:
        return collection.find_one(query or {}, {'_id': 0, 'component': 1, 'updated_at': 1, 'last_seen_at': 1, 'metadata': 1}, sort=[('updated_at', -1)])
    except Exception:
        return None


def _build_runtime_bridge_diagnostics(components: dict) -> dict:
    expected = ['telegram_bot', 'trading_loop', 'scanner']
    backend_identity = get_runtime_identity()
    runtime_status_count = _safe_collection_count(runtime_status_col)
    runtime_shadow_count = _safe_collection_count(runtime_components_shadow_col)
    runtime_status_expected = _safe_collection_count(runtime_status_col, {'component': {'$in': expected}})
    runtime_shadow_expected = _safe_collection_count(runtime_components_shadow_col, {'component': {'$in': expected}})
    latest_primary = _safe_latest_runtime_doc(runtime_status_col)
    latest_shadow = _safe_latest_runtime_doc(runtime_components_shadow_col)

    missing_components = [name for name in expected if not components.get(name) or (components.get(name) or {}).get('state') == 'missing']
    writer_db_names = sorted({
        str(((components.get(name) or {}).get('metadata') or {}).get('writer_db_name') or '').strip()
        for name in expected
        if str(((components.get(name) or {}).get('metadata') or {}).get('writer_db_name') or '').strip()
    })
    writer_mongo_fingerprints = sorted({
        str(((components.get(name) or {}).get('metadata') or {}).get('writer_mongo_target_fingerprint') or '').strip()
        for name in expected
        if str(((components.get(name) or {}).get('metadata') or {}).get('writer_mongo_target_fingerprint') or '').strip()
    })

    status = 'healthy'
    message = 'Los heartbeats esperados están presentes en la DB leída por esta API.'
    hints: list[str] = []

    if len(missing_components) == len(expected):
        status = 'critical'
        if (runtime_status_count or 0) == 0 and (runtime_shadow_count or 0) == 0:
            message = 'Esta API no encuentra ningún heartbeat en runtime_status ni en runtime_components_shadow.'
            hints.append('Lo más probable es que el worker/bot esté escribiendo en otra DB/servicio, o que no pueda persistir en Mongo.')
        else:
            message = 'Esta API sí ve documentos runtime en la DB actual, pero no ve telegram_bot/trading_loop/scanner.'
            hints.append('Revisar despliegue parcial, nombres de componente o limpieza accidental de las colecciones de runtime.')
    elif missing_components:
        status = 'warning'
        message = f"Faltan heartbeats para: {', '.join(missing_components)}."
        hints.append('El sistema está parcial o intermitente.')

    if writer_db_names and backend_identity.get('db_name') not in writer_db_names:
        status = 'critical'
        hints.append('La API está leyendo una DB distinta a la reportada por los escritores de heartbeat.')
    if writer_mongo_fingerprints and backend_identity.get('mongo_target_fingerprint') and backend_identity.get('mongo_target_fingerprint') not in writer_mongo_fingerprints:
        status = 'critical'
        hints.append('La API apunta a un cluster/host Mongo distinto al de los escritores de heartbeat.')

    return {
        'status': status,
        'message': message,
        'missing_components': missing_components,
        'runtime_status_count': runtime_status_count,
        'runtime_components_shadow_count': runtime_shadow_count,
        'runtime_status_expected_count': runtime_status_expected,
        'runtime_components_shadow_expected_count': runtime_shadow_expected,
        'latest_primary_component': (latest_primary or {}).get('component'),
        'latest_primary_updated_at': _parse_dt((latest_primary or {}).get('updated_at')),
        'latest_shadow_component': (latest_shadow or {}).get('component'),
        'latest_shadow_updated_at': _parse_dt((latest_shadow or {}).get('updated_at')),
        'writer_db_names': writer_db_names,
        'writer_mongo_target_fingerprints': writer_mongo_fingerprints,
        'hints': hints,
        'backend_identity': backend_identity,
    }


def get_system_runtime_snapshot() -> dict:
    now = _now_utc()
    components = {
        'telegram_bot': _runtime_component_snapshot('telegram_bot', warning_after_seconds=90, critical_after_seconds=300),
        'trading_loop': _runtime_component_snapshot('trading_loop', warning_after_seconds=90, critical_after_seconds=300),
        'scanner': _runtime_component_snapshot('scanner', warning_after_seconds=120, critical_after_seconds=360),
    }

    plan_active_query = {
        'plan': {'$in': ['trial', 'premium']},
        'plan_expires_at': {'$gt': now},
    }
    users_with_active_plan = users_col.count_documents(plan_active_query)
    users_trading_active = users_col.count_documents({'trading_status': 'active'})

    latest_open_doc = users_col.find_one(
        {'last_open_at': {'$ne': None}},
        {'_id': 0, 'user_id': 1, 'username': 1, 'last_open_at': 1, 'last_open': 1},
        sort=[('last_open_at', -1)],
    )
    latest_close_doc = users_col.find_one(
        {'last_close_at': {'$ne': None}},
        {'_id': 0, 'user_id': 1, 'username': 1, 'last_close_at': 1, 'last_close': 1},
        sort=[('last_close_at', -1)],
    )
    if not latest_close_doc:
        latest_trade = trades_col.find_one(
            {},
            {'_id': 0, 'user_id': 1, 'symbol': 1, 'side': 1, 'direction': 1, 'entry_price': 1, 'exit_price': 1, 'qty': 1, 'notional_usdc': 1, 'profit': 1, 'gross_pnl': 1, 'fees': 1, 'pnl_source': 1, 'realized_fills': 1, 'close_source': 1, 'exit_reason': 1, 'timestamp': 1},
            sort=[('timestamp', -1)],
        )
        if latest_trade:
            latest_close_doc = {
                'user_id': latest_trade.get('user_id'),
                'username': (users_col.find_one({'user_id': latest_trade.get('user_id')}, {'_id': 0, 'username': 1}) or {}).get('username'),
                'last_close_at': latest_trade.get('timestamp'),
                'last_close': {
                    'symbol': latest_trade.get('symbol'),
                    'side': latest_trade.get('side'),
                    'direction': latest_trade.get('direction'),
                    'entry_price': latest_trade.get('entry_price'),
                    'exit_price': latest_trade.get('exit_price'),
                    'qty': latest_trade.get('qty'),
                    'notional_usdc': latest_trade.get('notional_usdc'),
                    'profit': latest_trade.get('profit'),
                    'gross_pnl': latest_trade.get('gross_pnl'),
                    'fees': latest_trade.get('fees'),
                    'pnl_source': latest_trade.get('pnl_source'),
                    'realized_fills': latest_trade.get('realized_fills'),
                    'close_source': latest_trade.get('close_source'),
                    'exit_reason': latest_trade.get('exit_reason'),
                },
            }

    active_trades_collection_name = os.getenv('ACTIVE_TRADES_COLLECTION', 'active_trades')
    active_trades_collection = db[active_trades_collection_name]
    try:
        active_trades_count = int(active_trades_collection.count_documents({}))
    except Exception:
        active_trades_count = 0

    latest_trade_manager = None
    try:
        latest_active_trade = active_trades_collection.find_one(
            {},
            {'_id': 0, 'user_id': 1, 'symbol': 1, 'direction': 1, 'manager_heartbeat_ts': 1},
            sort=[('manager_heartbeat_ts', -1)],
        )
        if latest_active_trade:
            ts = latest_active_trade.get('manager_heartbeat_ts')
            ts_dt = datetime.utcfromtimestamp(float(ts)) if ts else None
            latest_trade_manager = {
                'user_id': latest_active_trade.get('user_id'),
                'symbol': latest_active_trade.get('symbol'),
                'direction': latest_active_trade.get('direction'),
                'manager_heartbeat_at': ts_dt,
            }
    except Exception as e:
        latest_trade_manager = {'error': str(e)}

    scanner_meta = components['scanner'].get('metadata') or {}
    issues = []
    for name, snapshot in components.items():
        if snapshot.get('status') not in {'online'}:
            issues.append({
                'component': name,
                'status': snapshot.get('status'),
                'message': snapshot.get('message'),
            })

    statuses = [components['telegram_bot']['status'], components['trading_loop']['status'], components['scanner']['status']]
    if 'error' in statuses or 'offline' in statuses:
        overall_status = 'degraded'
    elif 'stale' in statuses:
        overall_status = 'warning'
    else:
        overall_status = 'healthy'

    def _activity_payload(doc: dict | None, key: str, ts_key: str) -> dict | None:
        if not doc:
            return None
        payload = doc.get(key) or {}
        return {
            'user_id': doc.get('user_id'),
            'username': doc.get('username'),
            'at': _parse_dt(doc.get(ts_key)),
            'symbol': payload.get('symbol') if isinstance(payload, dict) else None,
            'event': payload.get('message') if isinstance(payload, dict) else None,
            'payload': payload if isinstance(payload, dict) else None,
        }

    bridge_diagnostics = _build_runtime_bridge_diagnostics(components)

    return {
        'overall_status': overall_status,
        'checked_at': now,
        'components': components,
        'runtime': {
            'users_with_active_plan': int(users_with_active_plan),
            'users_trading_active': int(users_trading_active),
            'active_trades': int(active_trades_count),
            'latest_trade_manager': latest_trade_manager,
            'latest_open': _activity_payload(latest_open_doc, 'last_open', 'last_open_at'),
            'latest_close': _activity_payload(latest_close_doc, 'last_close', 'last_close_at'),
            'scanner_last_event': scanner_meta.get('last_event'),
            'scanner_last_symbol': scanner_meta.get('symbol'),
            'scanner_last_user_id': scanner_meta.get('user_id'),
        },
        'backend_identity': bridge_diagnostics.get('backend_identity') or get_runtime_identity(),
        'bridge_diagnostics': bridge_diagnostics,
        'issues': issues,
    }


def log_admin_action(
    actor_user_id: int,
    actor_username: str | None,
    action: str,
    *,
    target_user_id: int | None = None,
    target_username: str | None = None,
    reason: str | None = None,
    status: str = 'success',
    message: str | None = None,
    metadata: dict | None = None,
) -> bool:
    try:
        doc = {
            'created_at': _now_utc(),
            'actor_user_id': int(actor_user_id),
            'actor_username': str(actor_username or '').strip() or None,
            'action': str(action or '').strip() or 'unknown',
            'target_user_id': int(target_user_id) if target_user_id is not None else None,
            'target_username': str(target_username or '').strip() or None,
            'reason': _sanitize_admin_reason(reason),
            'status': str(status or 'success').strip() or 'success',
            'message': str(message or '').strip() or None,
            'metadata': metadata or {},
        }
        admin_action_logs_col.insert_one(doc)
        return True
    except Exception as e:
        db_log(f"⚠ log_admin_action error actor={actor_user_id} action={action}: {e}")
        return False


def get_admin_action_history(limit: int = 20, target_user_id: int | None = None) -> list[dict]:
    lim = max(1, min(int(limit), 100))
    query = {}
    if target_user_id is not None:
        query['target_user_id'] = int(target_user_id)

    cursor = admin_action_logs_col.find(query, {'_id': 0}).sort('created_at', -1).limit(lim)
    results = []
    for row in cursor:
        created_at = _parse_dt(row.get('created_at'))
        results.append({
            'created_at': created_at,
            'actor_user_id': row.get('actor_user_id'),
            'actor_username': row.get('actor_username'),
            'action': row.get('action') or 'unknown',
            'target_user_id': row.get('target_user_id'),
            'target_username': row.get('target_username'),
            'reason': row.get('reason'),
            'status': row.get('status') or 'success',
            'message': row.get('message'),
            'metadata': row.get('metadata') or {},
        })
    return results



def log_user_activity(
    user_id: int,
    title: str,
    detail: str | None = None,
    *,
    tone: str = 'info',
    event_type: str = 'info',
    metadata: dict | None = None,
    occurred_at: datetime | None = None,
) -> bool:
    try:
        uid = int(user_id)
        title_value = str(title or '').strip()[:120]
        if not title_value:
            return False
        detail_value = str(detail or '').strip()[:500] or None
        event_value = str(event_type or 'info').strip().lower()[:40] or 'info'
        tone_value = str(tone or 'info').strip().lower()[:20] or 'info'
        now = occurred_at if isinstance(occurred_at, datetime) else _now_utc()
        user_doc = users_col.find_one({'user_id': uid}, {'_id': 0, 'username': 1}) or {}
        user_activity_col.insert_one({
            'user_id': uid,
            'username': user_doc.get('username'),
            'title': title_value,
            'detail': detail_value,
            'tone': tone_value,
            'event_type': event_value,
            'metadata': metadata or {},
            'created_at': now,
        })
        return True
    except Exception as e:
        try:
            db_log(f"⚠ log_user_activity error user_id={user_id}: {e}")
        except Exception:
            pass
        return False


def get_user_activity(user_id: int, limit: int = 12):
    try:
        uid = int(user_id)
        lim = max(1, min(int(limit), 50))
    except Exception:
        return []

    try:
        return list(
            user_activity_col.find(
                {'user_id': uid},
                {'_id': 0},
            )
            .sort('created_at', -1)
            .limit(lim)
        )
    except Exception as e:
        try:
            db_log(f"⚠ get_user_activity error user_id={user_id}: {e}")
        except Exception:
            pass
        return []



def get_admin_monitor_feed(limit: int = 30, event_types: list[str] | None = None):
    try:
        lim = max(1, min(int(limit), 100))
    except Exception:
        lim = 30

    default_types = [
        'trade_opened',
        'trade_closed',
        'payment_confirmed',
        'trading_activated',
        'trading_paused',
        'stats_reset',
        'private_key_hardened',
        'wallet_updated',
        'private_key_updated',
    ]
    normalized = [str(x).strip().lower() for x in (event_types or default_types) if str(x).strip()]
    query = {'admin_hidden': {'$ne': True}}
    if normalized:
        query['event_type'] = {'$in': normalized}
    try:
        return list(
            user_activity_col.find(query, {'_id': 0})
            .sort('created_at', -1)
            .limit(lim)
        )
    except Exception as e:
        try:
            db_log(f"⚠ get_admin_monitor_feed error: {e}")
        except Exception:
            pass
        return []


def clear_admin_monitor_feed(event_types: list[str] | None = None) -> dict:
    default_types = [
        'trade_opened',
        'trade_closed',
        'payment_confirmed',
        'trading_activated',
        'trading_paused',
        'stats_reset',
        'private_key_hardened',
        'wallet_updated',
        'private_key_updated',
    ]
    normalized = [str(x).strip().lower() for x in (event_types or default_types) if str(x).strip()]
    query = {'admin_hidden': {'$ne': True}}
    if normalized:
        query['event_type'] = {'$in': normalized}
    try:
        result = user_activity_col.update_many(
            query,
            {'$set': {'admin_hidden': True, 'admin_hidden_at': _now_utc()}},
        )
        return {'ok': True, 'hidden_count': int(getattr(result, 'modified_count', 0) or 0)}
    except Exception as e:
        try:
            db_log(f"⚠ clear_admin_monitor_feed error: {e}")
        except Exception:
            pass
        return {'ok': False, 'message': str(e), 'hidden_count': 0}


def get_admin_active_positions(limit: int = 20) -> list[dict]:
    try:
        lim = max(1, min(int(limit), 50))
    except Exception:
        lim = 20

    try:
        active_trades_collection_name = os.getenv('ACTIVE_TRADES_COLLECTION', 'active_trades')
        active_trades_collection = db[active_trades_collection_name]
        cursor = active_trades_collection.find({}, {'_id': 0}).sort('manager_heartbeat_ts', -1).limit(lim)
        items = []
        for doc in cursor:
            user_id = int(doc.get('user_id') or 0) if doc.get('user_id') is not None else None
            user_doc = users_col.find_one({'user_id': user_id}, {'_id': 0, 'username': 1, 'plan': 1, 'trading_status': 1}) if user_id else {}
            items.append({
                'user_id': user_id,
                'username': user_doc.get('username'),
                'plan': user_doc.get('plan') or 'none',
                'trading_status': user_doc.get('trading_status') or 'inactive',
                'symbol': doc.get('symbol'),
                'direction': doc.get('direction'),
                'side': doc.get('side'),
                'entry_price': _safe_float(doc.get('entry_price'), 0.0) if doc.get('entry_price') is not None else None,
                'qty_coin': _safe_float(doc.get('qty_coin'), 0.0) if doc.get('qty_coin') is not None else None,
                'qty_usdc': _safe_float(doc.get('qty_usdc'), 0.0) if doc.get('qty_usdc') is not None else None,
                'opened_at': _parse_dt(doc.get('opened_at')),
                'last_price': _safe_float(doc.get('last_price'), 0.0) if doc.get('last_price') is not None else None,
                'peak_price': _safe_float(doc.get('peak_price'), 0.0) if doc.get('peak_price') is not None else None,
                'manager_heartbeat_ts': float(doc.get('manager_heartbeat_ts') or 0.0),
                'manager_heartbeat_at': datetime.utcfromtimestamp(float(doc.get('manager_heartbeat_ts'))) if doc.get('manager_heartbeat_ts') else None,
            })
        return items
    except Exception as e:
        try:
            db_log(f"⚠ get_admin_active_positions error: {e}")
        except Exception:
            pass
        return []


def get_admin_live_monitor_snapshot(limit_events: int = 30, limit_positions: int = 20) -> dict:
    events = get_admin_monitor_feed(limit=limit_events)
    active_positions = get_admin_active_positions(limit=limit_positions)
    return {
        'events': events,
        'active_positions': active_positions,
        'counts': {
            'events': len(events),
            'active_positions': len(active_positions),
            'trade_events': len([x for x in events if str((x or {}).get('event_type') or '').lower() in {'trade_opened', 'trade_closed'}]),
            'payment_events': len([x for x in events if str((x or {}).get('event_type') or '').lower() == 'payment_confirmed']),
        },
    }


def get_admin_operator_snapshots(limit: int = 20) -> list[dict]:
    try:
        lim = max(1, min(int(limit), 50))
    except Exception:
        lim = 20

    query = {
        '$or': [
            {'trading_status': 'active'},
            {'runtime_live_trade': True},
            {'runtime_checked_at': {'$ne': None}},
        ]
    }
    projection = {
        '_id': 0,
        'user_id': 1,
        'username': 1,
        'plan': 1,
        'plan_expires_at': 1,
        'trading_status': 1,
        'runtime_state': 1,
        'runtime_mode': 1,
        'runtime_message': 1,
        'runtime_checked_at': 1,
        'runtime_live_trade': 1,
        'runtime_active_symbol': 1,
        'runtime_exchange_balance': 1,
        'runtime_exchange_equity': 1,
        'runtime_exchange_status': 1,
        'runtime_exchange_positions_count': 1,
        'runtime_last_cycle_at': 1,
        'runtime_last_result': 1,
        'runtime_last_block_reason': 1,
        'runtime_last_symbol': 1,
        'runtime_last_decision': 1,
        'runtime_metadata': 1,
    }

    try:
        cursor = users_col.find(query, projection).sort('runtime_checked_at', -1).limit(lim)
        rows = []
        for doc in cursor:
            metadata = doc.get('runtime_metadata') or {}
            rows.append({
                'user_id': int(doc.get('user_id') or 0),
                'username': doc.get('username'),
                'plan': doc.get('plan') or 'none',
                'plan_expires_at': _parse_dt(doc.get('plan_expires_at')),
                'trading_status': doc.get('trading_status') or 'inactive',
                'runtime_state': doc.get('runtime_state') or 'unknown',
                'runtime_mode': doc.get('runtime_mode'),
                'runtime_message': doc.get('runtime_message'),
                'runtime_checked_at': _parse_dt(doc.get('runtime_checked_at')),
                'runtime_live_trade': bool(doc.get('runtime_live_trade', False)),
                'runtime_active_symbol': doc.get('runtime_active_symbol'),
                'exchange_balance': _safe_float(doc.get('runtime_exchange_balance'), 0.0) if doc.get('runtime_exchange_balance') is not None else None,
                'exchange_equity': _safe_float(doc.get('runtime_exchange_equity'), 0.0) if doc.get('runtime_exchange_equity') is not None else None,
                'exchange_status': doc.get('runtime_exchange_status'),
                'positions_count': _safe_int(doc.get('runtime_exchange_positions_count'), 0) if doc.get('runtime_exchange_positions_count') is not None else None,
                'last_cycle_at': _parse_dt(doc.get('runtime_last_cycle_at')),
                'last_result': doc.get('runtime_last_result'),
                'last_block_reason': doc.get('runtime_last_block_reason'),
                'last_symbol': doc.get('runtime_last_symbol'),
                'last_decision': doc.get('runtime_last_decision'),
                'last_event': metadata.get('last_event'),
                'scanner_score': metadata.get('scanner_score'),
                'strategy_model': metadata.get('strategy_model'),
                'capital_threshold': metadata.get('capital_threshold'),
            })
        return rows
    except Exception as e:
        try:
            db_log(f"⚠ get_admin_operator_snapshots error: {e}")
        except Exception:
            pass
        return []


def get_user_active_trade_snapshot(user_id: int) -> dict | None:
    try:
        uid = int(user_id)
    except Exception:
        return None

    try:
        active_trades_collection_name = os.getenv('ACTIVE_TRADES_COLLECTION', 'active_trades')
        active_trades_collection = db[active_trades_collection_name]
        doc = active_trades_collection.find_one({'user_id': uid})
        if not isinstance(doc, dict):
            return None
        doc.pop('_id', None)
        return doc
    except Exception as e:
        try:
            db_log(f"⚠ get_user_active_trade_snapshot error user_id={user_id}: {e}")
        except Exception:
            pass
        return None


def get_user_cycle_policy(user_id: int) -> dict:
    try:
        uid = int(user_id)
    except Exception:
        return {
            'user_id': None,
            'should_run_cycle': False,
            'entries_allowed': False,
            'manager_allowed': False,
            'manager_only': False,
            'desired_trading_status': 'inactive',
            'runtime_state': 'unknown',
            'runtime_mode': 'unknown',
            'runtime_message': 'Usuario inválido.',
            'live_trade': False,
            'active_symbol': None,
            'has_wallet': False,
            'has_private_key': False,
            'plan_active': False,
        }

    u = users_col.find_one(
        {'user_id': uid},
        {
            '_id': 0,
            'wallet': 1,
            'private_key': 1,
            'private_key_runtime_status': 1,
            'private_key_runtime_error': 1,
            'private_key_runtime_checked_at': 1,
            'trading_status': 1,
            'plan': 1,
            'plan_expires_at': 1,
        },
    ) or {}

    active_trade = get_user_active_trade_snapshot(uid) or {}
    emergency = settings_col.find_one({'_id': f'emergency_exit:{uid}'}) or {}
    if emergency and not active_trade:
        active_trade = {'symbol': emergency.get('symbol'), 'emergency_exit_pending': True}
    has_wallet = bool(u.get('wallet'))
    has_private_key = bool(u.get('private_key'))
    desired_trading_status = str(u.get('trading_status') or 'inactive').strip().lower() or 'inactive'
    plan_active = _plan_is_active(u)
    live_trade = bool(active_trade)
    active_symbol = active_trade.get('symbol') if isinstance(active_trade, dict) else None
    private_key_runtime_status = str(u.get('private_key_runtime_status') or '').strip().lower() or None
    private_key_runtime_error = str(u.get('private_key_runtime_error') or '').strip() or None
    private_key_invalid = private_key_runtime_status in {'decrypt_error', 'invalid', 'unsupported_version'}

    entries_allowed = bool(not emergency and desired_trading_status == 'active' and plan_active and has_wallet and has_private_key and not private_key_invalid)
    manager_allowed = bool(live_trade and has_wallet and has_private_key and not private_key_invalid)
    manager_only = bool(manager_allowed and not entries_allowed)
    should_run_cycle = bool(entries_allowed or manager_allowed)

    if manager_only:
        runtime_state = 'manager_only'
        runtime_mode = 'manager_only'
        if desired_trading_status != 'active':
            runtime_message = 'Nuevas entradas pausadas. El motor seguirá gestionando la posición activa hasta cerrarla.'
        elif not plan_active:
            runtime_message = 'El acceso ya no permite nuevas entradas, pero la posición activa seguirá bajo gestión hasta cerrarse.'
        else:
            runtime_message = 'El motor mantiene una posición activa en modo de gestión.'
    elif entries_allowed:
        runtime_state = 'entries_enabled'
        runtime_mode = 'entries_enabled'
        runtime_message = 'El motor puede seguir evaluando entradas y gestionando operaciones.'
    elif desired_trading_status != 'active':
        runtime_state = 'paused'
        runtime_mode = 'paused'
        runtime_message = 'El trading está pausado para nuevas entradas.'
    elif not plan_active:
        runtime_state = 'access_blocked'
        runtime_mode = 'blocked'
        runtime_message = 'No hay acceso vigente para abrir nuevas operaciones.'
    elif private_key_invalid:
        runtime_state = 'configuration_blocked'
        runtime_mode = 'blocked'
        runtime_message = (
            'La private key almacenada no pudo validarse. Reconfigúrala en la MiniApp antes de reactivar la operativa.'
            if not live_trade
            else 'La private key almacenada no pudo validarse. Hay una posición activa que no puede ser gestionada automáticamente hasta reparar la credencial.'
        )
        if private_key_runtime_error:
            runtime_message = f"{runtime_message} Detalle técnico: {private_key_runtime_error[:140]}"
    elif not has_wallet or not has_private_key:
        runtime_state = 'configuration_blocked'
        runtime_mode = 'blocked'
        missing = []
        if not has_wallet:
            missing.append('wallet')
        if not has_private_key:
            missing.append('private key')
        runtime_message = f"Falta configuración crítica: {', '.join(missing)}."
    else:
        runtime_state = 'idle'
        runtime_mode = 'idle'
        runtime_message = 'Sin actividad operativa registrada todavía.'

    return {
        'user_id': uid,
        'should_run_cycle': should_run_cycle,
        'entries_allowed': entries_allowed,
        'manager_allowed': manager_allowed,
        'manager_only': manager_only,
        'desired_trading_status': desired_trading_status,
        'runtime_state': runtime_state,
        'runtime_mode': runtime_mode,
        'runtime_message': runtime_message,
        'live_trade': live_trade,
        'active_symbol': active_symbol,
        'has_wallet': has_wallet,
        'has_private_key': has_private_key,
        'plan_active': plan_active,
        'private_key_runtime_status': private_key_runtime_status,
        'private_key_runtime_error': private_key_runtime_error,
        'private_key_invalid': private_key_invalid,
    }


def touch_user_operational_state(
    user_id: int,
    state: str,
    message: str,
    *,
    mode: str | None = None,
    source: str = 'system',
    live_trade: bool | None = None,
    active_symbol: str | None = None,
    metadata: dict | None = None,
) -> bool:
    try:
        uid = int(user_id)
    except Exception:
        return False

    now = _now_utc()
    payload = {
        'user_id': uid,
        'state': str(state or 'unknown').strip().lower() or 'unknown',
        'mode': str(mode or '').strip().lower() or None,
        'message': str(message or '').strip() or None,
        'source': str(source or 'system').strip().lower() or 'system',
        'last_seen_at': now,
        'updated_at': now,
        'live_trade': bool(live_trade) if live_trade is not None else False,
        'active_symbol': str(active_symbol or '').strip().upper() or None,
        'metadata': metadata or {},
    }
    try:
        user_runtime_col.update_one(
            {'user_id': uid},
            {'$set': payload, '$setOnInsert': {'created_at': now}},
            upsert=True,
        )
        runtime_metadata = payload.get('metadata') or {}
        users_col.update_one(
            {'user_id': uid},
            {
                '$set': {
                    'runtime_state': payload['state'],
                    'runtime_mode': payload['mode'],
                    'runtime_message': payload['message'],
                    'runtime_source': payload['source'],
                    'runtime_checked_at': now,
                    'runtime_live_trade': payload['live_trade'],
                    'runtime_active_symbol': payload['active_symbol'],
                    'runtime_metadata': runtime_metadata,
                    'runtime_exchange_balance': _safe_float(runtime_metadata.get('exchange_available_balance'), 0.0) if runtime_metadata.get('exchange_available_balance') is not None else None,
                    'runtime_exchange_equity': _safe_float(runtime_metadata.get('exchange_account_value'), 0.0) if runtime_metadata.get('exchange_account_value') is not None else None,
                    'runtime_exchange_status': runtime_metadata.get('exchange_status'),
                    'runtime_exchange_positions_count': _safe_int(runtime_metadata.get('positions_count'), 0) if runtime_metadata.get('positions_count') is not None else None,
                    'runtime_last_cycle_at': _parse_dt(runtime_metadata.get('last_cycle_at')) or now,
                    'runtime_last_result': runtime_metadata.get('last_result'),
                    'runtime_last_block_reason': runtime_metadata.get('last_block_reason'),
                    'runtime_last_symbol': runtime_metadata.get('last_symbol'),
                    'runtime_last_decision': runtime_metadata.get('last_decision'),
                }
            },
        )
        return True
    except Exception as e:
        try:
            db_log(f"⚠ touch_user_operational_state error user_id={uid}: {e}")
        except Exception:
            pass
        return False


def get_user_operational_runtime(user_id: int) -> dict | None:
    try:
        uid = int(user_id)
    except Exception:
        return None

    try:
        doc = user_runtime_col.find_one({'user_id': uid}, {'_id': 0})
        if not isinstance(doc, dict):
            return None
        return doc
    except Exception as e:
        try:
            db_log(f"⚠ get_user_operational_runtime error user_id={uid}: {e}")
        except Exception:
            pass
        return None


def _safe_int(x, default: int = 0) -> int:
    try:
        if x is None:
            return int(default)
        if isinstance(x, bool):
            return int(x)
        if isinstance(x, int):
            return x
        return int(float(x))
    except Exception:
        return int(default)


def _safe_float(x, default: float = 0.0) -> float:
    try:
        if x is None:
            return float(default)
        return float(x)
    except Exception:
        return float(default)

def _safe_counter_key(value: str | None) -> str:
    raw = str(value or '').strip().lower()
    if not raw:
        return 'unknown'
    cleaned = re.sub(r'[^a-z0-9_]+', '_', raw)
    cleaned = re.sub(r'_+', '_', cleaned).strip('_')
    return cleaned or 'unknown'


def _strategy_runtime_query(
    *,
    user_id: int | None = None,
    symbol: str | None = None,
    execution_mode: str | None = None,
    strategy_id: str | None = None,
    regime_id: str | None = None,
    event_type: str | None = None,
) -> dict:
    query: dict = {}
    try:
        if user_id is not None:
            query['user_id'] = int(user_id)
    except Exception:
        return {'user_id': '__invalid__'}

    normalized_symbol = str(symbol or '').strip().upper()
    if normalized_symbol:
        query['symbol'] = normalized_symbol

    normalized_mode = str(execution_mode or '').strip().lower()
    if normalized_mode:
        query['execution_mode'] = normalized_mode

    normalized_strategy = str(strategy_id or '').strip().lower()
    if normalized_strategy:
        query['strategy_id'] = normalized_strategy

    normalized_regime = str(regime_id or '').strip().lower()
    if normalized_regime:
        query['regime_id'] = normalized_regime

    normalized_event = _safe_counter_key(event_type) if str(event_type or '').strip() else None
    if normalized_event:
        query['event_type'] = normalized_event
    return query


def _normalize_strategy_runtime_doc(doc: dict | None) -> dict:
    raw = dict(doc or {})
    raw.pop('_id', None)
    for key in (
        'created_at',
        'last_seen_at',
    ):
        if isinstance(raw.get(key), datetime):
            raw[key] = raw[key].isoformat()

    for nested_key in (
        'signal_summary',
        'shadow_summary',
        'scanner_summary',
        'regime_summary',
        'last_signal_summary',
        'last_shadow_summary',
        'last_scanner_summary',
        'last_regime_summary',
        'extra',
        'last_extra',
    ):
        nested = raw.get(nested_key)
        if isinstance(nested, dict):
            normalized_nested = {}
            for k, v in nested.items():
                if isinstance(v, datetime):
                    normalized_nested[k] = v.isoformat()
                else:
                    normalized_nested[k] = v
            raw[nested_key] = normalized_nested
    return raw


def _build_strategy_runtime_counter_delta(payload: dict) -> dict:
    event_key = _safe_counter_key(payload.get('event_type'))
    execution_mode = _safe_counter_key(payload.get('execution_mode'))
    out = {
        'events_total': 1,
        f'event_type_counts.{event_key}': 1,
    }
    if execution_mode == 'live':
        out['live_events_total'] = 1
    elif execution_mode == 'shadow':
        out['shadow_events_total'] = 1
    else:
        out['router_events_total'] = 1

    if bool(payload.get('signal')):
        out['signals_total'] = 1
        if execution_mode == 'shadow':
            out['shadow_signals_total'] = 1
        elif execution_mode == 'live':
            out['live_signals_total'] = 1

    if bool(payload.get('selected')):
        out['selected_total'] = 1
    if bool(payload.get('trade_opened')):
        out['trades_opened_total'] = 1
    if bool(payload.get('regime_changed')):
        out['regime_changes_total'] = 1
    if bool(payload.get('shadow_evaluated')):
        out['shadow_evaluated_total'] = 1
    if bool(payload.get('shadow_signal')):
        out['shadow_signal_total'] = 1
    return out


def record_strategy_router_event(user_id: int, payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    try:
        uid = int(user_id)
    except Exception:
        return False

    now = _now_utc()
    event_type = _safe_counter_key(payload.get('event_type'))
    symbol = str(payload.get('symbol') or '').strip().upper() or None
    strategy_id = str(payload.get('strategy_id') or '').strip().lower() or 'unknown'
    regime_id = str(payload.get('regime_id') or '').strip().lower() or 'unknown'
    execution_mode = str(payload.get('execution_mode') or '').strip().lower() or 'router'
    direction = str(payload.get('direction') or '').strip().lower() or None

    signal_summary = payload.get('signal_summary') if isinstance(payload.get('signal_summary'), dict) else {}
    shadow_summary = payload.get('shadow_summary') if isinstance(payload.get('shadow_summary'), dict) else {}
    scanner_summary = payload.get('scanner_summary') if isinstance(payload.get('scanner_summary'), dict) else {}
    regime_summary = payload.get('regime_summary') if isinstance(payload.get('regime_summary'), dict) else {}
    extra = payload.get('extra') if isinstance(payload.get('extra'), dict) else {}

    doc = {
        'user_id': uid,
        'event_type': event_type,
        'symbol': symbol,
        'strategy_id': strategy_id,
        'regime_id': regime_id,
        'execution_mode': execution_mode,
        'direction': direction,
        'signal': bool(payload.get('signal')),
        'selected': bool(payload.get('selected')),
        'trade_opened': bool(payload.get('trade_opened')),
        'regime_changed': bool(payload.get('regime_changed')),
        'shadow_evaluated': bool(payload.get('shadow_evaluated')),
        'shadow_signal': bool(payload.get('shadow_signal')),
        'created_at': now,
        'signal_summary': signal_summary,
        'shadow_summary': shadow_summary,
        'scanner_summary': scanner_summary,
        'regime_summary': regime_summary,
        'extra': extra,
    }

    retention_days = max(int(os.getenv('STRATEGY_EVENT_RETENTION_DAYS', '14')), 0)
    if retention_days:
        doc['expires_at'] = now + timedelta(days=retention_days)

    try:
        strategy_router_events_col.insert_one(doc)

        summary_filter = {
            'user_id': uid,
            'symbol': symbol,
            'strategy_id': strategy_id,
            'regime_id': regime_id,
            'execution_mode': execution_mode,
        }
        summary_set = {
            'last_seen_at': now,
            'last_event_type': event_type,
            'last_direction': direction,
            'last_signal': bool(doc.get('signal')),
            'last_selected': bool(doc.get('selected')),
            'last_trade_opened': bool(doc.get('trade_opened')),
            'last_regime_changed': bool(doc.get('regime_changed')),
            'last_shadow_evaluated': bool(doc.get('shadow_evaluated')),
            'last_shadow_signal': bool(doc.get('shadow_signal')),
            'last_signal_summary': signal_summary,
            'last_shadow_summary': shadow_summary,
            'last_scanner_summary': scanner_summary,
            'last_regime_summary': regime_summary,
            'last_extra': extra,
        }
        strategy_runtime_summary_col.update_one(
            summary_filter,
            {
                '$set': summary_set,
                '$inc': _build_strategy_runtime_counter_delta(doc),
                '$setOnInsert': {'created_at': now},
            },
            upsert=True,
        )
        return True
    except Exception as e:
        try:
            db_log(f"⚠ record_strategy_router_event error user_id={uid}: {e}")
        except Exception:
            pass
        return False


def get_strategy_router_events(
    user_id: int | None = None,
    *,
    limit: int = 100,
    event_type: str | None = None,
    execution_mode: str | None = None,
    symbol: str | None = None,
    strategy_id: str | None = None,
    regime_id: str | None = None,
) -> list[dict]:
    lim = max(1, min(int(limit or 100), 500))
    query = _strategy_runtime_query(
        user_id=user_id,
        symbol=symbol,
        execution_mode=execution_mode,
        strategy_id=strategy_id,
        regime_id=regime_id,
        event_type=event_type,
    )
    if query.get('user_id') == '__invalid__':
        return []
    try:
        cursor = strategy_router_events_col.find(query, {'_id': 0}).sort('created_at', -1).limit(lim)
        return [_normalize_strategy_runtime_doc(item) for item in cursor]
    except Exception as e:
        try:
            db_log(f"⚠ get_strategy_router_events error query={query}: {e}")
        except Exception:
            pass
        return []


def get_strategy_runtime_summary(
    user_id: int | None = None,
    *,
    limit: int = 100,
    symbol: str | None = None,
    execution_mode: str | None = None,
    strategy_id: str | None = None,
    regime_id: str | None = None,
) -> list[dict]:
    lim = max(1, min(int(limit or 100), 500))
    query = _strategy_runtime_query(
        user_id=user_id,
        symbol=symbol,
        execution_mode=execution_mode,
        strategy_id=strategy_id,
        regime_id=regime_id,
    )
    if query.get('user_id') == '__invalid__':
        return []
    try:
        cursor = strategy_runtime_summary_col.find(query, {'_id': 0}).sort('last_seen_at', -1).limit(lim)
        return [_normalize_strategy_runtime_doc(item) for item in cursor]
    except Exception as e:
        try:
            db_log(f"⚠ get_strategy_runtime_summary error query={query}: {e}")
        except Exception:
            pass
        return []


def get_strategy_runtime_overview(user_id: int | None = None, limit_recent_events: int = 25) -> dict:
    query = _strategy_runtime_query(user_id=user_id)
    if query.get('user_id') == '__invalid__':
        return {
            'scope_user_id': None,
            'counts': {},
            'breakdown': {'execution_mode': {}, 'strategy': {}, 'regime': {}, 'event_type': {}},
            'recent_events': [],
            'partial': True,
            'error': 'invalid_user_id',
        }

    counts = {
        'summary_rows': 0,
        'unique_users': 0,
        'unique_symbols': 0,
        'unique_strategies': 0,
        'unique_regimes': 0,
        'events_total': 0,
        'live_events_total': 0,
        'shadow_events_total': 0,
        'router_events_total': 0,
        'signals_total': 0,
        'live_signals_total': 0,
        'shadow_signals_total': 0,
        'selected_total': 0,
        'trades_opened_total': 0,
        'regime_changes_total': 0,
        'shadow_evaluated_total': 0,
        'shadow_signal_total': 0,
    }
    breakdown = {
        'execution_mode': {},
        'strategy': {},
        'regime': {},
        'event_type': {},
    }

    try:
        projection = {
            '_id': 0,
            'user_id': 1,
            'symbol': 1,
            'strategy_id': 1,
            'regime_id': 1,
            'execution_mode': 1,
            'event_type_counts': 1,
            'events_total': 1,
            'live_events_total': 1,
            'shadow_events_total': 1,
            'router_events_total': 1,
            'signals_total': 1,
            'live_signals_total': 1,
            'shadow_signals_total': 1,
            'selected_total': 1,
            'trades_opened_total': 1,
            'regime_changes_total': 1,
            'shadow_evaluated_total': 1,
            'shadow_signal_total': 1,
            'last_seen_at': 1,
        }
        rows = list(strategy_runtime_summary_col.find(query, projection))
        users = set()
        symbols = set()
        strategies = set()
        regimes = set()
        counter_fields = [
            'events_total',
            'live_events_total',
            'shadow_events_total',
            'router_events_total',
            'signals_total',
            'live_signals_total',
            'shadow_signals_total',
            'selected_total',
            'trades_opened_total',
            'regime_changes_total',
            'shadow_evaluated_total',
            'shadow_signal_total',
        ]

        for row in rows:
            counts['summary_rows'] += 1
            users.add(row.get('user_id'))
            if row.get('symbol'):
                symbols.add(row.get('symbol'))
            if row.get('strategy_id'):
                strategies.add(row.get('strategy_id'))
            if row.get('regime_id'):
                regimes.add(row.get('regime_id'))

            for field in counter_fields:
                counts[field] += int(row.get(field) or 0)

            mode_key = _safe_counter_key(row.get('execution_mode'))
            strategy_key = _safe_counter_key(row.get('strategy_id'))
            regime_key = _safe_counter_key(row.get('regime_id'))

            mode_bucket = breakdown['execution_mode'].setdefault(mode_key, {'rows': 0, 'events_total': 0, 'signals_total': 0, 'selected_total': 0, 'trades_opened_total': 0, 'shadow_signal_total': 0})
            mode_bucket['rows'] += 1
            mode_bucket['events_total'] += int(row.get('events_total') or 0)
            mode_bucket['signals_total'] += int(row.get('signals_total') or 0)
            mode_bucket['selected_total'] += int(row.get('selected_total') or 0)
            mode_bucket['trades_opened_total'] += int(row.get('trades_opened_total') or 0)
            mode_bucket['shadow_signal_total'] += int(row.get('shadow_signal_total') or 0)

            strategy_bucket = breakdown['strategy'].setdefault(strategy_key, {'rows': 0, 'events_total': 0, 'signals_total': 0, 'selected_total': 0, 'trades_opened_total': 0, 'shadow_signal_total': 0})
            strategy_bucket['rows'] += 1
            strategy_bucket['events_total'] += int(row.get('events_total') or 0)
            strategy_bucket['signals_total'] += int(row.get('signals_total') or 0)
            strategy_bucket['selected_total'] += int(row.get('selected_total') or 0)
            strategy_bucket['trades_opened_total'] += int(row.get('trades_opened_total') or 0)
            strategy_bucket['shadow_signal_total'] += int(row.get('shadow_signal_total') or 0)

            regime_bucket = breakdown['regime'].setdefault(regime_key, {'rows': 0, 'events_total': 0, 'signals_total': 0, 'selected_total': 0, 'trades_opened_total': 0, 'shadow_signal_total': 0})
            regime_bucket['rows'] += 1
            regime_bucket['events_total'] += int(row.get('events_total') or 0)
            regime_bucket['signals_total'] += int(row.get('signals_total') or 0)
            regime_bucket['selected_total'] += int(row.get('selected_total') or 0)
            regime_bucket['trades_opened_total'] += int(row.get('trades_opened_total') or 0)
            regime_bucket['shadow_signal_total'] += int(row.get('shadow_signal_total') or 0)

            event_counts = row.get('event_type_counts') if isinstance(row.get('event_type_counts'), dict) else {}
            for raw_event_key, raw_event_count in event_counts.items():
                event_key = _safe_counter_key(raw_event_key)
                breakdown['event_type'][event_key] = int(breakdown['event_type'].get(event_key) or 0) + int(raw_event_count or 0)

        counts['unique_users'] = len([x for x in users if x is not None])
        counts['unique_symbols'] = len(symbols)
        counts['unique_strategies'] = len(strategies)
        counts['unique_regimes'] = len(regimes)
    except Exception as e:
        try:
            db_log(f"⚠ get_strategy_runtime_overview aggregate error query={query}: {e}")
        except Exception:
            pass
        return {
            'scope_user_id': int(user_id) if user_id is not None else None,
            'counts': counts,
            'breakdown': breakdown,
            'recent_events': [],
            'partial': True,
            'error': str(e),
        }

    recent_events = get_strategy_router_events(user_id=user_id, limit=max(1, min(int(limit_recent_events or 25), 200)))
    return {
        'scope_user_id': int(user_id) if user_id is not None else None,
        'counts': counts,
        'breakdown': breakdown,
        'recent_events': recent_events,
        'partial': False,
    }

# ============================================================
# USUARIOS
# ============================================================

def create_user(user_id: int, username: str):
    if not users_col.find_one({"user_id": int(user_id)}):
        users_col.insert_one({
            "user_id": int(user_id),
            "username": username,
            "wallet": None,
            "private_key": None,
            "private_key_encrypted": False,
            "private_key_version": None,
            "private_key_updated_at": None,
            "private_key_runtime_status": None,
            "private_key_runtime_error": None,
            "private_key_runtime_checked_at": None,
            "private_key_runtime_failure_count": 0,
            "private_key_runtime_last_failure_at": None,
            "capital": 0.0,
            "trading_status": "inactive",

            # ✅ Planes (trial/premium)
            "plan": "none",                 # none | trial | premium
            "plan_expires_at": None,        # datetime UTC naive
            "trial_used": False,            # trial una sola vez
            "expiry_notified_on": None,     # YYYY-MM-DD (Cuba) para evitar spam

            # ✅ Referidos (solo conteo de válidos)
            "referrer": None,                # user_id del referidor
            "referral_valid_count": 0,       # contador en el referidor
            "referral_counted": False,       # marca si este usuario ya contó como válido
            "terms_accepted": False,
            "terms_timestamp": None,
        })
        db_log(f"👤 Usuario creado {user_id}")

def is_user_registered(user_id: int) -> bool:
    return users_col.find_one({"user_id": int(user_id)}, {"_id": 1}) is not None

def get_all_users():
    return list(users_col.find({}, {"_id": 0, "user_id": 1}))

def save_user_wallet(user_id: int, wallet: str):
    users_col.update_one({"user_id": int(user_id)}, {"$set": {"wallet": wallet}})
    log_user_activity(int(user_id), 'Wallet actualizada', 'La wallet operativa fue actualizada desde la plataforma.', tone='info', event_type='wallet_updated', metadata={'wallet_masked': str(wallet)[:8] + '...' + str(wallet)[-6:] if wallet else None})


def clear_user_private_key_runtime_issue(user_id: int) -> bool:
    try:
        uid = int(user_id)
    except Exception:
        return False
    now = _now_utc()
    try:
        users_col.update_one(
            {"user_id": uid},
            {
                "$set": {
                    "private_key_runtime_status": "ok",
                    "private_key_runtime_error": None,
                    "private_key_runtime_checked_at": now,
                    "private_key_runtime_last_recovered_at": now,
                    "private_key_runtime_failure_count": 0,
                },
                "$unset": {
                    "private_key_runtime_last_failure_at": "",
                    "private_key_runtime_failure_kind": "",
                    "private_key_runtime_cipher_version": "",
                },
            },
        )
        return True
    except Exception as e:
        try:
            db_log(f"⚠ clear_user_private_key_runtime_issue error user_id={uid}: {e}")
        except Exception:
            pass
        return False


def mark_user_private_key_runtime_issue(
    user_id: int,
    *,
    reason: str,
    failure_kind: str = 'decrypt_error',
    version: str | None = None,
) -> bool:
    try:
        uid = int(user_id)
    except Exception:
        return False
    now = _now_utc()
    try:
        users_col.update_one(
            {"user_id": uid},
            {
                "$set": {
                    "private_key_runtime_status": str(failure_kind or 'decrypt_error').strip().lower() or 'decrypt_error',
                    "private_key_runtime_error": str(reason or 'No se pudo validar la private key almacenada.')[:300],
                    "private_key_runtime_checked_at": now,
                    "private_key_runtime_last_failure_at": now,
                    "private_key_runtime_failure_kind": str(failure_kind or 'decrypt_error').strip().lower() or 'decrypt_error',
                    "private_key_runtime_cipher_version": str(version).strip() if version else None,
                },
                "$inc": {"private_key_runtime_failure_count": 1},
            },
        )
        return True
    except Exception as e:
        try:
            db_log(f"⚠ mark_user_private_key_runtime_issue error user_id={uid}: {e}")
        except Exception:
            pass
        return False


def reset_user_private_key_credentials(user_id: int) -> dict:
    try:
        uid = int(user_id)
    except Exception:
        return {'ok': False, 'result': 'invalid_user_id', 'message': 'Usuario inválido'}

    user = users_col.find_one(
        {'user_id': uid},
        {
            '_id': 0,
            'user_id': 1,
            'wallet': 1,
            'private_key': 1,
            'private_key_encrypted': 1,
            'private_key_version': 1,
            'trading_status': 1,
        },
    )
    if not user:
        return {'ok': False, 'result': 'user_not_found', 'message': 'Usuario no encontrado'}

    now = _now_utc()
    had_private_key = bool(user.get('private_key'))
    preserve_wallet = bool(user.get('wallet'))
    try:
        users_col.update_one(
            {'user_id': uid},
            {
                '$set': {
                    'private_key': None,
                    'private_key_encrypted': False,
                    'private_key_version': None,
                    'private_key_updated_at': None,
                    'private_key_runtime_status': None,
                    'private_key_runtime_error': None,
                    'private_key_runtime_checked_at': None,
                    'private_key_runtime_failure_count': 0,
                    'private_key_runtime_last_recovered_at': None,
                    'trading_status': 'inactive',
                    'last_bot_error': None,
                    'last_bot_error_at': None,
                    'updated_at': now,
                },
                '$unset': {
                    'private_key_runtime_last_failure_at': '',
                    'private_key_runtime_failure_kind': '',
                    'private_key_runtime_cipher_version': '',
                },
            },
        )
    except Exception as e:
        try:
            db_log(f"⚠ reset_user_private_key_credentials error user_id={uid}: {e}")
        except Exception:
            pass
        return {'ok': False, 'result': 'db_error', 'message': 'No se pudo resetear la credencial del usuario'}

    return {
        'ok': True,
        'result': 'credentials_reset',
        'message': 'Credencial operativa reseteada. El usuario debe volver a configurar su private key.',
        'user_id': uid,
        'had_private_key': had_private_key,
        'wallet_preserved': preserve_wallet,
        'trading_paused': True,
    }


def save_user_private_key(user_id: int, pk: str):
    encrypted_pk, version = encrypt_private_key(pk)
    users_col.update_one(
        {"user_id": int(user_id)},
        {
            "$set": {
                "private_key": encrypted_pk,
                "private_key_encrypted": True,
                "private_key_version": version,
                "private_key_updated_at": datetime.utcnow(),
            }
        },
    )
    clear_user_private_key_runtime_issue(int(user_id))
    log_user_activity(int(user_id), 'Clave operativa actualizada', 'La private key quedó registrada de forma segura.', tone='info', event_type='private_key_updated')

def get_raw_user_private_key_record(user_id: int) -> dict | None:
    return users_col.find_one(
        {"user_id": int(user_id)},
        {
            "_id": 0,
            "user_id": 1,
            "private_key": 1,
            "private_key_encrypted": 1,
            "private_key_version": 1,
            "private_key_updated_at": 1,
        },
    )


def migrate_user_private_key_to_encrypted(user_id: int) -> dict:
    doc = get_raw_user_private_key_record(int(user_id))
    if not doc:
        return {"result": "user_not_found", "changed": False}

    current_value = doc.get("private_key")
    if not current_value:
        return {"result": "not_configured", "changed": False}

    if bool(doc.get("private_key_encrypted", False)):
        return {
            "result": "already_encrypted",
            "changed": False,
            "version": doc.get("private_key_version"),
        }

    save_user_private_key(int(user_id), str(current_value))
    return {"result": "migrated", "changed": True, "version": "fernet-v1"}


def migrate_legacy_private_keys(limit: int = 25) -> dict:
    lim = max(1, min(int(limit), 100))
    cursor = users_col.find(
        {
            "private_key": {"$ne": None},
            "$or": [
                {"private_key_encrypted": {"$exists": False}},
                {"private_key_encrypted": False},
            ],
        },
        {"_id": 0, "user_id": 1},
    ).sort("user_id", 1).limit(lim)

    migrated_user_ids = []
    skipped_user_ids = []
    for row in cursor:
        uid = int(row.get("user_id"))
        outcome = migrate_user_private_key_to_encrypted(uid)
        if outcome.get("changed"):
            migrated_user_ids.append(uid)
        else:
            skipped_user_ids.append(uid)

    remaining_legacy = users_col.count_documents({
        "private_key": {"$ne": None},
        "$or": [
            {"private_key_encrypted": {"$exists": False}},
            {"private_key_encrypted": False},
        ],
    })

    return {
        "requested_limit": lim,
        "migrated_count": len(migrated_user_ids),
        "migrated_user_ids": migrated_user_ids,
        "skipped_user_ids": skipped_user_ids,
        "remaining_legacy_plaintext_keys": int(remaining_legacy),
    }

def save_user_capital(user_id: int, capital: float):
    capital = _clamp_non_negative(_safe_float(capital, 0.0))
    users_col.update_one({"user_id": int(user_id)}, {"$set": {"capital": capital}})

def set_trading_status(user_id: int, status: str):
    normalized = str(status or '').strip().lower() or 'inactive'
    uid = int(user_id)
    users_col.update_one({"user_id": uid}, {"$set": {"trading_status": normalized}})

    active_trade = get_user_active_trade_snapshot(uid) or {}
    if normalized == 'active':
        log_user_activity(uid, 'Trading activado', 'El motor quedó habilitado para operar con tu configuración actual.', tone='success', event_type='trading_activated')
        touch_user_operational_state(
            uid,
            'activation_requested',
            'Solicitud registrada. El motor aplicará la activación en su próximo ciclo.',
            mode='pending_sync',
            source='control',
            live_trade=bool(active_trade),
            active_symbol=(active_trade or {}).get('symbol'),
        )
    else:
        if active_trade:
            detail = 'Nuevas entradas pausadas. La posición activa seguirá bajo gestión hasta cerrarse.'
            mode = 'manager_only'
            state = 'manager_only'
        else:
            detail = 'Trading pausado. No se abrirán nuevas operaciones hasta que lo reanudes.'
            mode = 'paused'
            state = 'paused'
        log_user_activity(uid, 'Trading pausado', detail, tone='warning', event_type='trading_paused')
        touch_user_operational_state(
            uid,
            state,
            detail,
            mode=mode,
            source='control',
            live_trade=bool(active_trade),
            active_symbol=(active_trade or {}).get('symbol'),
        )


# ============================================================
# REFERIDOS (SOLO CONTEO DE VÁLIDOS)
# ============================================================

def set_referrer(user_id: int, referrer_id: int):
    """
    Asigna el referidor una sola vez (si no existe ya).
    No permite auto-referido.
    """
    try:
        user_id = int(user_id)
        referrer_id = int(referrer_id)
        if user_id == referrer_id:
            return

        users_col.update_one(
            {"user_id": user_id, "$or": [{"referrer": None}, {"referrer": {"$exists": False}}]},
            {"$set": {"referrer": referrer_id}}
        )
    except Exception as e:
        db_log(f"❌ Error set_referrer user={user_id}: {e}")

def get_user_referrer(user_id: int):
    u = users_col.find_one({"user_id": int(user_id)}, {"_id": 0, "referrer": 1})
    return (u or {}).get("referrer")

def get_referral_valid_count(referrer_id: int) -> int:
    u = users_col.find_one({"user_id": int(referrer_id)}, {"_id": 0, "referral_valid_count": 1})
    try:
        return int((u or {}).get("referral_valid_count", 0) or 0)
    except Exception:
        return 0

def _mark_referral_valid(target_user_id: int):
    """
    Compatibilidad legacy.
    Delega en el nuevo registro transaccional de conversión referida.
    """
    result = register_referral_conversion(int(target_user_id))
    return bool(result.get('counted'))


def register_referral_conversion(target_user_id: int) -> dict:
    """
    Marca una conversión de referido válida una sola vez.

    Regla de negocio actual:
    - El referido cuenta como válido en su primera compra Premium (15 o 30 días).
    - El conteo del referrer se incrementa una sola vez.
    - No aplica recompensa aquí; solo registra la conversión válida.
    """
    try:
        uid = int(target_user_id)
    except Exception:
        return {'ok': False, 'counted': False, 'result': 'invalid_user_id'}

    now = _now_utc()
    try:
        claimed = users_col.find_one_and_update(
            {
                'user_id': uid,
                'referrer': {'$ne': None},
                '$or': [
                    {'referral_counted': False},
                    {'referral_counted': {'$exists': False}},
                ],
            },
            {
                '$set': {
                    'referral_counted': True,
                    'referral_converted_at': now,
                    'updated_at': now,
                }
            },
            projection={'_id': 0, 'user_id': 1, 'referrer': 1},
            return_document=ReturnDocument.AFTER,
        )
    except Exception as e:
        db_log(f"❌ Error register_referral_conversion user={uid}: {e}")
        return {'ok': False, 'counted': False, 'result': 'db_error', 'error': str(e)}

    if claimed:
        referrer_id = int(claimed.get('referrer'))
        try:
            users_col.update_one(
                {'user_id': referrer_id},
                {
                    '$inc': {'referral_valid_count': 1},
                    '$set': {'updated_at': now},
                },
            )
        except Exception as e:
            db_log(f"❌ Error incrementando referral_valid_count referrer={referrer_id} user={uid}: {e}")
            return {
                'ok': False,
                'counted': False,
                'result': 'referrer_increment_failed',
                'referrer_id': referrer_id,
                'error': str(e),
            }
        db_log(f"👥 Referido válido contado: referrer={referrer_id} user={uid}")
        return {
            'ok': True,
            'counted': True,
            'result': 'counted',
            'referrer_id': referrer_id,
            'converted_at': now,
        }

    try:
        existing = users_col.find_one({'user_id': uid}, {'_id': 0, 'referrer': 1, 'referral_counted': 1}) or {}
    except Exception as e:
        db_log(f"❌ Error leyendo conversión referida user={uid}: {e}")
        return {'ok': False, 'counted': False, 'result': 'db_error', 'error': str(e)}

    if not existing:
        return {'ok': False, 'counted': False, 'result': 'user_not_found'}
    referrer_id = existing.get('referrer')
    if not referrer_id:
        return {'ok': True, 'counted': False, 'result': 'no_referrer'}
    if bool(existing.get('referral_counted', False)):
        return {'ok': True, 'counted': False, 'result': 'already_counted', 'referrer_id': int(referrer_id)}
    return {'ok': True, 'counted': False, 'result': 'not_eligible', 'referrer_id': int(referrer_id)}


def _build_referral_reward_event(
    *,
    referrer_id: int,
    referred_user_id: int,
    source_order_id: str,
    purchase_days: int,
    reward_days: int,
    tx_hash: str | None = None,
    amount_usdt: float | None = None,
    referrer_previous_plan: str | None = None,
    referrer_previous_expires_at: datetime | None = None,
    referrer_new_expires_at: datetime | None = None,
    metadata: dict | None = None,
) -> dict:
    now = _now_utc()
    return {
        'referrer_id': int(referrer_id),
        'referred_user_id': int(referred_user_id),
        'source_order_id': str(source_order_id),
        'reward_plan': 'premium',
        'purchase_plan': 'premium',
        'purchase_days': int(purchase_days),
        'reward_days': int(reward_days),
        'tx_hash': str(tx_hash or '') or None,
        'amount_usdt': float(amount_usdt) if amount_usdt is not None else None,
        'referrer_previous_plan': str(referrer_previous_plan or 'none'),
        'referrer_previous_expires_at': referrer_previous_expires_at,
        'referrer_new_expires_at': referrer_new_expires_at,
        'metadata': metadata or {},
        'created_at': now,
        'updated_at': now,
    }


def apply_referral_reward_for_premium_purchase(
    referred_user_id: int,
    purchase_days: int,
    *,
    source_order_id: str,
    tx_hash: str | None = None,
    amount_usdt: float | None = None,
    metadata: dict | None = None,
) -> dict:
    """
    Aplica la recompensa del sistema referido una sola vez por referido válido.

    Regla vigente:
    - Premium 15 días comprado por el referido => 7 días premium al referrer.
    - Premium 30 días comprado por el referido => 15 días premium al referrer.
    - La recompensa solo se concede en la primera compra válida del referido.
    """
    try:
        referred_uid = int(referred_user_id)
        purchase_day_value = int(purchase_days)
    except Exception:
        return {'ok': False, 'applied': False, 'result': 'invalid_input'}

    reward_days = get_referral_reward_days_for_purchase(purchase_day_value)
    if reward_days <= 0:
        return {'ok': True, 'applied': False, 'result': 'reward_not_configured'}

    conversion = register_referral_conversion(referred_uid)
    if not conversion.get('ok'):
        return {'ok': False, 'applied': False, 'result': conversion.get('result') or 'conversion_failed', 'conversion': conversion}
    if not conversion.get('counted'):
        return {
            'ok': True,
            'applied': False,
            'result': conversion.get('result') or 'not_eligible',
            'conversion': conversion,
        }

    referrer_id = int(conversion.get('referrer_id'))
    referrer = users_col.find_one({'user_id': referrer_id}, {'_id': 0, 'plan': 1, 'plan_expires_at': 1, 'username': 1})
    if not referrer:
        db_log(f"❌ apply_referral_reward_for_premium_purchase referrer missing referred={referred_uid} referrer={referrer_id}")
        return {'ok': False, 'applied': False, 'result': 'referrer_not_found', 'conversion': conversion}

    previous_plan = referrer.get('plan') or 'none'
    previous_exp = _parse_dt(referrer.get('plan_expires_at'))
    new_exp = _midnight_cuba_after_days_from_base(previous_exp, reward_days)
    now = _now_utc()

    users_col.update_one(
        {'user_id': referrer_id},
        {
            '$set': {
                'plan': 'premium',
                'plan_expires_at': new_exp,
                'expiry_notified_on': None,
                'last_referral_reward_at': now,
                'last_referral_reward_days': reward_days,
                'last_referral_reward_source_order_id': str(source_order_id),
                'last_referral_reward_purchase_days': purchase_day_value,
                'updated_at': now,
            }
        },
    )

    reward_event = _build_referral_reward_event(
        referrer_id=referrer_id,
        referred_user_id=referred_uid,
        source_order_id=str(source_order_id),
        purchase_days=purchase_day_value,
        reward_days=reward_days,
        tx_hash=tx_hash,
        amount_usdt=amount_usdt,
        referrer_previous_plan=previous_plan,
        referrer_previous_expires_at=previous_exp,
        referrer_new_expires_at=new_exp,
        metadata=metadata,
    )
    try:
        referral_reward_events_col.insert_one(reward_event)
    except Exception as e:
        db_log(f"⚠ referral reward event insert error order={source_order_id} referrer={referrer_id}: {e}")

    users_col.update_one(
        {'user_id': referred_uid},
        {
            '$set': {
                'referral_rewarded_at': now,
                'referral_reward_source_order_id': str(source_order_id),
                'referral_reward_purchase_days': purchase_day_value,
                'referral_reward_days': reward_days,
                'referral_reward_referrer_id': referrer_id,
                'referral_reward_status': 'applied',
                'updated_at': now,
            }
        },
    )

    log_subscription_event(
        referrer_id,
        'referral_reward',
        plan='premium',
        days=reward_days,
        source='referral_program',
        before_plan=previous_plan,
        before_expires_at=previous_exp,
        after_plan='premium',
        after_expires_at=new_exp,
        order_id=str(source_order_id),
        metadata={
            **(metadata or {}),
            'referred_user_id': referred_uid,
            'purchase_days': purchase_day_value,
            'tx_hash': tx_hash,
            'amount_usdt': float(amount_usdt) if amount_usdt is not None else None,
        },
    )
    log_user_activity(
        referrer_id,
        'Recompensa de referido aplicada',
        f'Recibiste {reward_days} día(s) Premium porque tu referido activó Premium {purchase_day_value} días.',
        tone='success',
        event_type='referral_reward',
        metadata={
            'referred_user_id': referred_uid,
            'purchase_days': purchase_day_value,
            'reward_days': reward_days,
            'source_order_id': str(source_order_id),
        },
        occurred_at=now,
    )
    log_user_activity(
        referred_uid,
        'Compra validada como referido',
        'Tu compra contó como referido válido para el programa actual.',
        tone='info',
        event_type='referral_conversion',
        metadata={
            'referrer_id': referrer_id,
            'purchase_days': purchase_day_value,
            'reward_days_for_referrer': reward_days,
            'source_order_id': str(source_order_id),
        },
        occurred_at=now,
    )
    db_log(
        f"🎁 Recompensa referida aplicada referrer={referrer_id} referred={referred_uid} purchase_days={purchase_day_value} reward_days={reward_days}"
    )
    return {
        'ok': True,
        'applied': True,
        'result': 'reward_applied',
        'referrer_id': referrer_id,
        'referred_user_id': referred_uid,
        'purchase_days': purchase_day_value,
        'reward_days': reward_days,
        'new_expires_at': new_exp,
        'conversion': conversion,
    }


def get_user_wallet(user_id: int):
    u = users_col.find_one({"user_id": int(user_id)})
    return u.get("wallet") if u else None

def get_user_private_key(user_id: int):
    uid = int(user_id)
    u = users_col.find_one(
        {"user_id": uid},
        {
            "_id": 0,
            "private_key": 1,
            "private_key_encrypted": 1,
            "private_key_version": 1,
            "private_key_runtime_status": 1,
        },
    )
    if not u:
        return None
    value = u.get("private_key")
    if not value:
        return None
    try:
        decrypted = decrypt_private_key(
            value,
            encrypted=bool(u.get("private_key_encrypted", False)),
            version=u.get("private_key_version"),
        )
    except PrivateKeyDecryptError as exc:
        mark_user_private_key_runtime_issue(
            uid,
            reason=str(exc),
            failure_kind='decrypt_error',
            version=u.get("private_key_version"),
        )
        raise

    if str(u.get('private_key_runtime_status') or '').strip().lower() == 'decrypt_error':
        clear_user_private_key_runtime_issue(uid)
    return decrypted

def get_user_capital(user_id: int):
    u = users_col.find_one({"user_id": int(user_id)})
    return _safe_float(u.get("capital", 0.0), 0.0) if u else 0.0

def _plan_is_active(u: dict) -> bool:
    plan = (u or {}).get("plan") or "none"
    exp = _parse_dt((u or {}).get("plan_expires_at"))
    if plan not in ("trial", "premium"):
        return False
    if not exp:
        return False
    return _now_utc() < exp


def _days_remaining_from_exp(exp: datetime | None) -> int:
    exp = _parse_dt(exp)
    if not exp:
        return 0
    remaining = exp - _now_utc()
    if remaining.total_seconds() <= 0:
        return 0
    return max(1, int((remaining.total_seconds() + 86399) // 86400))


MANUAL_PLAN_OPTIONS = {"trial", "premium"}


def _normalize_manual_plan(plan: str | None) -> str:
    normalized = str(plan or '').strip().lower()
    return normalized if normalized in MANUAL_PLAN_OPTIONS else 'premium'


def _manual_plan_label(plan: str | None) -> str:
    normalized = _normalize_manual_plan(plan)
    return 'PRUEBA' if normalized == 'trial' else 'PREMIUM'


def get_manual_plan_days_preview(target_user_id: int, target_plan: str, days: int) -> dict:
    """
    Previsualiza el efecto de una extensión manual sin persistir cambios.
    """
    try:
        target_user_id = int(target_user_id)
        days = int(days)
        target_plan = _normalize_manual_plan(target_plan)
        if days <= 0:
            return {"ok": False, "message": "La cantidad de días debe ser mayor que cero"}

        u = users_col.find_one({"user_id": target_user_id}, {"plan": 1, "plan_expires_at": 1, "trial_used": 1})
        if not u:
            return {"ok": False, "message": "Usuario no encontrado"}

        previous_plan = (u.get("plan") or "none")
        previous_exp = _parse_dt(u.get("plan_expires_at"))
        has_active_access = _plan_is_active(u)

        if target_plan == 'trial' and has_active_access and previous_plan == 'premium':
            return {
                "ok": False,
                "message": "No se puede aplicar PRUEBA mientras el usuario tenga PREMIUM activo",
            }

        base_type = "current_expiry" if has_active_access and previous_exp else "today"
        exp_utc = _midnight_cuba_after_days_from_base(previous_exp, days)

        return {
            "ok": True,
            "days": days,
            "target_plan": target_plan,
            "target_plan_label": _manual_plan_label(target_plan),
            "previous_plan": previous_plan,
            "previous_expires_at": previous_exp,
            "previous_days_remaining": _days_remaining_from_exp(previous_exp) if has_active_access else 0,
            "new_plan": target_plan,
            "new_plan_label": _manual_plan_label(target_plan),
            "new_expires_at": exp_utc,
            "new_days_remaining": _days_remaining_from_exp(exp_utc),
            "base_type": base_type,
        }
    except Exception as e:
        db_log(f"❌ Error previsualizando plan manual user={target_user_id} plan={target_plan}: {e}")
        return {"ok": False, "message": "Error interno al calcular la previsualización"}


def get_manual_premium_days_preview(target_user_id: int, days: int) -> dict:
    return get_manual_plan_days_preview(target_user_id, 'premium', days)


def user_is_ready(user_id: int) -> bool:
    u = users_col.find_one({"user_id": int(user_id)})
    if not u:
        return False

    # ✅ Debe tener plan activo (trial o premium)
    if not _plan_is_active(u):
        return False

    return bool(
        u.get("wallet") and
        u.get("private_key") and
        u.get("trading_status") == "active"
    )

# ============================================================
# PLANES (TRIAL / PREMIUM)
# ============================================================

def ensure_access_on_activate(user_id: int) -> dict:
    """
    Llamar cuando el usuario toca "Activar Trading".
    - Si tiene premium activo => allowed
    - Si tiene trial activo => allowed
    - Si no tiene plan activo y NO ha usado trial => inicia trial 5 días (vence medianoche Cuba)
    - Si ya usó trial y no tiene premium => bloqueado
    """
    u = users_col.find_one({"user_id": int(user_id)})
    if not u:
        return {"allowed": False, "message": "❌ Usuario no registrado."}

    private_key_runtime_status = str(u.get('private_key_runtime_status') or '').strip().lower() or None
    if private_key_runtime_status in {'decrypt_error', 'invalid', 'unsupported_version'}:
        return {
            "allowed": False,
            "message": "⛔ La private key almacenada no pudo validarse. Reconfigúrala en la MiniApp antes de activar trading.",
        }

    # Premium activo
    if _plan_is_active(u) and (u.get("plan") == "premium"):
        return {"allowed": True, "plan_message": "🟢 *Trading ACTIVADO*\nPlan: *PREMIUM* ✅"}

    # Trial activo
    if _plan_is_active(u) and (u.get("plan") == "trial"):
        exp = _parse_dt(u.get("plan_expires_at"))
        if exp:
            exp_cuba = exp.replace(tzinfo=pytz.UTC).astimezone(CUBA_TZ)
            exp_str = exp_cuba.strftime("%Y-%m-%d 00:00 Cuba")
        else:
            exp_str = ""
        return {"allowed": True, "plan_message": f"🟢 *Trading ACTIVADO*\nPlan: *PRUEBA* ✅\nVence: *{exp_str}*"}

    # Iniciar trial (una sola vez)
    if not bool(u.get("trial_used", False)):
        exp_utc = _midnight_cuba_after_days(5)
        users_col.update_one(
            {"user_id": int(user_id)},
            {"$set": {"plan": "trial", "plan_expires_at": exp_utc, "trial_used": True}}
        )
        exp_cuba = exp_utc.replace(tzinfo=pytz.UTC).astimezone(CUBA_TZ)
        exp_str = exp_cuba.strftime("%Y-%m-%d 00:00 Cuba")
        db_log(f"✅ Trial iniciado user={user_id} exp={exp_utc.isoformat()}")
        return {"allowed": True, "plan_message": f"🟢 *Trading ACTIVADO*\nPlan: *PRUEBA* ✅\nVence: *{exp_str}*"}
    # Ya usó trial
    return {
        "allowed": False,
        "message": "⛔ Tu prueba terminó.\nPara seguir utilizando el bot, contacta al administrador."
    }

def _midnight_cuba_after_days_from_base(base_dt: datetime | None, days: int) -> datetime:
    """
    Calcula la próxima expiración a medianoche Cuba sumando días completos
    sobre una fecha base.

    - Si `base_dt` existe y sigue vigente, se usa como base para extender.
    - Si no existe o ya venció, el conteo parte desde hoy en Cuba.
    """
    base_utc = _parse_dt(base_dt)
    if base_utc and base_utc > _now_utc():
        base_local = base_utc.replace(tzinfo=pytz.UTC).astimezone(CUBA_TZ)
        base_date = base_local.date()
    else:
        base_date = _now_cuba().date()

    target_date = base_date + timedelta(days=int(days))
    midnight_local = CUBA_TZ.localize(datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0))
    return midnight_local.astimezone(pytz.UTC).replace(tzinfo=None)


def grant_manual_plan_days(target_user_id: int, target_plan: str, days: int) -> dict:
    """
    Extensión manual de acceso por una cantidad exacta de días.

    Reglas:
    - Si el usuario tiene acceso vigente, suma desde el vencimiento actual.
    - Si no tiene acceso vigente, parte desde hoy (hora Cuba).
    - Si el plan destino es PREMIUM, el usuario queda actualizado inmediatamente a premium.
    - Si el plan destino es PRUEBA, no se permite degradar un PREMIUM activo.
    - No marca referidos válidos: esto es una extensión/admin reward, no una compra.
    """
    try:
        target_user_id = int(target_user_id)
        days = int(days)
        target_plan = _normalize_manual_plan(target_plan)
        if days <= 0:
            return {"ok": False, "message": "La cantidad de días debe ser mayor que cero"}

        u = users_col.find_one({"user_id": target_user_id}, {"plan": 1, "plan_expires_at": 1, "trial_used": 1})
        if not u:
            return {"ok": False, "message": "Usuario no encontrado"}

        previous_plan = (u.get("plan") or "none")
        previous_exp = _parse_dt(u.get("plan_expires_at"))
        has_active_access = _plan_is_active(u)

        if target_plan == 'trial' and has_active_access and previous_plan == 'premium':
            return {"ok": False, "message": "No se puede aplicar PRUEBA mientras el usuario tenga PREMIUM activo"}

        exp_utc = _midnight_cuba_after_days_from_base(previous_exp, days)
        set_fields = {
            "plan": target_plan,
            "plan_expires_at": exp_utc,
            "expiry_notified_on": None,
        }
        if target_plan == 'trial':
            set_fields['trial_used'] = True

        users_col.update_one({"user_id": target_user_id}, {"$set": set_fields})

        plan_label = _manual_plan_label(target_plan)
        db_log(
            f"🎁 Plan manual user={target_user_id} plan={target_plan} days={days} prev_plan={previous_plan} prev_exp={previous_exp.isoformat() if previous_exp else 'none'} new_exp={exp_utc.isoformat()}"
        )
        log_user_activity(
            int(target_user_id),
            f"Acceso actualizado · {plan_label}",
            f"Tu acceso fue extendido manualmente por {days} día(s). Nuevo vencimiento {exp_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC.",
            tone='success',
            event_type='access_updated',
            occurred_at=_now_utc(),
            metadata={
                'plan': target_plan,
                'days': int(days),
                'new_expires_at': exp_utc.isoformat(),
            },
        )
        return {
            "ok": True,
            "message": f"{plan_label} actualizado manualmente por {days} días",
            "target_plan": target_plan,
            "target_plan_label": plan_label,
            "previous_plan": previous_plan,
            "previous_expires_at": previous_exp,
            "new_plan": target_plan,
            "new_plan_label": plan_label,
            "new_expires_at": exp_utc,
            "new_days_remaining": _days_remaining_from_exp(exp_utc),
            "days": days,
        }
    except Exception as e:
        db_log(f"❌ Error aplicando plan manual user={target_user_id} plan={target_plan}: {e}")
        return {"ok": False, "message": "Error interno al aplicar la extensión manual"}


def grant_manual_premium_days(target_user_id: int, days: int) -> dict:
    return grant_manual_plan_days(target_user_id, 'premium', days)


def activate_premium_plan(target_user_id: int) -> bool:
    """
    Compatibilidad legacy: activación manual premium de 30 días.

    Nota: no marca referidos válidos. Las compras automáticas futuras
    deben contabilizarse por su propio flujo de pagos.
    """
    outcome = grant_manual_premium_days(int(target_user_id), 30)
    return bool(outcome.get("ok"))

def is_plan_expired(user_id: int) -> bool:
    u = users_col.find_one({"user_id": int(user_id)}, {"_id": 0, "plan": 1, "plan_expires_at": 1})
    if not u:
        return False
    plan = u.get("plan") or "none"
    exp = _parse_dt(u.get("plan_expires_at"))
    if plan not in ("trial", "premium") or not exp:
        return False
    return _now_utc() >= exp

def should_notify_expired(user_id: int) -> bool:
    """
    True si está vencido y no se notificó hoy (hora Cuba).
    """
    u = users_col.find_one({"user_id": int(user_id)}, {"_id": 0, "expiry_notified_on": 1})
    if not u:
        return False
    today_cuba = _now_cuba().strftime("%Y-%m-%d")
    return u.get("expiry_notified_on") != today_cuba

def mark_expiry_notified(user_id: int):
    today_cuba = _now_cuba().strftime("%Y-%m-%d")
    users_col.update_one({"user_id": int(user_id)}, {"$set": {"expiry_notified_on": today_cuba}})

# ============================================================
# TRADES + INTERÉS COMPUESTO
# ============================================================

def register_trade(user_id, symbol, side, entry_price, exit_price, qty, profit, best_score, **extra_fields):
    """
    Registra el trade.

    Nota:
    - No aplica interés compuesto (capital configurado eliminado).
    - El sizing/uso de capital debe venir del balance real del exchange.
    - `profit` debe llegar NETO (realized pnl - fees).
    - `extra_fields` permite persistir metadatos de auditoría sin romper llamadas antiguas.
    """
    doc = {
        "user_id": int(user_id),
        "symbol": str(symbol),
        "side": str(side),
        "entry_price": _safe_float(entry_price, 0.0),
        "exit_price": _safe_float(exit_price, 0.0),
        "qty": _safe_float(qty, 0.0),
        "profit": _safe_float(profit, 0.0),
        "best_score": _safe_float(best_score, 0.0),
        "timestamp": datetime.utcnow(),
    }

    for key, value in (extra_fields or {}).items():
        if value is None:
            continue
        if key in {"fees", "gross_pnl", "realized_fills", "opened_at_ms"}:
            doc[key] = _safe_float(value, 0.0)
        elif key in {"pnl_source", "exit_reason", "close_source", "direction"}:
            doc[key] = str(value)
        elif key == "metadata" and isinstance(value, dict):
            doc[key] = value
        else:
            doc[key] = value

    trades_col.insert_one(doc)

def get_user_trades(user_id: int):
    return list(
        trades_col.find({"user_id": int(user_id)}, {"_id": 0})
        .sort("timestamp", -1)
        .limit(20)
    )

# ============================================================
# ADMIN – INFORMACIÓN VISUAL (STATS)
# ============================================================

def get_admin_visual_stats() -> dict:
    """
    Retorna métricas globales para el panel admin:
    - total_users
    - free_old
    - premium_active
    - premium_expired
    """
    total_users = users_col.count_documents({})

    now = datetime.utcnow()

    premium_active = users_col.count_documents({
        "plan": "premium",
        "plan_expires_at": {"$gt": now}
    })

    premium_expired = users_col.count_documents({
        "plan": "premium",
        "plan_expires_at": {"$lte": now}
    })

    free_old = users_col.count_documents({
        "$or": [
            {"plan": {"$exists": False}},
            {"plan": None},
            {"plan": "trial"}
        ],
        "trial_used": True
    })

    return {
        "total_users": int(total_users),
        "free_old": int(free_old),
        "premium_active": int(premium_active),
        "premium_expired": int(premium_expired),
    }

# ============================================================
# OPERACIÓN ACTUAL / ÚLTIMA (INFO PARA BOT)
# ============================================================

def save_last_open(user_id: int, open_data: dict):
    """
    Guarda la información de la última operación ABIERTA.
    Se sobreescribe siempre (solo informativo).
    """
    now = datetime.utcnow()
    payload = dict(open_data or {})
    users_col.update_one(
        {"user_id": int(user_id)},
        {"$set": {"last_open": payload, "last_open_at": now}}
    )
    symbol = payload.get('symbol') or payload.get('coin') or 'Operación'
    side = str(payload.get('side') or payload.get('direction') or '').upper()
    detail_parts = []
    if payload.get('entry_price') is not None:
        detail_parts.append(f"Entrada {payload.get('entry_price')}")
    if payload.get('qty') is not None:
        detail_parts.append(f"Qty {payload.get('qty')}")
    if payload.get('notional_usdc') is not None:
        detail_parts.append(f"Valor {payload.get('notional_usdc')} USDC")
    if payload.get('message'):
        detail_parts.append(str(payload.get('message')))
    detail = ' · '.join(detail_parts) if detail_parts else 'Se registró una nueva apertura en el bot.'
    title = f"Apertura {symbol}" if not side else f"Apertura {symbol} · {side}"
    log_user_activity(int(user_id), title, detail, tone='info', event_type='trade_opened', metadata={'symbol': symbol, 'side': side or None}, occurred_at=now)

def save_last_close(user_id: int, close_data: dict):
    """
    Guarda la información de la última operación CERRADA.
    Se sobreescribe siempre (solo informativo).
    Además deja una notificación pendiente para que el trading loop
    pueda enviar el cierre al usuario/admin cuando el manager cierra en background.
    """
    now = datetime.utcnow()
    payload = dict(close_data or {})
    users_col.update_one(
        {"user_id": int(user_id)},
        {"$set": {
            "last_close": payload,
            "last_close_at": now,
            "pending_close_notification": payload,
            "pending_close_notification_at": now,
        }}
    )
    symbol = payload.get('symbol') or payload.get('coin') or 'Operación'
    side = str(payload.get('side') or payload.get('direction') or '').upper()
    pnl = _safe_float(payload.get('profit'), 0.0) if payload.get('profit') is not None else None
    tone = 'success' if pnl is not None and pnl > 0 else ('danger' if pnl is not None and pnl < 0 else 'info')
    detail_parts = []
    if payload.get('entry_price') is not None:
        detail_parts.append(f"Entrada {payload.get('entry_price')}")
    if payload.get('exit_price') is not None:
        detail_parts.append(f"Salida {payload.get('exit_price')}")
    if payload.get('qty') is not None:
        detail_parts.append(f"Qty {payload.get('qty')}")
    if payload.get('notional_usdc') is not None:
        detail_parts.append(f"Valor {payload.get('notional_usdc')} USDC")
    if payload.get('gross_pnl') is not None:
        detail_parts.append(f"Bruto {round(_safe_float(payload.get('gross_pnl'), 0.0), 4)}")
    if payload.get('fees') is not None:
        detail_parts.append(f"Fees {round(_safe_float(payload.get('fees'), 0.0), 4)}")
    if pnl is not None:
        detail_parts.append(f"Neto {round(pnl, 4)}")
    if payload.get('message'):
        detail_parts.append(str(payload.get('message')))
    detail = ' · '.join(detail_parts) if detail_parts else 'Se registró un cierre en el bot.'
    title = f"Cierre {symbol}" if not side else f"Cierre {symbol} · {side}"
    log_user_activity(int(user_id), title, detail, tone=tone, event_type='trade_closed', metadata={'symbol': symbol, 'side': side or None, 'profit': pnl}, occurred_at=now)


def pop_pending_close_notification(user_id: int) -> dict | None:
    """
    Consume la última notificación pendiente de cierre.
    Se usa para cierres ejecutados por el manager en background, donde el loop
    no recibe un evento CLOSE directo pero sí debe notificar y reflejar el cierre.
    """
    try:
        uid = int(user_id)
    except Exception:
        return None

    doc = users_col.find_one(
        {"user_id": uid},
        {"_id": 0, "pending_close_notification": 1, "pending_close_notification_at": 1},
    ) or {}
    payload = doc.get("pending_close_notification")
    if not isinstance(payload, dict) or not payload:
        return None

    users_col.update_one(
        {"user_id": uid},
        {"$unset": {"pending_close_notification": "", "pending_close_notification_at": ""}},
    )
    return payload

def clear_pending_close_notification(user_id: int) -> bool:
    """
    Elimina una notificación de cierre que ya fue enviada por otra ruta.

    Se usa cuando el ciclo devuelve un evento CLOSE/BOTH y el cierre ya fue
    enviado inmediatamente desde close_data, para evitar que el mismo payload
    quede pendiente y reaparezca en el siguiente ciclo.
    """
    try:
        uid = int(user_id)
    except Exception:
        return False
    try:
        result = users_col.update_one(
            {"user_id": uid},
            {"$unset": {"pending_close_notification": "", "pending_close_notification_at": ""}},
        )
        return bool(getattr(result, "modified_count", 0) or 0)
    except Exception as e:
        try:
            db_log(f"⚠ clear_pending_close_notification error user_id={uid}: {e}")
        except Exception:
            pass
        return False


def get_last_operation(user_id: int) -> dict:
    """
    Retorna last_open y last_close para mostrar en el botón Información.
    Si last_close no está poblado pero sí existe un trade cerrado en trades_col,
    construye un fallback para no dejar la MiniApp sin datos de cierre.
    """
    u = users_col.find_one(
        {"user_id": int(user_id)},
        {"_id": 0, "last_open": 1, "last_close": 1}
    ) or {}

    if not isinstance(u.get("last_close"), dict) or not u.get("last_close"):
        latest_trade = trades_col.find_one(
            {"user_id": int(user_id)},
            {"_id": 0},
            sort=[("timestamp", -1)],
        )
        if latest_trade:
            symbol = latest_trade.get("symbol") or "Operación"
            direction = str(latest_trade.get("direction") or latest_trade.get("side") or "")
            u["last_close"] = {
                "symbol": symbol,
                "side": str(latest_trade.get("side") or "").upper(),
                "direction": direction,
                "entry_price": latest_trade.get("entry_price"),
                "exit_price": latest_trade.get("exit_price"),
                "qty": latest_trade.get("qty"),
                "notional_usdc": latest_trade.get("notional_usdc"),
                "profit": latest_trade.get("profit"),
                "gross_pnl": latest_trade.get("gross_pnl"),
                "fees": latest_trade.get("fees"),
                "pnl_source": latest_trade.get("pnl_source"),
                "realized_fills": latest_trade.get("realized_fills"),
                "close_source": latest_trade.get("close_source"),
                "exit_reason": latest_trade.get("exit_reason"),
            }
    return u

def admin_set_user_trading_status(user_id: int, status: str) -> bool:
    normalized = str(status or '').strip().lower()
    if normalized not in {'active', 'inactive'}:
        return False
    doc = users_col.find_one({"user_id": int(user_id)}, {"_id": 1})
    if not doc:
        return False
    set_trading_status(int(user_id), normalized)
    return True

# ============================================================
# LEGACY – FEES (DESACTIVADO)
# Mantener SOLO para compatibilidad con imports en trading_engine.py
# NO afecta trading / estrategia.
# ============================================================

def add_daily_admin_fee(user_id: int, amount: float):
    """DEPRECATED: fees desactivadas. Se deja para no romper imports."""
    try:
        db_log(f"ℹ add_daily_admin_fee ignorado (fees desactivadas) user={user_id} amount={amount}")
    except Exception:
        pass
    return None

def add_weekly_ref_fee(referrer_id: int, amount: float):
    """DEPRECATED: fees desactivadas. Se deja para no romper imports."""
    try:
        db_log(f"ℹ add_weekly_ref_fee ignorado (fees desactivadas) referrer={referrer_id} amount={amount}")
    except Exception:
        pass
    return None



# ============================================================
# ADMIN – STATS EPOCH (RESET CONTADOR)
# ============================================================

def get_admin_trade_stats_epoch() -> datetime | None:
    """Devuelve el epoch (UTC) desde el cual se deben contar las estadísticas del admin."""
    try:
        doc = settings_col.find_one({"_id": "admin_stats_epoch"}, {"_id": 0, "epoch": 1})
        epoch = (doc or {}).get("epoch")
        if isinstance(epoch, datetime):
            return epoch
        # si por algún motivo quedó como string ISO
        try:
            return datetime.fromisoformat(str(epoch))
        except Exception:
            return None
    except Exception:
        return None


def reset_admin_trade_stats_epoch() -> datetime:
    """Resetea estadísticas: fija epoch=now (UTC). Devuelve el epoch guardado."""
    now = datetime.utcnow()
    try:
        settings_col.update_one(
            {"_id": "admin_stats_epoch"},
            {"$set": {"epoch": now}},
            upsert=True
        )
        db_log(f"♻️ admin_stats_epoch actualizado -> {now.isoformat()}")
    except Exception as e:
        try:
            db_log(f"⚠ reset_admin_trade_stats_epoch error: {e}")
        except Exception:
            pass
    return now


def get_user_trade_stats_epoch(user_id: int):
    """Retorna el epoch (datetime UTC) desde el cual se calculan las stats del usuario."""
    try:
        uid = int(user_id)
    except Exception:
        return None
    try:
        u = users_col.find_one({"user_id": uid}, {"user_trade_stats_epoch": 1})
        if not u:
            return None
        ep = u.get("user_trade_stats_epoch")
        return ep if isinstance(ep, datetime) else None
    except Exception:
        return None


def reset_user_trade_stats_epoch(user_id: int):
    """Resetea el conteo de stats para un usuario (no borra trades, solo mueve el epoch)."""
    try:
        uid = int(user_id)
    except Exception:
        return False
    try:
        now = datetime.utcnow()
        users_col.update_one({"user_id": uid}, {"$set": {"user_trade_stats_epoch": now}}, upsert=True)
        return True
    except Exception as e:
        try:
            db_log(f"⚠ reset_user_trade_stats_epoch error user_id={user_id}: {e}")
        except Exception:
            pass
        return False


# ============================================================
# ADMIN – ESTADÍSTICAS DE TRADING (24h / 7d / 30d)
# ============================================================

def _empty_trade_stats(*, real_since: datetime, epoch: datetime | None, user_id: int | None = None, error: str | None = None) -> dict:
    payload = {
        "total": 0,
        "wins": 0,
        "losses": 0,
        "break_evens": 0,
        "decisive_trades": 0,
        "win_rate": 0.0,
        "win_rate_decisive": 0.0,
        "pnl_total": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "profit_factor": 0.0,
        "since": real_since,
        "epoch": epoch,
    }
    if user_id is not None:
        payload["user_id"] = int(user_id)
    if error:
        payload["error"] = str(error)
    return payload


def get_admin_trade_stats(hours: int) -> dict:
    """
    Retorna estadísticas globales de trades cerrados en un período:
      - total, wins, losses, break_evens
      - win_rate sobre total y win_rate_decisive excluyendo breakevens
      - pnl_total (suma profits netos)
      - gross_profit (suma profits positivos)
      - gross_loss (suma pérdidas en valor absoluto)
      - profit_factor = gross_profit / gross_loss (si gross_loss==0 => 0 o inf)

    hours: ventana hacia atrás en horas (ej: 24, 168, 720)
    """
    try:
        hours = int(hours)
    except Exception:
        hours = 24

    if hours <= 0:
        hours = 24

    since = datetime.utcnow() - timedelta(hours=hours)

    epoch = get_admin_trade_stats_epoch()
    real_since = since
    if isinstance(epoch, datetime) and epoch > since:
        real_since = epoch

    try:
        pipeline = [
            {"$match": {"timestamp": {"$gte": real_since}}},
            {"$group": {
                "_id": None,
                "total": {"$sum": 1},
                "wins": {"$sum": {"$cond": [{"$gt": ["$profit", 0]}, 1, 0]}},
                "losses": {"$sum": {"$cond": [{"$lt": ["$profit", 0]}, 1, 0]}},
                "pnl_total": {"$sum": "$profit"},
                "gross_profit": {"$sum": {"$cond": [{"$gt": ["$profit", 0]}, "$profit", 0]}},
                "gross_loss": {"$sum": {"$cond": [{"$lt": ["$profit", 0]}, {"$abs": "$profit"}, 0]}},
            }}
        ]

        agg = list(trades_col.aggregate(pipeline, allowDiskUse=False))
        if not agg:
            return _empty_trade_stats(real_since=real_since, epoch=epoch)

        r = agg[0]
        total = int(r.get("total", 0) or 0)
        wins = int(r.get("wins", 0) or 0)
        losses = int(r.get("losses", 0) or 0)
        break_evens = max(0, total - wins - losses)
        decisive_trades = wins + losses
        pnl_total = float(r.get("pnl_total", 0.0) or 0.0)
        gross_profit = float(r.get("gross_profit", 0.0) or 0.0)
        gross_loss = float(r.get("gross_loss", 0.0) or 0.0)

        win_rate = (wins / total * 100.0) if total > 0 else 0.0
        win_rate_decisive = (wins / decisive_trades * 100.0) if decisive_trades > 0 else 0.0
        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        else:
            profit_factor = float("inf") if gross_profit > 0 else 0.0

        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "break_evens": break_evens,
            "decisive_trades": decisive_trades,
            "win_rate": round(win_rate, 2),
            "win_rate_decisive": round(win_rate_decisive, 2),
            "pnl_total": round(pnl_total, 6),
            "gross_profit": round(gross_profit, 6),
            "gross_loss": round(gross_loss, 6),
            "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else float("inf"),
            "since": real_since,
            "epoch": epoch,
        }
    except Exception as e:
        try:
            db_log(f"⚠ get_admin_trade_stats error hours={hours}: {e}")
        except Exception:
            pass
        return _empty_trade_stats(real_since=real_since, epoch=epoch, error=str(e))


# ============================================================
# ESTADÍSTICAS POR USUARIO (24h / 7d / 30d)
# ============================================================

def get_user_trade_stats(user_id: int, hours: int) -> dict:
    """
    Retorna estadísticas de trades cerrados para un usuario en un período:
      - total, wins, losses, break_evens
      - win_rate sobre total y win_rate_decisive excluyendo breakevens
      - pnl_total (suma profits netos)
      - gross_profit (suma profits positivos)
      - gross_loss (suma pérdidas en valor absoluto)
      - profit_factor = gross_profit / gross_loss (si gross_loss==0 => 0 o inf)

    user_id: ID de Telegram del usuario
    hours: ventana hacia atrás en horas (ej: 24, 168, 720)
    """
    try:
        hours = int(hours)
    except Exception:
        hours = 24

    if hours <= 0:
        hours = 24

    try:
        uid = int(user_id)
    except Exception:
        uid = 0

    since = datetime.utcnow() - timedelta(hours=hours)

    # Respeta el reset global del admin y el reset específico del usuario
    epoch = get_admin_trade_stats_epoch()
    user_epoch = get_user_trade_stats_epoch(uid)
    real_since = since
    if isinstance(epoch, datetime) and epoch > real_since:
        real_since = epoch
    if isinstance(user_epoch, datetime) and user_epoch > real_since:
        real_since = user_epoch

    try:
        pipeline = [
            {"$match": {"user_id": uid, "timestamp": {"$gte": real_since}}},
            {"$group": {
                "_id": None,
                "total": {"$sum": 1},
                "wins": {"$sum": {"$cond": [{"$gt": ["$profit", 0]}, 1, 0]}},
                "losses": {"$sum": {"$cond": [{"$lt": ["$profit", 0]}, 1, 0]}},
                "pnl_total": {"$sum": "$profit"},
                "gross_profit": {"$sum": {"$cond": [{"$gt": ["$profit", 0]}, "$profit", 0]}},
                "gross_loss": {"$sum": {"$cond": [{"$lt": ["$profit", 0]}, {"$abs": "$profit"}, 0]}},
            }}
        ]

        agg = list(trades_col.aggregate(pipeline, allowDiskUse=False))
        if not agg:
            return _empty_trade_stats(real_since=real_since, epoch=epoch, user_id=uid)

        r = agg[0]
        total = int(r.get("total", 0) or 0)
        wins = int(r.get("wins", 0) or 0)
        losses = int(r.get("losses", 0) or 0)
        break_evens = max(0, total - wins - losses)
        decisive_trades = wins + losses
        pnl_total = float(r.get("pnl_total", 0.0) or 0.0)
        gross_profit = float(r.get("gross_profit", 0.0) or 0.0)
        gross_loss = float(r.get("gross_loss", 0.0) or 0.0)

        win_rate = (wins / total * 100.0) if total > 0 else 0.0
        win_rate_decisive = (wins / decisive_trades * 100.0) if decisive_trades > 0 else 0.0
        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        else:
            profit_factor = float("inf") if gross_profit > 0 else 0.0

        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "break_evens": break_evens,
            "decisive_trades": decisive_trades,
            "win_rate": round(win_rate, 2),
            "win_rate_decisive": round(win_rate_decisive, 2),
            "pnl_total": round(pnl_total, 6),
            "gross_profit": round(gross_profit, 6),
            "gross_loss": round(gross_loss, 6),
            "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else float("inf"),
            "since": real_since,
            "epoch": epoch,
            "user_id": uid,
        }
    except Exception as e:
        try:
            db_log(f"⚠ get_user_trade_stats error user_id={user_id} hours={hours}: {e}")
        except Exception:
            pass
        return _empty_trade_stats(real_since=real_since, epoch=epoch, user_id=uid, error=str(e))



def get_user_track_record_summary(user_id: int, recent_form_limit: int = 12) -> dict:
    """
    Resume el track record completo del usuario respetando epochs de reset.
    Diseñado para MiniApp / panel de rendimiento.
    """
    try:
        uid = int(user_id)
    except Exception:
        uid = 0

    epoch = get_admin_trade_stats_epoch()
    user_epoch = get_user_trade_stats_epoch(uid)
    real_since = datetime.utcnow() - timedelta(days=3650)
    if isinstance(epoch, datetime) and epoch > real_since:
        real_since = epoch
    if isinstance(user_epoch, datetime) and user_epoch > real_since:
        real_since = user_epoch

    payload = {
        'total': 0,
        'wins': 0,
        'losses': 0,
        'break_evens': 0,
        'decisive_trades': 0,
        'net_pnl': 0.0,
        'gross_profit': 0.0,
        'gross_loss': 0.0,
        'avg_pnl': 0.0,
        'avg_win': 0.0,
        'avg_loss': 0.0,
        'expectancy': 0.0,
        'best_trade': 0.0,
        'worst_trade': 0.0,
        'profit_factor': 0.0,
        'win_rate': 0.0,
        'current_streak_type': 'none',
        'current_streak_count': 0,
        'best_win_streak': 0,
        'best_loss_streak': 0,
        'recent_form': [],
        'recent_form_compact': '—',
        'dominant_symbol': None,
        'dominant_symbol_count': 0,
        'dominant_symbol_pnl': 0.0,
        'first_trade_at': None,
        'last_trade_at': None,
        'since': real_since,
        'epoch': epoch,
        'user_id': uid,
    }

    try:
        cursor = trades_col.find(
            {'user_id': uid, 'timestamp': {'$gte': real_since}},
            {'_id': 0, 'symbol': 1, 'profit': 1, 'timestamp': 1},
        ).sort('timestamp', 1)

        prev_streak = None
        current_streak_count = 0
        form = []
        symbol_stats: dict[str, dict] = {}

        for trade in cursor:
            profit = _safe_float((trade or {}).get('profit'), 0.0)
            ts = _parse_dt((trade or {}).get('timestamp'))
            symbol = str((trade or {}).get('symbol') or '—')

            payload['total'] += 1
            payload['net_pnl'] += profit
            payload['best_trade'] = profit if payload['total'] == 1 else max(payload['best_trade'], profit)
            payload['worst_trade'] = profit if payload['total'] == 1 else min(payload['worst_trade'], profit)

            if payload['first_trade_at'] is None:
                payload['first_trade_at'] = ts
            payload['last_trade_at'] = ts

            symbol_row = symbol_stats.setdefault(symbol, {'count': 0, 'net_pnl': 0.0})
            symbol_row['count'] += 1
            symbol_row['net_pnl'] += profit

            if profit > 0:
                payload['wins'] += 1
                payload['gross_profit'] += profit
                result = 'win'
                form.append('W')
            elif profit < 0:
                payload['losses'] += 1
                payload['gross_loss'] += abs(profit)
                result = 'loss'
                form.append('L')
            else:
                payload['break_evens'] += 1
                result = 'flat'
                form.append('F')

            if result in {'win', 'loss'}:
                if prev_streak == result:
                    current_streak_count += 1
                else:
                    current_streak_count = 1
                    prev_streak = result
                if result == 'win':
                    payload['best_win_streak'] = max(payload['best_win_streak'], current_streak_count)
                else:
                    payload['best_loss_streak'] = max(payload['best_loss_streak'], current_streak_count)
            else:
                prev_streak = None
                current_streak_count = 0

        payload['decisive_trades'] = payload['wins'] + payload['losses']
        if payload['total'] > 0:
            payload['avg_pnl'] = payload['net_pnl'] / payload['total']
            payload['expectancy'] = payload['avg_pnl']
            payload['win_rate'] = (payload['wins'] / payload['total']) * 100.0
        if payload['wins'] > 0:
            payload['avg_win'] = payload['gross_profit'] / payload['wins']
        if payload['losses'] > 0:
            payload['avg_loss'] = -(payload['gross_loss'] / payload['losses'])
        if payload['gross_loss'] > 0:
            payload['profit_factor'] = payload['gross_profit'] / payload['gross_loss']
        else:
            payload['profit_factor'] = float('inf') if payload['gross_profit'] > 0 else 0.0

        if prev_streak is not None and current_streak_count > 0:
            payload['current_streak_type'] = prev_streak
            payload['current_streak_count'] = current_streak_count

        if symbol_stats:
            dominant_symbol, dominant_stats = sorted(
                symbol_stats.items(),
                key=lambda item: (item[1]['count'], item[1]['net_pnl']),
                reverse=True,
            )[0]
            payload['dominant_symbol'] = dominant_symbol
            payload['dominant_symbol_count'] = int(dominant_stats['count'])
            payload['dominant_symbol_pnl'] = float(dominant_stats['net_pnl'])

        recent = form[-max(1, int(recent_form_limit)):]
        recent.reverse()
        payload['recent_form'] = recent
        payload['recent_form_compact'] = ' '.join(recent) if recent else '—'

        payload['net_pnl'] = round(payload['net_pnl'], 6)
        payload['gross_profit'] = round(payload['gross_profit'], 6)
        payload['gross_loss'] = round(payload['gross_loss'], 6)
        payload['avg_pnl'] = round(payload['avg_pnl'], 6)
        payload['expectancy'] = round(payload['expectancy'], 6)
        payload['avg_win'] = round(payload['avg_win'], 6)
        payload['avg_loss'] = round(payload['avg_loss'], 6)
        payload['best_trade'] = round(payload['best_trade'], 6)
        payload['worst_trade'] = round(payload['worst_trade'], 6)
        payload['win_rate'] = round(payload['win_rate'], 2)
        payload['dominant_symbol_pnl'] = round(payload['dominant_symbol_pnl'], 6)
        if payload['profit_factor'] != float('inf'):
            payload['profit_factor'] = round(payload['profit_factor'], 4)
        return payload
    except Exception as e:
        try:
            db_log(f"⚠ get_user_track_record_summary error user_id={user_id}: {e}")
        except Exception:
            pass
        payload['error'] = str(e)
        return payload


# ============================================================
# TÉRMINOS Y CONDICIONES
# ============================================================

def accept_terms(user_id: int) -> bool:
    """Marca aceptación de términos y guarda timestamp UTC."""
    try:
        from datetime import datetime
        ts = datetime.utcnow()
        users_col.update_one(
            {"user_id": int(user_id)},
            {"$set": {"terms_accepted": True, "terms_timestamp": ts}},
            upsert=False,
        )
        log_user_activity(int(user_id), 'Términos aceptados', 'La cuenta ya puede habilitar el trading cuando la configuración esté completa.', tone='success', event_type='terms_accepted', occurred_at=ts)
        return True
    except Exception:
        return False


def has_accepted_terms(user_id: int) -> bool:
    """Retorna True si el usuario ya aceptó términos."""
    try:
        u = users_col.find_one({"user_id": int(user_id)}, {"_id": 0, "terms_accepted": 1})
        return bool((u or {}).get("terms_accepted", False))
    except Exception:
        return False


# ============================================================
# HELPERS PÚBLICOS PARA API / MINIAPP
# ============================================================


def _derive_effective_trading_view(*, desired_status: str | None, runtime_state: str | None, runtime_message: str | None, private_key_health: str | None, wallet_configured: bool, private_key_configured: bool, terms_accepted: bool, plan_active: bool, live_trade: bool = False) -> dict:
    desired = str(desired_status or 'inactive').strip().lower() or 'inactive'
    state = str(runtime_state or '').strip().lower() or 'unknown'
    detail = str(runtime_message or '').strip()
    key_health = str(private_key_health or '').strip().lower() or 'not_configured'

    def payload(status: str, label: str, tone: str, message: str) -> dict:
        return {
            'trading_requested_status': desired,
            'trading_effective_status': status,
            'trading_effective_label': label,
            'trading_effective_tone': tone,
            'trading_effective_detail': message,
            'credential_repair_required': bool(key_health == 'invalid'),
        }

    if key_health == 'invalid':
        base = 'La private key almacenada no pudo validarse. Reconfigúrala en la MiniApp antes de operar.'
        if live_trade:
            base = 'La private key almacenada no pudo validarse. Existe una posición activa que no podrá gestionarse correctamente hasta reparar la credencial.'
        return payload('blocked', 'Bloqueado por credencial', 'blocked', detail or base)

    if state == 'configuration_blocked':
        return payload('blocked', 'Bloqueado por configuración', 'blocked', detail or 'Falta completar o reparar la configuración operativa.')
    if state == 'access_blocked':
        return payload('blocked', 'Bloqueado por acceso', 'blocked', detail or 'No existe acceso vigente para abrir nuevas operaciones.')
    if state == 'manager_only':
        return payload('manager_only', 'Gestión activa', 'warning', detail or 'El motor mantiene una posición activa, pero no abrirá nuevas entradas.')
    if state in {'entries_enabled', 'cycle_running', 'cycle_completed'}:
        return payload('active', 'Operativo', 'active', detail or 'La operativa está habilitada.')
    if state == 'paused' or desired != 'active':
        return payload('inactive', 'Pausado', 'inactive', detail or 'El usuario no solicitó nuevas entradas.')

    blockers = []
    if not wallet_configured:
        blockers.append('wallet')
    if not private_key_configured:
        blockers.append('private key')
    if not terms_accepted:
        blockers.append('políticas')
    if not plan_active:
        blockers.append('acceso')

    if blockers:
        return payload('blocked', 'Bloqueado', 'blocked', detail or f"Faltan requisitos para operar: {', '.join(blockers)}.")
    return payload('pending', 'Pendiente de sincronizar', 'info', detail or 'La solicitud está registrada, pero el motor todavía no refleja un estado final.')


def get_user_public_snapshot(user_id: int) -> dict | None:
    """
    Snapshot seguro del usuario para API/MiniApp.
    Nunca expone la private key.
    """
    try:
        uid = int(user_id)
    except Exception:
        return None

    u = users_col.find_one(
        {"user_id": uid},
        {
            "_id": 0,
            "user_id": 1,
            "username": 1,
            "wallet": 1,
            "private_key": 1,
            "private_key_encrypted": 1,
            "private_key_version": 1,
            "private_key_runtime_status": 1,
            "private_key_runtime_error": 1,
            "private_key_runtime_checked_at": 1,
            "private_key_runtime_failure_count": 1,
            "terms_timestamp": 1,
            "trading_status": 1,
            "plan": 1,
            "plan_expires_at": 1,
            "trial_used": 1,
            "terms_accepted": 1,
            "referral_valid_count": 1,
            "last_open_at": 1,
            "last_close_at": 1,
            "runtime_state": 1,
            "runtime_mode": 1,
            "runtime_message": 1,
            "runtime_source": 1,
            "runtime_checked_at": 1,
            "runtime_live_trade": 1,
            "runtime_active_symbol": 1,
        },
    )
    if not u:
        return None

    exp = _parse_dt(u.get("plan_expires_at"))
    private_key_health = ("invalid" if str(u.get("private_key_runtime_status") or "").strip().lower() in {"decrypt_error", "invalid", "unsupported_version"} else ("configured" if bool(u.get("private_key")) else "not_configured"))
    plan_active = _plan_is_active(u)
    effective_view = _derive_effective_trading_view(
        desired_status=u.get("trading_status") or "inactive",
        runtime_state=u.get("runtime_state") or 'unknown',
        runtime_message=u.get("runtime_message"),
        private_key_health=private_key_health,
        wallet_configured=bool(u.get("wallet")),
        private_key_configured=bool(u.get("private_key")),
        terms_accepted=bool(u.get("terms_accepted", False)),
        plan_active=plan_active,
        live_trade=bool(u.get("runtime_live_trade", False)),
    )
    return {
        "user_id": int(u.get("user_id")),
        "username": u.get("username"),
        "wallet": u.get("wallet"),
        "wallet_configured": bool(u.get("wallet")),
        "private_key_configured": bool(u.get("private_key")),
        "private_key_storage": ("encrypted" if bool(u.get("private_key")) and bool(u.get("private_key_encrypted", False)) else ("legacy_plaintext" if bool(u.get("private_key")) else "not_configured")),
        "private_key_health": private_key_health,
        "private_key_runtime_status": u.get("private_key_runtime_status"),
        "private_key_runtime_error": u.get("private_key_runtime_error"),
        "private_key_runtime_checked_at": _parse_dt(u.get("private_key_runtime_checked_at")),
        "private_key_runtime_failure_count": int(u.get("private_key_runtime_failure_count", 0) or 0),
        "trading_status": u.get("trading_status") or "inactive",
        "plan": u.get("plan") or "none",
        "plan_expires_at": exp,
        "plan_active": plan_active,
        "plan_days_remaining": _days_remaining_from_exp(exp) if plan_active else 0,
        "trial_used": bool(u.get("trial_used", False)),
        "terms_accepted": bool(u.get("terms_accepted", False)),
        "terms_timestamp": _parse_dt(u.get("terms_timestamp")),
        "referral_valid_count": int(u.get("referral_valid_count", 0) or 0),
        "last_open_at": _parse_dt(u.get("last_open_at")),
        "last_close_at": _parse_dt(u.get("last_close_at")),
        "runtime_state": u.get("runtime_state") or 'unknown',
        "runtime_mode": u.get("runtime_mode"),
        "runtime_message": u.get("runtime_message"),
        "runtime_source": u.get("runtime_source"),
        "runtime_checked_at": _parse_dt(u.get("runtime_checked_at")),
        "runtime_live_trade": bool(u.get("runtime_live_trade", False)),
        "runtime_active_symbol": u.get("runtime_active_symbol"),
        **effective_view,
    }


def get_user_trades_limited(user_id: int, limit: int = 20):
    try:
        uid = int(user_id)
        lim = max(1, min(int(limit), 100))
    except Exception:
        return []

    return list(
        trades_col.find({"user_id": uid}, {"_id": 0})
        .sort("timestamp", -1)
        .limit(lim)
    )


def get_security_overview() -> dict:
    encrypted_keys = users_col.count_documents({"private_key": {"$ne": None}, "private_key_encrypted": True})
    legacy_plaintext_keys = users_col.count_documents({
        "private_key": {"$ne": None},
        "$or": [
            {"private_key_encrypted": {"$exists": False}},
            {"private_key_encrypted": False},
        ],
    })
    users_with_wallet = users_col.count_documents({"wallet": {"$ne": None}})
    return {
        "encrypted_private_keys": int(encrypted_keys),
        "legacy_plaintext_private_keys": int(legacy_plaintext_keys),
        "wallets_configured": int(users_with_wallet),
    }


def _build_admin_user_projection() -> dict:
    return {
        "_id": 0,
        "user_id": 1,
        "username": 1,
        "wallet": 1,
        "private_key": 1,
        "private_key_encrypted": 1,
        "private_key_version": 1,
        "private_key_updated_at": 1,
        "private_key_runtime_status": 1,
        "private_key_runtime_error": 1,
        "private_key_runtime_checked_at": 1,
        "private_key_runtime_failure_count": 1,
        "trading_status": 1,
        "plan": 1,
        "plan_expires_at": 1,
        "trial_used": 1,
        "terms_accepted": 1,
        "terms_timestamp": 1,
        "referral_valid_count": 1,
        "last_open_at": 1,
        "last_close_at": 1,
    }


def get_admin_user_snapshot(user_id: int) -> dict | None:
    doc = users_col.find_one({"user_id": int(user_id)}, _build_admin_user_projection())
    if not doc:
        return None
    snapshot = get_user_public_snapshot(int(user_id))
    if not snapshot:
        return None
    snapshot.update({
        "terms_timestamp": _parse_dt(doc.get("terms_timestamp")),
        "private_key_version": doc.get("private_key_version"),
        "private_key_updated_at": _parse_dt(doc.get("private_key_updated_at")),
        "recent_admin_actions": get_admin_action_history(limit=10, target_user_id=int(user_id)),
    })
    return snapshot


def search_users_for_admin(query: str, limit: int = 10) -> list[dict]:
    query = (query or '').strip()
    lim = max(1, min(int(limit), 25))
    if not query:
        return []

    clauses = []
    if query.isdigit():
        clauses.append({"user_id": int(query)})

    safe_pattern = re.escape(query)
    clauses.append({"username": {"$regex": safe_pattern, "$options": "i"}})

    cursor = users_col.find({"$or": clauses}, _build_admin_user_projection()).sort("user_id", 1).limit(lim)
    results = []
    for doc in cursor:
        snapshot = get_user_public_snapshot(int(doc.get("user_id")))
        if snapshot:
            results.append(snapshot)
    return results


# ============================================================
# PAGOS AUTOMÁTICOS – PREMIUM 15D / 30D
# ============================================================

def get_payment_order_by_id(order_id: str, *, user_id: int | None = None) -> dict | None:
    query = {'order_id': str(order_id)}
    if user_id is not None:
        query['user_id'] = int(user_id)
    return payment_orders_col.find_one(query)


def get_active_payment_order_for_user(user_id: int) -> dict | None:
    return payment_orders_col.find_one(
        {
            'user_id': int(user_id),
            'status': {'$in': ['awaiting_payment', 'verification_in_progress', 'paid_unconfirmed']},
        },
        sort=[('created_at', -1)],
    )


def cancel_open_payment_orders_for_user(user_id: int, *, reason: str = 'cancelled_by_system') -> int:
    now = _now_utc()
    result = payment_orders_col.update_many(
        {
            'user_id': int(user_id),
            'status': {'$in': ['awaiting_payment', 'verification_in_progress', 'paid_unconfirmed']},
        },
        {'$set': {'status': 'cancelled', 'last_verification_reason': str(reason), 'updated_at': now}},
    )
    return int(result.modified_count or 0)


def log_subscription_event(
    user_id: int,
    event_type: str,
    *,
    plan: str,
    days: int,
    source: str,
    before_plan: str | None = None,
    before_expires_at: datetime | None = None,
    after_plan: str | None = None,
    after_expires_at: datetime | None = None,
    order_id: str | None = None,
    metadata: dict | None = None,
) -> bool:
    try:
        now = _now_utc()
        subscription_events_col.insert_one({
            'user_id': int(user_id),
            'event_type': str(event_type),
            'plan': str(plan),
            'days': int(days),
            'source': str(source),
            'before_plan': before_plan,
            'before_expires_at': before_expires_at,
            'after_plan': after_plan,
            'after_expires_at': after_expires_at,
            'order_id': order_id,
            'metadata': metadata or {},
            'created_at': now,
            'updated_at': now,
        })
        return True
    except Exception as e:
        try:
            db_log(f"⚠ log_subscription_event error user_id={user_id}: {e}")
        except Exception:
            pass
        return False


def apply_payment_premium_purchase(user_id: int, days: int, *, order_id: str, tx_hash: str | None = None, amount_usdt: float | None = None, metadata: dict | None = None) -> dict:
    try:
        uid = int(user_id)
        day_value = int(days)
    except Exception:
        return {'ok': False, 'message': 'Datos de compra inválidos'}

    if day_value not in (15, 30):
        return {'ok': False, 'message': 'Duración premium inválida'}

    u = users_col.find_one({'user_id': uid}, {'plan': 1, 'plan_expires_at': 1, 'trial_used': 1, 'username': 1})
    if not u:
        return {'ok': False, 'message': 'Usuario no encontrado'}

    previous_plan = (u.get('plan') or 'none')
    previous_exp = _parse_dt(u.get('plan_expires_at'))
    exp_utc = _midnight_cuba_after_days_from_base(previous_exp, day_value)
    now = _now_utc()
    set_fields = {
        'plan': 'premium',
        'plan_expires_at': exp_utc,
        'expiry_notified_on': None,
        'last_purchase_at': now,
        'last_purchase_days': day_value,
        'last_purchase_plan': 'premium',
        'last_purchase_source': 'payment_bep20',
        'last_payment_order_id': str(order_id),
        'last_payment_tx_hash': str(tx_hash or '') or None,
        'last_payment_amount_usdt': float(amount_usdt) if amount_usdt is not None else None,
    }
    users_col.update_one({'user_id': uid}, {'$set': set_fields})

    log_subscription_event(
        uid,
        'purchase',
        plan='premium',
        days=day_value,
        source='payment_bep20',
        before_plan=previous_plan,
        before_expires_at=previous_exp,
        after_plan='premium',
        after_expires_at=exp_utc,
        order_id=str(order_id),
        metadata={
            **(metadata or {}),
            'tx_hash': tx_hash,
            'amount_usdt': float(amount_usdt) if amount_usdt is not None else None,
        },
    )
    log_user_activity(
        uid,
        'Pago premium confirmado',
        f'Tu acceso premium fue activado por {day_value} día(s). Nuevo vencimiento {exp_utc.strftime("%Y-%m-%d %H:%M:%S")} UTC.',
        tone='success',
        event_type='payment_confirmed',
        metadata={
            'order_id': str(order_id),
            'days': day_value,
            'amount_usdt': float(amount_usdt) if amount_usdt is not None else None,
            'tx_hash': tx_hash,
        },
        occurred_at=now,
    )

    try:
        referral_reward = apply_referral_reward_for_premium_purchase(
            uid,
            day_value,
            source_order_id=str(order_id),
            tx_hash=tx_hash,
            amount_usdt=float(amount_usdt) if amount_usdt is not None else None,
            metadata=metadata,
        )
    except Exception as referral_exc:
        db_log(f"⚠ apply_payment_premium_purchase referral reward error user={uid} order={order_id}: {referral_exc}")
        referral_reward = {
            'ok': False,
            'applied': False,
            'result': 'reward_exception',
            'error': str(referral_exc),
        }

    return {
        'ok': True,
        'message': f'Premium activado por {day_value} días',
        'plan': 'premium',
        'days': day_value,
        'previous_plan': previous_plan,
        'previous_expires_at': previous_exp,
        'new_expires_at': exp_utc,
        'new_days_remaining': _days_remaining_from_exp(exp_utc),
        'referral_reward': referral_reward,
    }
