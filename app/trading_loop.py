# ============================================================
# TRADING LOOP – Trading X Hyper Pro
# PRODUCCIÓN REAL 24/7 — BANK GRADE (BLINDADO)
# ============================================================

import asyncio
from app.cycle_workers import CycleWorkers
import logging
import os
import random
import time
from datetime import datetime

from telegram.ext import Application
from telegram import error as tg_error

from app.database import (
    get_all_users,
    user_is_ready,
    is_plan_expired,
    should_notify_expired,
    mark_expiry_notified,
    save_last_open,
    publish_runtime_component,
    get_user_cycle_policy,
    touch_user_operational_state,
    get_user_public_snapshot,
    mark_user_private_key_runtime_issue,
    pop_pending_close_notification,
    clear_pending_close_notification,
    record_strategy_router_event,
)
from app.crypto_utils import PrivateKeyDecryptError
from app.trading_engine import execute_trade_cycle
from app.config import SCAN_INTERVAL, ADMIN_TELEGRAM_ID

# ============================================================
# CONFIG BANK GRADE
# ============================================================

MAX_CONCURRENT_USERS = 5          # Control de carga
TRADE_TIMEOUT_SECONDS = max(45, int(os.getenv("TRADE_TIMEOUT_SECONDS", "90")))  # Tiempo de espera del loop; no cancela el worker
ERROR_BACKOFF_SECONDS = 3

# ✅ FIX: evita que al hacer deploy “arranque tirando órdenes” inmediatamente
STARTUP_GRACE_SECONDS = 20        # espera inicial antes de escanear/operar

# ✅ FIX: reparte llamadas (evita picos, evita todos al mismo símbolo al mismo tiempo)
USER_JITTER_MAX_SECONDS = 2.0     # jitter aleatorio por usuario antes de ejecutar su ciclo

# ============================================================
# STATE
# ============================================================

user_locks: dict[int, asyncio.Lock] = {}
_cycle_workers = CycleWorkers()
telegram_blacklist: set[int] = set()

# timestamp de arranque del loop
_loop_started_at = 0.0

# ============================================================
# LOG HARDENING (evita leaks de token en httpx logs)
# ============================================================

def _harden_logging():
    try:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
    except Exception:
        pass

# ============================================================
# LOG
# ============================================================

def log(msg: str, level: str = "INFO"):
    try:
        safe_msg = str(msg).encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    except Exception:
        safe_msg = str(msg)

    print(f"[LOOP {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}] {level} {safe_msg}")

# ============================================================
# MENSAJERÍA SEGURA (BANK GRADE)
# ============================================================

async def send_message_safe(app: Application, user_id: int, text: str):
    if user_id in telegram_blacklist:
        return

    try:
        await app.bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="Markdown"
        )

    except tg_error.Forbidden:
        telegram_blacklist.add(user_id)
        log(f"Usuario {user_id} bloqueó el bot (blacklisted)", "WARN")

    except tg_error.RetryAfter as e:
        await asyncio.sleep(int(e.retry_after) + 1)
        try:
            await app.bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
        except Exception as inner_e:
            log(f"Error Telegram retry usuario {user_id}: {inner_e}", "ERROR")

    except Exception as e:
        log(f"Error Telegram usuario {user_id}: {e}", "ERROR")


_admin_push_last_sent: dict[str, float] = {}

def _admin_user_label(user_id: int) -> str:
    try:
        snap = get_user_public_snapshot(int(user_id)) or {}
        if snap.get('username'):
            return f"@{snap['username']}"
    except Exception:
        pass
    return f"user_id={int(user_id)}"


async def _flush_pending_close_notification(app: Application, user_id: int) -> bool:
    """Envía inmediatamente un cierre pendiente, independientemente de que haya una nueva apertura."""
    try:
        pending_close = await asyncio.to_thread(pop_pending_close_notification, user_id)
    except Exception as e:
        log(f"Error leyendo pending_close_notification user {user_id}: {e}", "ERROR")
        return False
    if not pending_close:
        return False

    msg = pending_close.get("message")
    if msg:
        await send_message_safe(app, user_id, msg)
    try:
        await send_admin_push_safe(app, _compose_admin_close_message(user_id, pending_close))
    except Exception as e:
        log(f"Error push admin pending CLOSE user {user_id}: {e}", "ERROR")
    log(f"Notificación de cierre enviada sin depender de una nueva apertura user={user_id} symbol={pending_close.get('symbol') or '-'}", "INFO")
    return True


async def send_admin_push_safe(app: Application, text: str, *, dedupe_key: str | None = None, cooldown_seconds: int = 0):
    try:
        admin_id = int(ADMIN_TELEGRAM_ID or 0)
    except Exception:
        admin_id = 0
    if admin_id <= 0:
        return

    if dedupe_key and cooldown_seconds > 0:
        now_ts = time.time()
        last_ts = float(_admin_push_last_sent.get(dedupe_key) or 0.0)
        if now_ts - last_ts < float(cooldown_seconds):
            return
        _admin_push_last_sent[dedupe_key] = now_ts

    from app.trade_notifications import message_parts
    for part in message_parts(text):
        try:
            await app.bot.send_message(chat_id=admin_id, text=part)
        except tg_error.RetryAfter as e:
            await asyncio.sleep(int(e.retry_after) + 1)
            try:
                await app.bot.send_message(chat_id=admin_id, text=part)
            except Exception as inner_e:
                log(f"Error admin push retry: {inner_e}", "ERROR")
        except Exception as e:
            log(f"Error admin push: {e}", "ERROR")
    


def _runtime_failure_message(component: str, outcome: dict) -> str:
    identity = (outcome or {}).get('identity') or {}
    bits = [
        '⚠️ Heartbeat runtime con incidencia',
        f"Componente: {component}",
        f"Rol: {identity.get('process_role') or 'unknown'}",
        f"Instancia: {identity.get('runtime_instance') or 'unknown'}",
        f"DB: {identity.get('db_name') or 'unknown'}",
    ]
    mongo_target = identity.get('mongo_target')
    if mongo_target:
        bits.append(f"Mongo: {mongo_target}")
    if outcome.get('primary_error'):
        bits.append(f"Primary: {outcome['primary_error']}")
    if outcome.get('shadow_error'):
        bits.append(f"Shadow: {outcome['shadow_error']}")
    if outcome.get('readback_error'):
        bits.append(f"Readback: {outcome['readback_error']}")
    if outcome.get('verified') is False:
        bits.append('Readback no confirmado')
    return '\n'.join(bits)


async def _publish_runtime_component_safe(
    app: Application,
    component: str,
    state: str = 'online',
    *,
    metadata: dict | None = None,
    verify_readback: bool = False,
    cooldown_seconds: int = 300,
    dedupe_suffix: str | None = None,
) -> dict:
    outcome = await asyncio.to_thread(publish_runtime_component,
        component,
        state,
        metadata=metadata,
        verify_readback=verify_readback,
    )
    if not outcome.get('healthy'):
        log(f"Runtime heartbeat falló component={component} outcome={outcome}", 'ERROR')
        dedupe_key = f"runtime:{component}:{dedupe_suffix or state}:{outcome.get('primary_error')}:{outcome.get('shadow_error')}:{outcome.get('readback_error')}"
        await send_admin_push_safe(
            app,
            _runtime_failure_message(component, outcome),
            dedupe_key=dedupe_key,
            cooldown_seconds=int(cooldown_seconds or 300),
        )
    return outcome


def _compose_admin_open_message(user_id: int, open_data: dict) -> str:
    from app.trade_notifications import format_admin_open
    return format_admin_open(user_id, _admin_user_label(user_id), open_data)


def _compose_admin_close_message(user_id: int, close_data: dict) -> str:
    symbol = (close_data or {}).get('symbol') or (close_data or {}).get('coin') or '—'
    side = str((close_data or {}).get('side') or (close_data or {}).get('direction') or '—').upper()
    profit = (close_data or {}).get('profit')
    exit_reason = (close_data or {}).get('exit_reason') or (close_data or {}).get('close_source') or (close_data or {}).get('message')
    header = '✅ Operación cerrada'
    try:
        pnl = float(profit)
        if pnl < 0:
            header = '🔴 Operación cerrada'
    except Exception:
        pnl = None
    bits = [header, f"Usuario: {_admin_user_label(user_id)}", f"Símbolo: {symbol}", f"Lado: {side}"]
    if profit is not None:
        bits.append(f"PnL: {profit}")
    if exit_reason:
        bits.append(f"Motivo: {str(exit_reason)[:180]}")
    return "\n".join(bits)

# ============================================================
# EJECUCIÓN SEGURA POR USUARIO
# ============================================================

async def execute_user_cycle(app: Application, user_id: int, semaphore: asyncio.Semaphore):
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()

    lock = user_locks[user_id]

    # evita reentradas por usuario
    if lock.locked():
        log(f"Usuario {user_id} ya en ejecución — skip")
        return None

    async with semaphore:
        async with lock:
            # ✅ jitter para repartir carga (por usuario)
            try:
                if USER_JITTER_MAX_SECONDS > 0:
                    await asyncio.sleep(random.uniform(0.0, float(USER_JITTER_MAX_SECONDS)))
            except Exception:
                pass

            try:
                await _publish_runtime_component_safe(
                    app,
                    'scanner',
                    'online',
                    metadata={
                        'user_id': int(user_id),
                        'phase': 'execute_cycle',
                    },
                    verify_readback=False,
                    dedupe_suffix='execute_cycle',
                )
                result = await _cycle_workers.run(user_id, execute_trade_cycle,
                                                  timeout=TRADE_TIMEOUT_SECONDS)

                await _publish_runtime_component_safe(
                    app,
                    'scanner',
                    'online',
                    metadata={
                        'user_id': int(user_id),
                        'phase': 'cycle_finished',
                        'last_event': (result or {}).get('event') if isinstance(result, dict) else None,
                        'symbol': (((result or {}).get('manager') or {}).get('symbol') if isinstance(result, dict) else None)
                            or (((result or {}).get('open') or {}).get('symbol') if isinstance(result, dict) else None),
                    },
                    verify_readback=False,
                    dedupe_suffix='cycle_finished',
                )
                return result

            except asyncio.TimeoutError:
                await asyncio.to_thread(touch_user_operational_state,
                    user_id,
                    'error',
                    f'El ciclo operativo excedió {TRADE_TIMEOUT_SECONDS}s y sigue pendiente; no se iniciará otro worker para esta cuenta.',
                    mode='error',
                    source='trading_loop',
                    metadata={'phase': 'timeout', 'timeout_seconds': TRADE_TIMEOUT_SECONDS},
                )
                try:
                    await asyncio.to_thread(record_strategy_router_event,
                        int(user_id),
                        {
                            'event_type': 'cycle_timeout',
                            'symbol': '',
                            'strategy_id': 'none',
                            'regime_id': 'unknown',
                            'execution_mode': 'router',
                            'signal': False,
                            'selected': False,
                            'trade_opened': False,
                            'regime_changed': False,
                            'shadow_evaluated': False,
                            'shadow_signal': False,
                            'signal_summary': {},
                            'shadow_summary': {},
                            'scanner_summary': {},
                            'regime_summary': {},
                            'extra': {'phase': 'timeout', 'timeout_seconds': int(TRADE_TIMEOUT_SECONDS)},
                        },
                    )
                except Exception:
                    pass
                await _publish_runtime_component_safe(app, 'scanner', 'online', metadata={'user_id': int(user_id), 'phase': 'user_timeout', 'warning_kind': 'timeout', 'error': 'execute_user_cycle timeout'}, verify_readback=False, dedupe_suffix='user_timeout')
                log(f"Timeout ejecución usuario {user_id}", "WARN")
                await send_admin_push_safe(app, f"⚠️ Timeout en ciclo de trading\nUsuario: {_admin_user_label(user_id)}\nEl ciclo excedió {TRADE_TIMEOUT_SECONDS}s.", dedupe_key=f"timeout:{user_id}", cooldown_seconds=300)
                return {'event': 'USER_TIMEOUT', 'user_id': int(user_id)}

            except PrivateKeyDecryptError as e:
                await asyncio.to_thread(mark_user_private_key_runtime_issue,
                    int(user_id),
                    reason=str(e),
                    failure_kind='decrypt_error',
                )
                await asyncio.to_thread(touch_user_operational_state,
                    user_id,
                    'configuration_blocked',
                    'La private key almacenada no pudo validarse. Reconfigúrala en la MiniApp para reactivar la operativa.',
                    mode='blocked',
                    source='credential_guard',
                    metadata={'phase': 'private_key_decrypt_error', 'credential_status': 'decrypt_error', 'credential_error': str(e)[:180]},
                )
                await _publish_runtime_component_safe(app, 'scanner', 'online', metadata={'user_id': int(user_id), 'phase': 'user_credentials_blocked', 'warning_kind': 'private_key_decrypt_error', 'error': str(e)[:300]}, verify_readback=False, dedupe_suffix='user_credentials_blocked')
                log(f"Private key inválida usuario {user_id}: {e}", "ERROR")
                await send_admin_push_safe(app, f"🚨 Credencial operativa inválida\nUsuario: {_admin_user_label(user_id)}\nDetalle: {str(e)[:250]}\nAcción: el usuario quedó bloqueado hasta reconfigurar su private key en la MiniApp.", dedupe_key=f"invalid_private_key:{user_id}", cooldown_seconds=21600)
                return {'event': 'USER_CREDENTIALS_INVALID', 'user_id': int(user_id), 'reason': 'private_key_decrypt_error'}

            except Exception as e:
                await asyncio.to_thread(touch_user_operational_state,
                    user_id,
                    'error',
                    f'El ciclo devolvió una excepción operativa: {str(e)[:180]}',
                    mode='error',
                    source='trading_loop',
                    metadata={'phase': 'exception', 'error': str(e)[:180]},
                )
                await _publish_runtime_component_safe(app, 'scanner', 'online', metadata={'user_id': int(user_id), 'phase': 'user_exception', 'warning_kind': 'user_exception', 'error': str(e)[:300]}, verify_readback=False, dedupe_suffix='user_exception')
                log(f"Error crítico usuario {user_id}: {e}", "ERROR")
                await send_admin_push_safe(app, f"🚨 Error crítico de ejecución\nUsuario: {_admin_user_label(user_id)}\nDetalle: {str(e)[:250]}", dedupe_key=f"cycle_error:{user_id}:{str(e)[:80]}", cooldown_seconds=300)
                return {'event': 'USER_EXCEPTION', 'user_id': int(user_id), 'reason': str(e)[:120]}

# ============================================================
# LOOP PRINCIPAL
# ============================================================

async def trading_loop(app: Application):
    global _loop_started_at

    _harden_logging()
    _loop_started_at = time.time()

    from app.strategies.breakout_reset import (MTF_H1_TRANSITION_ENABLED,
        MTF_H1_TRANSITION_ADX_MIN, MTF_H1_REQUIRE_EMA200, MTF_M15_REQUIRE_EMA200,
        MTF_M15_PULLBACK_ENABLED, MTF_M15_PULLBACK_MAX_ATR)
    from app.config import STRATEGY_ROUTER_LIQUIDITY_PROBE_ENABLED
    log(f"ENTRY_PROFILE release=2026-09-29-m15-pullback "
        f"h1_transition={MTF_H1_TRANSITION_ENABLED} h1_transition_adx={MTF_H1_TRANSITION_ADX_MIN} "
        f"h1_ema200={MTF_H1_REQUIRE_EMA200} m15_ema200={MTF_M15_REQUIRE_EMA200} "
        f"liquidity_probe={STRATEGY_ROUTER_LIQUIDITY_PROBE_ENABLED} "
        f"m15_pullback={MTF_M15_PULLBACK_ENABLED} m15_pullback_max_atr={MTF_M15_PULLBACK_MAX_ATR}")
    log("Trading Loop iniciado — BANK GRADE 24/7")
    log(f"Startup grace: {STARTUP_GRACE_SECONDS}s (no escanea/operará durante este tiempo)")
    await _publish_runtime_component_safe(
        app,
        'trading_loop',
        'online',
        metadata={
            'phase': 'started',
            'scan_interval': SCAN_INTERVAL,
            'startup_grace_seconds': STARTUP_GRACE_SECONDS,
        },
        verify_readback=True,
        dedupe_suffix='started',
    )

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_USERS)

    while True:
        try:
            # ✅ FIX: no arrancar “a operar” inmediatamente tras deploy/restart
            if STARTUP_GRACE_SECONDS > 0:
                elapsed = time.time() - float(_loop_started_at or time.time())
                if elapsed < float(STARTUP_GRACE_SECONDS):
                    await _publish_runtime_component_safe(
                        app,
                        'trading_loop',
                        'online',
                        metadata={
                            'phase': 'startup_grace',
                            'seconds_remaining': max(0, int(float(STARTUP_GRACE_SECONDS) - elapsed)),
                        },
                        verify_readback=False,
                        dedupe_suffix='startup_grace',
                    )
                    await asyncio.sleep(1.0)
                    continue

            users = await asyncio.to_thread(get_all_users,) or []
            await _publish_runtime_component_safe(
                app,
                'trading_loop',
                'online',
                metadata={
                    'phase': 'loop_tick',
                    'users_loaded': len(users),
                    'max_concurrent_users': MAX_CONCURRENT_USERS,
                },
                verify_readback=True,
                dedupe_suffix='loop_tick',
            )
            log(f"Usuarios cargados (antes de filtros): {len(users)}")

            tasks = []
            task_user_ids = []

            for user in users:
                raw_user_id = user.get("user_id")
                if not raw_user_id:
                    continue

                try:
                    user_id = int(raw_user_id)
                except Exception:
                    log(f"user_id inválido: {raw_user_id}", "WARN")
                    continue

                # Un cierre del manager puede haber ocurrido entre ciclos.
                # Entregamos su notificación antes de evaluar una nueva entrada,
                # para que nunca dependa de que aparezca otra operación.
                try:
                    await _flush_pending_close_notification(app, user_id)
                except Exception as e:
                    log(f"Error flush cierre pendiente pre-ciclo user {user_id}: {e}", "ERROR")

                try:
                    policy = await asyncio.to_thread(get_user_cycle_policy, user_id)

                    if await asyncio.to_thread(is_plan_expired, user_id) and await asyncio.to_thread(should_notify_expired, user_id):
                        await send_message_safe(
                            app,
                            user_id,
                            "⛔ Tu plan ha vencido. Las nuevas entradas quedan bloqueadas hasta reactivar el acceso."
                        )
                        await asyncio.to_thread(mark_expiry_notified, user_id)

                    if not policy.get('should_run_cycle', False):
                        await asyncio.to_thread(touch_user_operational_state,
                            user_id,
                            policy.get('runtime_state') or 'idle',
                            policy.get('runtime_message') or 'Sin actividad operativa.',
                            mode=policy.get('runtime_mode'),
                            source='trading_loop',
                            live_trade=bool(policy.get('live_trade')),
                            active_symbol=policy.get('active_symbol'),
                            metadata={'phase': 'skip_policy'},
                        )
                        continue
                except Exception as e:
                    log(f"Error verificando readiness usuario {user_id}: {e}", "ERROR")
                    await asyncio.to_thread(touch_user_operational_state,
                        user_id,
                        'error',
                        f'No se pudo evaluar el estado operativo: {str(e)[:180]}',
                        mode='error',
                        source='trading_loop',
                        metadata={'phase': 'policy_exception'},
                    )
                    continue

                await asyncio.to_thread(touch_user_operational_state,
                    user_id,
                    'cycle_running',
                    'El motor está revisando esta cuenta en tiempo real.',
                    mode='cycle_running',
                    source='trading_loop',
                    live_trade=bool(policy.get('live_trade')),
                    active_symbol=policy.get('active_symbol'),
                    metadata={'phase': 'scheduled'},
                )
                tasks.append(execute_user_cycle(app, user_id, semaphore))
                task_user_ids.append(user_id)

            if not tasks:
                await _publish_runtime_component_safe(
                    app,
                    'scanner',
                    'online',
                    metadata={
                        'phase': 'idle',
                        'users_loaded': len(users),
                        'users_ready': 0,
                    },
                    verify_readback=True,
                    dedupe_suffix='idle',
                )
                await asyncio.sleep(max(1, int(SCAN_INTERVAL or 1)))
                continue

            results = await asyncio.gather(*tasks, return_exceptions=True)
            await _publish_runtime_component_safe(
                app,
                'trading_loop',
                'online',
                metadata={
                    'phase': 'batch_completed',
                    'tasks_executed': len(tasks),
                },
                verify_readback=True,
                dedupe_suffix='batch_completed',
            )

            for user_id, result in zip(task_user_ids, results):
                if isinstance(result, Exception):
                    log(f"Error ciclo usuario {user_id}: {result}", "ERROR")
                    await asyncio.to_thread(touch_user_operational_state,
                        user_id,
                        'error',
                        f'El motor devolvió un error: {str(result)[:180]}',
                        mode='error',
                        source='trading_loop',
                        metadata={'phase': 'result_exception'},
                    )
                    await send_admin_push_safe(app, f"🚨 Error en resultado del loop\nUsuario: {_admin_user_label(user_id)}\nDetalle: {str(result)[:250]}", dedupe_key=f"result_error:{user_id}:{str(result)[:80]}", cooldown_seconds=300)
                    continue

                if not isinstance(result, dict):
                    policy = await asyncio.to_thread(get_user_cycle_policy, user_id)
                    await asyncio.to_thread(touch_user_operational_state,
                        user_id,
                        policy.get('runtime_state') or 'idle',
                        policy.get('runtime_message') or 'Sin cambios operativos en este ciclo.',
                        mode=policy.get('runtime_mode'),
                        source='trading_loop',
                        live_trade=bool(policy.get('live_trade')),
                        active_symbol=policy.get('active_symbol'),
                        metadata={'phase': 'result_empty'},
                    )
                    continue

                event_name = str(result.get('event') or '').upper()

                if event_name in ('OPEN', 'BOTH'):
                    await _publish_runtime_component_safe(
                        app,
                        'trading_loop',
                        'online',
                        metadata={
                            'phase': 'user_cycle_processed',
                            'last_user_id': int(user_id),
                            'last_event': event_name.lower(),
                            'symbol': ((result.get('open') or {}).get('symbol')),
                        },
                        verify_readback=False,
                        dedupe_suffix='user_cycle_open',
                    )
                    await asyncio.to_thread(touch_user_operational_state,
                        user_id,
                        'cycle_completed',
                        'Se abrió una operación y el motor quedó sincronizado con la nueva posición.',
                        mode='entries_enabled',
                        source='trading_loop',
                        live_trade=True,
                        active_symbol=((result.get('open') or {}).get('symbol')),
                        metadata={'phase': 'open_event', 'last_result': event_name.lower(), 'last_symbol': ((result.get('open') or {}).get('symbol')), 'last_cycle_at': datetime.utcnow()},
                    )
                elif event_name in ('CLOSE', 'RECONCILE_CLOSED', 'BOTH'):
                    await _publish_runtime_component_safe(
                        app,
                        'trading_loop',
                        'online',
                        metadata={
                            'phase': 'user_cycle_processed',
                            'last_user_id': int(user_id),
                            'last_event': event_name.lower(),
                            'symbol': ((result.get('close') or {}).get('symbol')),
                        },
                        verify_readback=False,
                        dedupe_suffix='user_cycle_close',
                    )
                    policy = await asyncio.to_thread(get_user_cycle_policy, user_id)
                    await asyncio.to_thread(touch_user_operational_state,
                        user_id,
                        policy.get('runtime_state') or 'idle',
                        'El motor cerró o reconcilió una operación reciente y volvió a su estado operativo actual.',
                        mode=policy.get('runtime_mode'),
                        source='trading_loop',
                        live_trade=bool(policy.get('live_trade')),
                        active_symbol=policy.get('active_symbol'),
                        metadata={'phase': 'close_event', 'event': event_name, 'last_result': event_name.lower(), 'last_symbol': ((result.get('close') or {}).get('symbol')), 'last_cycle_at': datetime.utcnow()},
                    )
                else:
                    policy = await asyncio.to_thread(get_user_cycle_policy, user_id)
                    await _publish_runtime_component_safe(
                        app,
                        'trading_loop',
                        'online',
                        metadata={
                            'phase': 'user_cycle_processed',
                            'last_user_id': int(user_id),
                            'last_event': (event_name or 'none').lower(),
                            'symbol': policy.get('active_symbol'),
                        },
                        verify_readback=False,
                        dedupe_suffix='user_cycle_idle',
                    )
                    await asyncio.to_thread(touch_user_operational_state,
                        user_id,
                        policy.get('runtime_state') or 'idle',
                        policy.get('runtime_message') or 'Ciclo completado sin nuevas aperturas.',
                        mode=policy.get('runtime_mode'),
                        source='trading_loop',
                        live_trade=bool(policy.get('live_trade')),
                        active_symbol=policy.get('active_symbol'),
                        metadata={'phase': 'cycle_complete', 'event': event_name or 'none', 'last_result': (event_name or 'none').lower(), 'last_symbol': policy.get('active_symbol'), 'last_cycle_at': datetime.utcnow()},
                    )

                # ================================
                # GUARDAR INFO OPERACIÓN (OPEN)
                # ================================
                if result.get("event") in ("OPEN", "BOTH"):
                    open_data = result.get("open") or {}
                    try:
                        await asyncio.to_thread(save_last_open, user_id, open_data)
                    except Exception as e:
                        log(f"Error guardando last_open user {user_id}: {e}", "ERROR")

                    msg = open_data.get("message")
                    if msg:
                        await send_message_safe(app, user_id, msg)
                    try:
                        await send_admin_push_safe(app, _compose_admin_open_message(user_id, open_data))
                    except Exception as e:
                        log(f"Error push admin OPEN user {user_id}: {e}", "ERROR")

                # ================================
                # GUARDAR INFO OPERACIÓN (CLOSE)
                # ================================
                if result.get("event") in ("CLOSE", "BOTH"):
                    close_data = result.get("close") or {}
                    msg = close_data.get("message")
                    if msg:
                        await send_message_safe(app, user_id, msg)
                    try:
                        await send_admin_push_safe(app, _compose_admin_close_message(user_id, close_data))
                    except Exception as e:
                        log(f"Error push admin CLOSE user {user_id}: {e}", "ERROR")

                # Cierres disparados por el manager en background no vuelven como event=CLOSE.
                # Si no hubo un CLOSE explícito en este ciclo, vaciamos la cola pendiente.
                # Si sí hubo CLOSE/BOTH, la notificación ya fue enviada arriba y solo
                # limpiamos el payload pendiente para que no reaparezca con la próxima apertura.
                if result.get("event") in ("CLOSE", "BOTH"):
                    try:
                        await asyncio.to_thread(clear_pending_close_notification, user_id)
                    except Exception as e:
                        log(f"Error limpiando pending_close_notification user {user_id}: {e}", "ERROR")
                else:
                    try:
                        await _flush_pending_close_notification(app, user_id)
                    except Exception as e:
                        log(f"Error flush cierre pendiente post-ciclo user {user_id}: {e}", "ERROR")

        except Exception as e:
            log(f"FALLO SISTÉMICO trading_loop: {e}", "CRITICAL")
            await send_admin_push_safe(app, f"🚨 Fallo sistémico del trading loop\nDetalle: {str(e)[:250]}", dedupe_key=f"systemic:{str(e)[:120]}", cooldown_seconds=300)
            await asyncio.sleep(float(ERROR_BACKOFF_SECONDS or 3))

        await asyncio.sleep(max(1, int(SCAN_INTERVAL or 1)))
