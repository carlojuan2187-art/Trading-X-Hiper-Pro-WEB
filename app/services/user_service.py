from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import HTTPException, status

from app.config import ADMIN_WHATSAPP_LINK, BOT_USERNAME
from app.database import (
    accept_terms,
    create_user,
    ensure_access_on_activate,
    get_last_operation,
    get_user_active_trade_snapshot,
    get_referral_valid_count,
    get_system_runtime_snapshot,
    get_user_activity,
    get_user_public_snapshot,
    get_user_trade_stats,
    get_user_track_record_summary,
    get_user_trades_limited,
    get_user_cycle_policy,
    has_accepted_terms,
    save_user_private_key,
    save_user_wallet,
    set_trading_status,
)
from app.hyperliquid_client import get_account_snapshot, get_balance

_ETH_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_PRIVATE_KEY_RE = re.compile(r"^(0x)?[a-fA-F0-9]{64}$")


def _serialize_dt(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None

def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _plan_status_label(profile: dict) -> str:
    if bool(profile.get('plan_active')):
        return 'active'
    if str(profile.get('plan') or '').lower() in {'trial', 'premium'}:
        return 'expired'
    return 'none'


def _access_copy(profile: dict) -> dict:
    days_remaining = _safe_int(profile.get('plan_days_remaining'), 0)
    expires_at = profile.get('plan_expires_at')
    if bool(profile.get('plan_active')):
        if days_remaining > 0:
            detail = f'{days_remaining} día(s) restantes'
        elif expires_at:
            detail = 'Acceso vigente con vencimiento cercano'
        else:
            detail = 'Acceso operativo vigente'
        return {'label': 'Activo', 'detail': detail, 'tone': 'active'}

    if str(profile.get('plan') or '').lower() in {'trial', 'premium'}:
        return {'label': 'Vencido', 'detail': 'El acceso existe, pero ya no está vigente', 'tone': 'blocked'}

    return {'label': 'Sin acceso', 'detail': 'Todavía no hay un plan activo', 'tone': 'blocked'}


def _readiness_score(profile: dict) -> tuple[int, int]:
    checks = [
        bool(profile.get('wallet_configured')),
        bool(profile.get('private_key_configured')) and str(profile.get('private_key_health') or '').lower() != 'invalid',
        bool(profile.get('terms_accepted')),
        bool(profile.get('plan_active')),
        str(profile.get('trading_status') or '').lower() == 'active',
    ]
    total = len(checks)
    completed = sum(1 for item in checks if item)
    return completed, total


def _trade_result_meta(profit: Any) -> dict:
    value = _safe_float(profit, 0.0)
    if value > 0:
        return {'label': 'Win', 'tone': 'success'}
    if value < 0:
        return {'label': 'Loss', 'tone': 'danger'}
    return {'label': 'Flat', 'tone': 'neutral'}


def _metric(label: str, value: Any) -> dict:
    return {'label': str(label), 'value': value if value is not None else '—'}


def _build_operation_snapshot(payload: Any, fallback_title: str) -> dict:
    data = dict(payload or {}) if isinstance(payload, dict) else {}
    symbol = data.get('symbol') or data.get('coin') or data.get('asset') or '—'
    side = data.get('side') or data.get('direction') or data.get('signal') or '—'
    price = data.get('entry_price') or data.get('exit_price') or data.get('price') or data.get('mark_price')
    quantity = data.get('qty') or data.get('size') or data.get('amount')
    reason = data.get('reason') or data.get('status') or data.get('result') or data.get('close_reason')
    summary_parts = []
    if price is not None:
        summary_parts.append(f'Precio {price}')
    if quantity is not None:
        summary_parts.append(f'Qty {quantity}')
    if reason:
        summary_parts.append(str(reason))
    return {
        'title': f"{symbol} · {str(side).upper() if side != '—' else fallback_title}",
        'detail': ' · '.join(summary_parts) if summary_parts else 'Sin detalle adicional disponible.',
    }


def _friendly_blockers(blockers: list[str] | None) -> list[str]:
    labels = {
        'wallet_missing': 'Falta configurar la wallet.',
        'private_key_missing': 'Falta configurar la private key.',
        'private_key_invalid': 'La private key guardada requiere reparación.',
        'terms_missing': 'Debes aceptar los términos operativos.',
    }
    return [labels.get(str(item), str(item)) for item in (blockers or [])]


def _runtime_operational_copy(profile: dict, policy: dict | None = None) -> dict:
    policy = dict(policy or {})
    state = str(profile.get('runtime_state') or policy.get('runtime_state') or 'unknown').strip().lower() or 'unknown'
    mode = str(profile.get('runtime_mode') or policy.get('runtime_mode') or '').strip().lower() or 'unknown'
    live_trade = bool(profile.get('runtime_live_trade')) or bool(policy.get('live_trade'))
    active_symbol = profile.get('runtime_active_symbol') or policy.get('active_symbol')
    detail = profile.get('runtime_message') or policy.get('runtime_message') or 'Sin lectura operativa todavía.'

    mapping = {
        'activation_requested': ('Pendiente de sincronizar', 'info'),
        'entries_enabled': ('Operativo', 'active'),
        'manager_only': ('Gestión activa', 'warning'),
        'paused': ('Pausado', 'inactive'),
        'access_blocked': ('Bloqueado por acceso', 'blocked'),
        'configuration_blocked': ('Bloqueado por configuración', 'blocked'),
        'idle': ('Sin actividad', 'neutral'),
        'cycle_running': ('Motor trabajando', 'active'),
        'cycle_completed': ('Sincronizado', 'active'),
        'error': ('Incidencia operativa', 'danger'),
    }
    label, tone = mapping.get(state, ('Sin lectura', 'neutral'))

    if mode == 'manager_only' and live_trade and active_symbol:
        detail = f'El motor mantiene {active_symbol} en modo gestión y no abrirá nuevas entradas.'
    elif state == 'entries_enabled' and active_symbol:
        detail = f'El motor puede operar normalmente. Símbolo activo reciente: {active_symbol}.'

    desired = str(profile.get('trading_status') or '').lower()
    aligned = True
    if desired == 'active' and state in {'paused', 'access_blocked', 'configuration_blocked'}:
        aligned = False
    if desired != 'active' and state == 'entries_enabled':
        aligned = False

    alignment_label = 'Sincronizado' if aligned else 'Revisar sincronización'
    return {
        'state': state,
        'mode': mode,
        'label': label,
        'tone': tone,
        'detail': detail,
        'live_trade': live_trade,
        'active_symbol': active_symbol,
        'aligned': aligned,
        'alignment_label': alignment_label,
    }


def _build_exchange_readiness(profile: dict) -> dict:
    snapshot = get_account_snapshot(int(profile.get("user_id") or 0))

    state = str(snapshot.get("status") or "blocked")
    label = str(snapshot.get("label") or "Sin lectura")
    tone = str(snapshot.get("tone") or "neutral")
    message = str(snapshot.get("message") or "Sin lectura del exchange todavía.")

    if not profile.get('wallet_configured'):
        state = 'wallet_missing'
        label = 'Falta wallet'
        tone = 'blocked'
        message = 'Configura la wallet para poder consultar la cuenta del exchange.'
    elif not profile.get('private_key_configured'):
        state = 'private_key_missing'
        label = 'Falta private key'
        tone = 'blocked'
        message = 'Configura la private key para habilitar la operativa.'
    elif not profile.get('terms_accepted'):
        state = 'terms_missing'
        label = 'Pendiente de políticas'
        tone = 'warning'
        message = 'Confirma la aceptación de políticas para habilitar la operativa.'
    elif not profile.get('plan_active'):
        state = 'plan_inactive'
        label = 'Sin acceso vigente'
        tone = 'blocked'
        message = 'Necesitas un plan activo para operar.'

    readiness = {
        'state': state,
        'label': label,
        'tone': tone,
        'message': message,
        'available_balance': _safe_float(snapshot.get('available_balance'), 0.0),
        'account_value': _safe_float(snapshot.get('account_value'), 0.0),
        'capital_threshold': _safe_float(snapshot.get('capital_threshold'), 0.0),
        'capital_sufficient': bool(snapshot.get('capital_sufficient')),
        'positions_count': _safe_int(snapshot.get('positions_count'), 0),
        'has_open_position': bool(snapshot.get('has_open_position')),
        'active_symbols': list(snapshot.get('active_symbols') or []),
        'exchange_reachable': bool(snapshot.get('exchange_reachable')),
    }

    if readiness['has_open_position'] and state not in {'wallet_missing', 'private_key_missing', 'terms_missing', 'plan_inactive'}:
        readiness['message'] = snapshot.get('message') or 'Hay una posición activa bajo gestión.'

    if state == 'ready' and str(profile.get('trading_status') or '').lower() != 'active':
        readiness['label'] = 'Listo al activar'
        readiness['tone'] = 'info'
        readiness['message'] = 'La cuenta está lista. Activa el trading para permitir nuevas entradas.'

    return readiness


def _clean_wallet(wallet: str) -> str:
    value = (wallet or '').strip()
    if not value:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='La wallet no puede estar vacía')
    if not _ETH_ADDRESS_RE.fullmatch(value):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Wallet inválida. Debe ser una dirección 0x de 42 caracteres')
    return value


def _clean_private_key(private_key: str) -> str:
    value = (private_key or '').strip()
    if not value:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='La private key no puede estar vacía')
    if not _PRIVATE_KEY_RE.fullmatch(value):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Private key inválida. Debe ser hexadecimal de 64 caracteres')
    return value if value.startswith('0x') else f'0x{value}'


def _snapshot_or_raise(user_id: int) -> dict:
    profile = get_user_public_snapshot(int(user_id))
    if not profile:
        raise LookupError('Usuario no encontrado')
    return profile


def ensure_user_exists(user_id: int, username: str | None = None) -> dict:
    safe_username = (username or '').strip() or f'user_{int(user_id)}'
    create_user(int(user_id), safe_username)
    profile = get_user_public_snapshot(int(user_id))
    if not profile:
        raise RuntimeError(f'No se pudo cargar el perfil del usuario {user_id}')
    return profile


def get_user_profile(user_id: int) -> dict:
    profile = _snapshot_or_raise(int(user_id))

    return {
        'user_id': profile['user_id'],
        'username': profile.get('username'),
        'wallet': profile.get('wallet'),
        'wallet_configured': bool(profile.get('wallet_configured')),
        'private_key_configured': bool(profile.get('private_key_configured')),
        'trading_status': profile.get('trading_status', 'inactive'),
        'trading_requested_status': profile.get('trading_requested_status', profile.get('trading_status', 'inactive')),
        'trading_effective_status': profile.get('trading_effective_status', profile.get('trading_status', 'inactive')),
        'trading_effective_label': profile.get('trading_effective_label'),
        'trading_effective_tone': profile.get('trading_effective_tone'),
        'trading_effective_detail': profile.get('trading_effective_detail'),
        'credential_repair_required': bool(profile.get('credential_repair_required')),
        'plan': profile.get('plan', 'none'),
        'plan_active': bool(profile.get('plan_active')),
        'plan_days_remaining': _safe_int(profile.get('plan_days_remaining'), 0),
        'plan_expires_at': _serialize_dt(profile.get('plan_expires_at')),
        'access_state': _plan_status_label(profile),
        'trial_used': bool(profile.get('trial_used')),
        'terms_accepted': bool(profile.get('terms_accepted')),
        'terms_timestamp': _serialize_dt(profile.get('terms_timestamp')),
        'private_key_storage': profile.get('private_key_storage', 'not_configured'),
        'private_key_health': profile.get('private_key_health', 'not_configured'),
        'private_key_runtime_status': profile.get('private_key_runtime_status'),
        'private_key_runtime_error': profile.get('private_key_runtime_error'),
        'private_key_runtime_checked_at': _serialize_dt(profile.get('private_key_runtime_checked_at')),
        'private_key_runtime_failure_count': _safe_int(profile.get('private_key_runtime_failure_count'), 0),
        'referral_valid_count': int(profile.get('referral_valid_count', 0) or 0),
        'last_open_at': _serialize_dt(profile.get('last_open_at')),
        'last_close_at': _serialize_dt(profile.get('last_close_at')),
        'runtime_state': profile.get('runtime_state') or 'unknown',
        'runtime_mode': profile.get('runtime_mode'),
        'runtime_message': profile.get('runtime_message'),
        'runtime_source': profile.get('runtime_source'),
        'runtime_checked_at': _serialize_dt(profile.get('runtime_checked_at')),
        'runtime_live_trade': bool(profile.get('runtime_live_trade')),
        'runtime_active_symbol': profile.get('runtime_active_symbol'),
    }


def get_dashboard(user_id: int, include_balance: bool = False) -> dict:
    profile = get_user_profile(int(user_id))
    policy = get_user_cycle_policy(int(user_id))
    runtime_readout = _runtime_operational_copy(profile, policy)
    exchange = _build_exchange_readiness(profile)
    ready = (
        profile['wallet_configured']
        and profile['private_key_configured']
        and profile['trading_status'] == 'active'
        and profile['plan_active']
        and profile['terms_accepted']
        and exchange.get('capital_sufficient', False)
    )
    completed, total = _readiness_score(profile)
    access = _access_copy(profile)
    payload = {
        **profile,
        'status_summary': 'ready' if ready else 'not_ready',
        'readiness_completed': completed,
        'readiness_total': total,
        'access_label': access['label'],
        'access_detail': access['detail'],
        'access_tone': access['tone'],
        'operational_state': runtime_readout['state'],
        'operational_mode': runtime_readout['mode'],
        'operational_label': runtime_readout['label'],
        'operational_tone': runtime_readout['tone'],
        'operational_detail': runtime_readout['detail'],
        'operational_aligned': runtime_readout['aligned'],
        'exchange_snapshot': exchange,
        'exchange_label': exchange.get('label'),
        'exchange_tone': exchange.get('tone'),
        'exchange_message': exchange.get('message'),
    }
    if include_balance:
        payload['exchange_balance'] = float(exchange.get('available_balance') or get_balance(int(user_id)) or 0.0)
    return payload


def get_control_summary(user_id: int) -> dict:
    profile = get_user_profile(int(user_id))
    policy = get_user_cycle_policy(int(user_id))
    runtime_readout = _runtime_operational_copy(profile, policy)
    exchange = _build_exchange_readiness(profile)
    terms_accepted = bool(profile['terms_accepted'])
    completed, total = _readiness_score(profile)
    return {
        **profile,
        'wallet_masked': profile['wallet'],
        'private_key_masked': '******** configurada' if profile['private_key_configured'] else 'No configurada',
        'security_posture': ('encrypted_at_rest' if profile.get('private_key_storage') == 'encrypted' else ('legacy_plaintext' if profile.get('private_key_storage') == 'legacy_plaintext' else 'not_configured')),
        'activation_ready': bool(
            profile['wallet_configured']
            and profile['private_key_configured']
            and str(profile.get('private_key_health') or '').lower() != 'invalid'
            and terms_accepted
        ),
        'activation_blockers': [
            blocker
            for blocker, is_missing in (
                ('wallet_missing', not profile['wallet_configured']),
                ('private_key_missing', not profile['private_key_configured']),
                ('private_key_invalid', str(profile.get('private_key_health') or '').lower() == 'invalid'),
                ('terms_missing', not terms_accepted),
            )
            if is_missing
        ],
        'activation_blockers_copy': _friendly_blockers([
            blocker
            for blocker, is_missing in (
                ('wallet_missing', not profile['wallet_configured']),
                ('private_key_missing', not profile['private_key_configured']),
                ('private_key_invalid', str(profile.get('private_key_health') or '').lower() == 'invalid'),
                ('terms_missing', not terms_accepted),
            )
            if is_missing
        ]),
        'support_contact': ADMIN_WHATSAPP_LINK or None,
        'readiness_completed': completed,
        'readiness_total': total,
        'operational_state': runtime_readout['state'],
        'operational_mode': runtime_readout['mode'],
        'operational_label': runtime_readout['label'],
        'operational_tone': runtime_readout['tone'],
        'operational_detail': runtime_readout['detail'],
        'operational_live_trade': runtime_readout['live_trade'],
        'operational_active_symbol': runtime_readout['active_symbol'],
        'operational_alignment_ok': runtime_readout['aligned'],
        'operational_alignment_label': runtime_readout['alignment_label'],
        'exchange_snapshot': exchange,
        'exchange_label': exchange.get('label'),
        'exchange_tone': exchange.get('tone'),
        'exchange_message': exchange.get('message'),
    }


def update_user_configuration(
    user_id: int,
    *,
    wallet: str | None = None,
    private_key: str | None = None,
) -> dict:
    if wallet is None and private_key is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='No se recibió ningún cambio para guardar')

    if wallet is not None:
        save_user_wallet(int(user_id), _clean_wallet(wallet))

    if private_key is not None:
        save_user_private_key(int(user_id), _clean_private_key(private_key))

    return get_control_summary(int(user_id))


def accept_user_terms(user_id: int) -> dict:
    if not accept_terms(int(user_id)):
        raise RuntimeError('No se pudieron aceptar los términos en este momento')
    return get_control_summary(int(user_id))


def activate_user_trading(user_id: int) -> dict:
    summary = get_control_summary(int(user_id))
    if not has_accepted_terms(int(user_id)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Debes aceptar los términos antes de activar el trading')

    access = ensure_access_on_activate(int(user_id))
    if not access.get('allowed', False):
        detail = access.get('message') or 'Tu acceso está bloqueado'
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)

    set_trading_status(int(user_id), 'active')
    summary = get_control_summary(int(user_id))
    detail = summary.get('operational_detail') or 'La activación quedó registrada.'
    exchange_message = ((summary.get('exchange_snapshot') or {}).get('message')) or summary.get('exchange_message')
    message_parts = [access.get('plan_message') or 'Trading activado', detail]
    if exchange_message:
        message_parts.append(exchange_message)
    return {
        'result': 'activated',
        'message': '\n'.join(part for part in message_parts if part),
        'control': summary,
    }


def pause_user_trading(user_id: int) -> dict:
    set_trading_status(int(user_id), 'inactive')
    control = get_control_summary(int(user_id))
    return {
        'result': 'paused',
        'message': control.get('operational_detail') or 'Trading pausado',
        'control': control,
    }




def _normalize_trade_row(trade: dict) -> dict:
    normalized = dict(trade or {})
    ts = normalized.get('timestamp')
    normalized['timestamp'] = _serialize_dt(ts)
    profit_value = _safe_float(normalized.get('profit'), 0.0)
    gross_pnl = _safe_float(normalized.get('gross_pnl'), profit_value)
    fees = _safe_float(normalized.get('fees'), 0.0)
    meta = _trade_result_meta(profit_value)
    normalized['profit'] = profit_value
    normalized['gross_pnl'] = gross_pnl
    normalized['fees'] = fees
    normalized['notional_usdc'] = _safe_float(normalized.get('notional_usdc'), 0.0) if normalized.get('notional_usdc') is not None else None
    normalized['result_label'] = meta['label']
    normalized['result_tone'] = {'success': 'success', 'danger': 'danger', 'neutral': 'neutral'}.get(meta['tone'], 'neutral')
    return normalized


def _build_operation_summary(trades: list[dict]) -> dict:
    if not trades:
        return {
            'wins': 0,
            'losses': 0,
            'net_visible': 0.0,
            'best_trade_pnl': 0.0,
            'worst_trade_pnl': 0.0,
        }

    profits = [_safe_float(item.get('profit'), 0.0) for item in trades]
    wins = sum(1 for value in profits if value > 0)
    losses = sum(1 for value in profits if value < 0)
    net_visible = round(sum(profits), 4)
    return {
        'wins': wins,
        'losses': losses,
        'net_visible': net_visible,
        'best_trade_pnl': round(max(profits), 4),
        'worst_trade_pnl': round(min(profits), 4),
    }


def _build_last_operation_summary(payload: Any, fallback_title: str, empty_detail: str) -> dict | None:
    if not isinstance(payload, dict) or not payload:
        return None
    title = payload.get('symbol') or payload.get('coin') or fallback_title
    side = str(payload.get('side') or payload.get('direction') or '').upper()
    detail_parts = []
    metrics = []
    entry = payload.get('entry_price')
    exit_price = payload.get('exit_price')
    qty = payload.get('qty')
    notional = payload.get('notional_usdc')
    net = payload.get('profit')
    gross = payload.get('gross_pnl')
    fees = payload.get('fees')
    source = payload.get('pnl_source')
    reason = payload.get('exit_reason') or payload.get('close_source')
    if entry is not None:
        detail_parts.append(f"Entrada {entry}")
        metrics.append(_metric('Entrada', entry))
    if exit_price is not None:
        detail_parts.append(f"Salida {exit_price}")
        metrics.append(_metric('Salida', exit_price))
    if qty is not None:
        detail_parts.append(f"Qty {qty}")
        metrics.append(_metric('Qty', qty))
    if notional is not None:
        detail_parts.append(f"Valor {round(_safe_float(notional, 0.0), 4)} USDC")
        metrics.append(_metric('Valor', f"{round(_safe_float(notional, 0.0), 4)} USDC"))
    if gross is not None:
        detail_parts.append(f"Bruto {round(_safe_float(gross, 0.0), 4)}")
        metrics.append(_metric('Bruto', f"{round(_safe_float(gross, 0.0), 4)} USDC"))
    if fees is not None:
        detail_parts.append(f"Fees {round(_safe_float(fees, 0.0), 4)}")
        metrics.append(_metric('Fees', f"{round(_safe_float(fees, 0.0), 4)} USDC"))
    if net is not None:
        detail_parts.append(f"Neto {round(_safe_float(net, 0.0), 4)}")
        metrics.append(_metric('PnL neto', f"{round(_safe_float(net, 0.0), 4)} USDC"))
    if source:
        detail_parts.append(f"Fuente {source}")
        metrics.append(_metric('Fuente', source))
    if reason:
        detail_parts.append(f"Motivo {reason}")
    if payload.get('message'):
        raw_message = str(payload.get('message'))
        normalized_message = raw_message.replace('\\n', '\n').strip()
        if normalized_message:
            detail_parts.append(normalized_message)
    return {
        'title': f"{title} · {side}" if side else str(title),
        'detail': ' · '.join(detail_parts) if detail_parts else empty_detail,
        'metrics': metrics[:8],
    }


def _activity_family(event_type: str | None) -> str:
    normalized = str(event_type or '').strip().lower()
    if normalized in {'trade_opened', 'trade_closed'}:
        return 'trading'
    if normalized in {'wallet_updated', 'private_key_updated', 'terms_accepted', 'access_updated', 'private_key_hardened'}:
        return 'account'
    if normalized in {'trading_activated', 'trading_paused', 'stats_reset'}:
        return 'control'
    return 'info'


def _activity_badge(event_type: str | None, tone: str | None) -> str:
    normalized = str(event_type or '').strip().lower()
    if normalized == 'trade_opened':
        return 'OPEN'
    if normalized == 'trade_closed':
        return 'CLOSE'
    if normalized == 'trading_activated':
        return 'ON'
    if normalized == 'trading_paused':
        return 'PAUSE'
    if normalized == 'wallet_updated':
        return 'WALLET'
    if normalized == 'private_key_updated':
        return 'KEY'
    if normalized == 'terms_accepted':
        return 'TERMS'
    if normalized == 'access_updated':
        return 'PLAN'
    if normalized == 'private_key_hardened':
        return 'HARDEN'
    if normalized == 'stats_reset':
        return 'RESET'
    tone_normalized = str(tone or '').lower()
    if tone_normalized == 'success':
        return 'OK'
    if tone_normalized == 'danger':
        return 'ALERT'
    return 'INFO'


def _serialize_activity_rows(rows: list[dict]) -> list[dict]:
    serialized = []
    for row in rows or []:
        item = dict(row or {})
        event_type = item.get('event_type') or 'info'
        tone = item.get('tone') or 'info'
        item['at'] = _serialize_dt(item.get('created_at'))
        serialized.append({
            'title': item.get('title') or 'Actividad',
            'detail': item.get('detail') or 'Sin detalle adicional.',
            'tone': tone,
            'event_type': event_type,
            'family': _activity_family(event_type),
            'badge': _activity_badge(event_type, tone),
            'at': item.get('at'),
        })
    return serialized


def _build_active_trade_summary(payload: Any) -> dict | None:
    if not isinstance(payload, dict) or not payload:
        return None

    symbol = payload.get('symbol') or payload.get('coin') or 'Operación activa'
    side = str(payload.get('side') or payload.get('direction') or '').upper()
    entry = payload.get('entry_price')
    last_price = payload.get('last_price')
    pnl = payload.get('last_pnl_pct')
    qty = payload.get('qty_coin_for_log') or payload.get('qty') or payload.get('size')
    notional = payload.get('qty_usdc_for_profit') or payload.get('notional_usdc')
    mode = payload.get('mode')
    detail_parts = []
    metrics = []
    if entry is not None:
        detail_parts.append(f'Entrada {entry}')
        metrics.append(_metric('Entrada', entry))
    if last_price is not None:
        detail_parts.append(f'Último precio {last_price}')
        metrics.append(_metric('Último', last_price))
    if qty is not None:
        detail_parts.append(f'Qty {qty}')
        metrics.append(_metric('Qty', qty))
    if notional is not None:
        detail_parts.append(f'Valor {round(_safe_float(notional, 0.0), 4)} USDC')
        metrics.append(_metric('Valor', f"{round(_safe_float(notional, 0.0), 4)} USDC"))
    if pnl is not None:
        detail_parts.append(f'PnL vivo {round(_safe_float(pnl, 0.0), 4)}%')
        metrics.append(_metric('PnL vivo', f"{round(_safe_float(pnl, 0.0), 4)}%"))
    if mode:
        metrics.append(_metric('Modo', mode))
    return {
        'title': f"{symbol} · {side}" if side else str(symbol),
        'detail': ' · '.join(detail_parts) if detail_parts else 'Hay una operación activa registrada en este momento.',
        'started_at': payload.get('started_at') or payload.get('persisted_at'),
        'symbol': symbol,
        'side': side,
        'metrics': metrics[:6],
    }


def _build_timeline_summary(activity: list[dict], trades: list[dict], active_trade: dict | None) -> dict:
    trading_events = sum(1 for item in activity if item.get('family') == 'trading')
    account_events = sum(1 for item in activity if item.get('family') == 'account')
    control_events = sum(1 for item in activity if item.get('family') == 'control')
    total_visible = len(activity)
    return {
        'total_visible_events': total_visible,
        'trading_events': trading_events,
        'account_events': account_events,
        'control_events': control_events,
        'recent_trades_visible': len(trades),
        'live_trade': bool(active_trade),
    }


def _visible_streak(trades: list[dict]) -> dict:
    streak_type = None
    streak_count = 0
    for trade in trades:
        profit = _safe_float(trade.get('profit'), 0.0)
        if profit > 0:
            current = 'win'
        elif profit < 0:
            current = 'loss'
        else:
            break
        if streak_type is None:
            streak_type = current
            streak_count = 1
        elif streak_type == current:
            streak_count += 1
        else:
            break
    return {'type': streak_type or 'none', 'count': streak_count}


def _visible_dominant_symbol(trades: list[dict]) -> dict:
    symbol_map: dict[str, dict] = {}
    for trade in trades:
        symbol = str(trade.get('symbol') or '—')
        row = symbol_map.setdefault(symbol, {'count': 0, 'pnl': 0.0})
        row['count'] += 1
        row['pnl'] += _safe_float(trade.get('profit'), 0.0)
    if not symbol_map:
        return {'symbol': None, 'count': 0, 'pnl': 0.0}
    symbol, meta = sorted(symbol_map.items(), key=lambda item: (item[1]['count'], item[1]['pnl']), reverse=True)[0]
    return {'symbol': symbol, 'count': int(meta['count']), 'pnl': round(_safe_float(meta['pnl']), 4)}


def _recent_form_compact(trades: list[dict], limit: int = 8) -> str:
    if not trades:
        return '—'
    symbols = []
    for trade in trades[:max(1, int(limit))]:
        profit = _safe_float(trade.get('profit'), 0.0)
        if profit > 0:
            symbols.append('W')
        elif profit < 0:
            symbols.append('L')
        else:
            symbols.append('F')
    return ' '.join(symbols) if symbols else '—'


def _augment_visible_summary(summary: dict, trades: list[dict]) -> dict:
    payload = dict(summary or {})
    count = len(trades or [])
    payload['avg_trade_visible'] = round((_safe_float(payload.get('net_visible'), 0.0) / count), 4) if count > 0 else 0.0
    streak = _visible_streak(trades or [])
    payload['current_streak_type'] = streak['type']
    payload['current_streak_count'] = streak['count']
    dominant = _visible_dominant_symbol(trades or [])
    payload['dominant_symbol'] = dominant['symbol']
    payload['dominant_symbol_count'] = dominant['count']
    payload['dominant_symbol_pnl'] = dominant['pnl']
    payload['recent_form_visible'] = _recent_form_compact(trades or [])
    return payload


def get_recent_operations(user_id: int, limit: int = 20) -> dict:
    last_operation = get_last_operation(int(user_id)) or {}
    active_trade = get_user_active_trade_snapshot(int(user_id)) or {}
    trades = get_user_trades_limited(int(user_id), limit=limit)
    normalized_trades = [_normalize_trade_row(trade) for trade in trades]
    activity = _serialize_activity_rows(get_user_activity(int(user_id), limit=max(min(limit, 20), 12)))
    active_trade_summary = _build_active_trade_summary(active_trade)
    visible_summary = _augment_visible_summary(_build_operation_summary(normalized_trades), normalized_trades)

    return {
        'last_open': last_operation.get('last_open'),
        'last_close': last_operation.get('last_close'),
        'active_trade': active_trade if active_trade_summary else None,
        'active_trade_summary': active_trade_summary,
        'last_open_summary': _build_last_operation_summary(last_operation.get('last_open'), 'Última apertura', 'Sin aperturas registradas todavía.'),
        'last_close_summary': _build_last_operation_summary(last_operation.get('last_close'), 'Último cierre', 'Sin cierres registrados todavía.'),
        'activity': activity,
        'timeline_summary': _build_timeline_summary(activity, normalized_trades, active_trade_summary),
        'summary': visible_summary,
        'trades': normalized_trades,
        'count': len(normalized_trades),
    }


def _track_record_streak_label(track_record: dict) -> str:
    streak_type = str(track_record.get('current_streak_type') or 'none')
    streak_count = _safe_int(track_record.get('current_streak_count'), 0)
    if streak_type == 'win' and streak_count > 0:
        return f'Racha win x{streak_count}'
    if streak_type == 'loss' and streak_count > 0:
        return f'Racha loss x{streak_count}'
    return 'Sin racha decisiva'


def get_performance_summary(user_id: int) -> dict:
    stats_24h = _normalize_trade_stats(get_user_trade_stats(int(user_id), 24) or {})
    stats_7d = _normalize_trade_stats(get_user_trade_stats(int(user_id), 24 * 7) or {})
    stats_30d = _normalize_trade_stats(get_user_trade_stats(int(user_id), 24 * 30) or {})
    track_record = get_user_track_record_summary(int(user_id)) or {}

    windows = {'24h': stats_24h, '7d': stats_7d, '30d': stats_30d}
    candidates = [(label, payload) for label, payload in windows.items() if int(payload.get('total', 0) or 0) > 0]
    best_window = max(candidates, key=lambda item: _safe_float(item[1].get('pnl_total'), 0.0), default=None)

    edge_tone = 'warning'
    edge_label = 'Sin suficiente muestra'
    edge_detail = 'Todavía no hay suficientes cierres para leer una ventaja estable.'
    if stats_7d.get('total', 0) >= 3:
        pf = _safe_float(stats_7d.get('profit_factor'), 0.0)
        wr = _safe_float(stats_7d.get('win_rate'), 0.0)
        if pf >= 1.3 and wr >= 55:
            edge_tone = 'success'
            edge_label = 'Ventaja favorable'
            edge_detail = 'La ventana de 7d mantiene profit factor y win rate aceptables.'
        elif pf >= 1.0 and wr >= 45:
            edge_tone = 'warning'
            edge_label = 'Ventaja mixta'
            edge_detail = 'Hay señal operativa, pero todavía no es una lectura contundente.'
        else:
            edge_tone = 'danger'
            edge_label = 'Requiere ajuste'
            edge_detail = 'La ventana de 7d todavía no sostiene una ventaja operativa clara.'

    executive = {
        'best_window': best_window[0] if best_window else None,
        'best_window_pnl': _safe_float(best_window[1].get('pnl_total'), 0.0) if best_window else 0.0,
        'edge_label': edge_label,
        'edge_tone': edge_tone,
        'edge_detail': edge_detail,
        'trades_30d': _safe_int(stats_30d.get('total'), 0),
        'decisive_30d': _safe_int(stats_30d.get('decisive_trades'), 0),
        'cadence_30d': round(_safe_int(stats_30d.get('total'), 0) / 30.0, 2),
        'expectancy': _safe_float(track_record.get('expectancy'), 0.0),
        'avg_win': _safe_float(track_record.get('avg_win'), 0.0),
        'avg_loss': _safe_float(track_record.get('avg_loss'), 0.0),
        'streak_label': _track_record_streak_label(track_record),
        'streak_best_win': _safe_int(track_record.get('best_win_streak'), 0),
        'streak_best_loss': _safe_int(track_record.get('best_loss_streak'), 0),
        'recent_form_compact': track_record.get('recent_form_compact') or '—',
        'dominant_symbol': track_record.get('dominant_symbol'),
        'dominant_symbol_count': _safe_int(track_record.get('dominant_symbol_count'), 0),
    }

    return {
        '24h': stats_24h,
        '7d': stats_7d,
        '30d': stats_30d,
        'track_record': {
            'total': _safe_int(track_record.get('total'), 0),
            'wins': _safe_int(track_record.get('wins'), 0),
            'losses': _safe_int(track_record.get('losses'), 0),
            'break_evens': _safe_int(track_record.get('break_evens'), 0),
            'net_pnl': _safe_float(track_record.get('net_pnl'), 0.0),
            'profit_factor': track_record.get('profit_factor', 0.0),
            'win_rate': _safe_float(track_record.get('win_rate'), 0.0),
            'avg_pnl': _safe_float(track_record.get('avg_pnl'), 0.0),
            'expectancy': _safe_float(track_record.get('expectancy'), 0.0),
            'avg_win': _safe_float(track_record.get('avg_win'), 0.0),
            'avg_loss': _safe_float(track_record.get('avg_loss'), 0.0),
            'best_trade': _safe_float(track_record.get('best_trade'), 0.0),
            'worst_trade': _safe_float(track_record.get('worst_trade'), 0.0),
            'current_streak_type': track_record.get('current_streak_type') or 'none',
            'current_streak_count': _safe_int(track_record.get('current_streak_count'), 0),
            'best_win_streak': _safe_int(track_record.get('best_win_streak'), 0),
            'best_loss_streak': _safe_int(track_record.get('best_loss_streak'), 0),
            'recent_form': track_record.get('recent_form') or [],
            'recent_form_compact': track_record.get('recent_form_compact') or '—',
            'dominant_symbol': track_record.get('dominant_symbol'),
            'dominant_symbol_count': _safe_int(track_record.get('dominant_symbol_count'), 0),
            'dominant_symbol_pnl': _safe_float(track_record.get('dominant_symbol_pnl'), 0.0),
            'first_trade_at': _serialize_dt(track_record.get('first_trade_at')),
            'last_trade_at': _serialize_dt(track_record.get('last_trade_at')),
        },
        'executive': executive,
    }

def _normalize_trade_stats(payload: dict) -> dict:
    return {
        'total': int(payload.get('total', 0) or 0),
        'wins': int(payload.get('wins', 0) or 0),
        'losses': int(payload.get('losses', 0) or 0),
        'break_evens': int(payload.get('break_evens', 0) or 0),
        'decisive_trades': int(payload.get('decisive_trades', 0) or 0),
        'win_rate': float(payload.get('win_rate', 0.0) or 0.0),
        'win_rate_decisive': float(payload.get('win_rate_decisive', 0.0) or 0.0),
        'pnl_total': float(payload.get('pnl_total', 0.0) or 0.0),
        'gross_profit': float(payload.get('gross_profit', 0.0) or 0.0),
        'gross_loss': float(payload.get('gross_loss', 0.0) or 0.0),
        'profit_factor': payload.get('profit_factor', 0.0),
        'since': _serialize_dt(payload.get('since')),
        'epoch': _serialize_dt(payload.get('epoch')),
    }


def get_referrals_summary(user_id: int) -> dict:
    uid = int(user_id)
    bot_username = str(BOT_USERNAME or '').strip().lstrip('@') or 'TradingXHiperPro_bot'
    referral_code = str(uid)
    referral_link = f'https://t.me/{bot_username}?start={referral_code}'
    reward_table = [
        {
            'purchase_days': 15,
            'purchase_label': 'Premium 15 días',
            'reward_days': 7,
            'reward_label': '7 días Premium',
        },
        {
            'purchase_days': 30,
            'purchase_label': 'Premium 30 días',
            'reward_days': 15,
            'reward_label': '15 días Premium',
        },
    ]
    share_text = (
        'Únete a mi bot y activa tu Premium desde este enlace: '
        f'{referral_link}'
    )
    return {
        'user_id': uid,
        'referral_code': referral_code,
        'referral_valid_count': int(get_referral_valid_count(uid) or 0),
        'bot_username': bot_username,
        'referral_link': referral_link,
        'share_text': share_text,
        'reward_table': reward_table,
        'valid_referral_rule': 'El referido cuenta como válido cuando compra Premium 15 o Premium 30.',
        'reward_rule': 'La recompensa se acredita una sola vez por referido válido y siempre se otorga en Premium.',
    }


def get_system_runtime_summary(user_id: int, include_private_details: bool = False) -> dict:
    profile = get_user_profile(int(user_id))
    snapshot = get_system_runtime_snapshot() or {}
    runtime = dict((snapshot.get('runtime') or {}))

    def _serialize_component(item: dict | None) -> dict:
        payload = dict(item or {})
        payload['last_seen_at'] = _serialize_dt(payload.get('last_seen_at'))
        return payload

    def _serialize_activity(item: dict | None) -> dict | None:
        if not item:
            return None
        payload = dict(item)
        payload['at'] = _serialize_dt(payload.get('at'))
        return payload

    latest_manager = runtime.get('latest_trade_manager') or {}
    latest_manager = dict(latest_manager) if latest_manager else None
    if latest_manager and latest_manager.get('manager_heartbeat_at'):
        latest_manager['manager_heartbeat_at'] = _serialize_dt(latest_manager.get('manager_heartbeat_at'))

    latest_open = _serialize_activity(runtime.get('latest_open'))
    latest_close = _serialize_activity(runtime.get('latest_close'))

    if latest_close:
        recent_activity_label = 'Cierres recientes'
        recent_activity_detail = 'Se registró actividad reciente de cierre en la plataforma.'
    elif latest_open:
        recent_activity_label = 'Actividad de trading'
        recent_activity_detail = 'Se registró actividad reciente de apertura en la plataforma.'
    elif runtime.get('scanner_last_event'):
        recent_activity_label = 'Mercado monitoreado'
        recent_activity_detail = 'La plataforma sigue procesando actividad de mercado.'
    else:
        recent_activity_label = 'Sin actividad reciente'
        recent_activity_detail = 'Todavía no hay eventos recientes para mostrar.'

    overall_status = snapshot.get('overall_status') or 'unknown'
    public_summary = {
        'last_update_at': _serialize_dt(snapshot.get('checked_at')),
        'connection_label': 'Sincronizada',
        'active_trades': int(runtime.get('active_trades', 0) or 0),
        'recent_activity_label': recent_activity_label,
        'recent_activity_detail': recent_activity_detail,
        'execution_label': 'Operativa' if overall_status == 'healthy' else ('Atención' if overall_status in {'warning', 'stale'} else 'Incidencia'),
        'execution_detail': 'La lectura mostrada aquí es una vista resumida para el usuario.',
        'plan_notice': 'Tu acceso vigente y configuración se muestran en esta sesión.' if profile.get('plan_active') else 'Tu acceso depende del plan activo y la configuración de tu cuenta.',
    }

    payload = {
        'viewer_user_id': int(profile['user_id']),
        'viewer_plan': profile.get('plan'),
        'overall_status': overall_status,
        'checked_at': _serialize_dt(snapshot.get('checked_at')),
        'public_summary': public_summary,
    }

    if not include_private_details:
        return payload

    payload.update({
        'backend': {
            'status': 'online',
            'message': 'El backend web respondió a esta solicitud.',
            'checked_at': _serialize_dt(snapshot.get('checked_at')),
        },
        'components': {
            'telegram_bot': _serialize_component((snapshot.get('components') or {}).get('telegram_bot')),
            'trading_loop': _serialize_component((snapshot.get('components') or {}).get('trading_loop')),
            'scanner': _serialize_component((snapshot.get('components') or {}).get('scanner')),
        },
        'runtime': {
            **runtime,
            'latest_open': latest_open,
            'latest_close': latest_close,
            'latest_trade_manager': latest_manager,
        },
        'issues': snapshot.get('issues') or [],
    })
    return payload
