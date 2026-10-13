"""Admin opening evidence. Explicit field selection excludes account credentials."""
from datetime import datetime, timezone
import math

SIGNAL_FIELDS = '''setup_state bars_since_reset reset_index extension_atr structural_extreme strategy_id strategy_version strategy_model regime_id regime_version detector_version router_mode router_decision router_regime_source router_candidate_regime router_candidate_confidence score strength h1_bias m15_bias h1_mode m15_mode adx1 adx15 adx5 atr_pct ema_stack_pct ema20_5m signal_reference_price last_candle_t_5m market_context_status stop_price target_price structural_target_price structural_tp_pct resolved_rr sweep_level sweep_idx bars_since_sweep sweep_depth_atr sweep_wick_ratio sweep_rvol'''.split()
LABELS = {'approved_margin_usdc':'Capital aprobado USDC', 'leverage':'Apalancamiento', 'strategy_id':'Estrategia', 'strategy_model':'Modelo', 'strategy_version':'Versión estrategia', 'regime_id':'Régimen', 'score':'Puntuación', 'strength':'Fuerza', 'stop_price':'Stop Loss', 'target_price':'Take Profit', 'resolved_rr':'RR ejecutado', 'sl_ok':'sl_ok', 'tp_ok':'tp_ok'}
PLAN_FIELDS = '''approved_margin_usdc leverage target_notional_usdc actual_notional_usdc requested_qty_coin actual_qty_coin entry_price_preview entry_price sl_price_pct tp_activation_price resolved_rr exit_policy bucket'''.split()


def selected(data, fields):
    data = data if isinstance(data, dict) else {}
    return {k: data.get(k) if isinstance(data.get(k), (str, int, float, bool)) else None for k in fields}


def opening_evidence(*, signal, plan, stop, target, protection):
    return {
        'signal': selected(signal, SIGNAL_FIELDS),
        'plan': selected(plan, PLAN_FIELDS),
        'protection': {'stop_price': stop, 'target_price': target,
                       **selected(protection, ['sl_ok', 'tp_ok'])},
    }


def display(value):
    if value is None or value == '':
        return 'no disponible'
    if isinstance(value, bool):
        return 'sí' if value else 'no'
    if isinstance(value, float):
        return format(value, '.12g') if math.isfinite(value) else 'no disponible'
    return str(value).replace('\n', ' ').replace('\r', ' ')[:200]


def format_admin_open(user_id, label, data):
    data = data or {}
    evidence = data.get('admin_evidence') or {}
    signal = evidence.get('signal') or {}
    plan = evidence.get('plan') or {}
    protection = evidence.get('protection') or {}
    stamp = data.get('opened_at_ms')
    try:
        moment = datetime.fromtimestamp(float(stamp)/1000, timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        moment = 'no disponible'
    ref = f"{user_id}:{data.get('symbol', '?')}:{stamp or '?'}"
    lines = ['🟢 APERTURA · INFORME ADMIN', f'ID de correlación: {ref}',
             f'Usuario: {display(label)} | ID: {user_id}', f'Hora UTC: {moment}',
             f"Par: {display(data.get('symbol'))} | Dirección: {display(data.get('direction') or data.get('side'))}",
             f"Entrada ejecutada: {display(data.get('entry_price'))}",
             f"Cantidad: {display(data.get('qty'))} | Nocional USDC: {display(data.get('notional_usdc'))}",
             f"Gestor iniciado: {display(data.get('manager_started'))}"]
    for title, fields, values in [('ESTRATEGIA Y SEÑAL', SIGNAL_FIELDS, signal),
                                   ('CAPITAL Y PLAN EJECUTADO', PLAN_FIELDS, plan),
                                   ('PROTECCIONES EN EXCHANGE', ['stop_price', 'target_price', 'sl_ok', 'tp_ok'], protection)]:
        lines.extend(['', title])
        lines.extend(f'{LABELS.get(key, key)}: {display(values.get(key))}' for key in fields)
    try:
        entry, qty = float(data['entry_price']), float(data['qty'])
        stop, target = float(protection['stop_price']), float(protection['target_price'])
        sign = 1 if str(data.get('direction') or data.get('side')).lower() == 'long' else -1
        if min(entry, qty, stop, target) > 0:
            lines.extend(['', 'ESTIMACIONES A LOS NIVELES (sin comisiones ni deslizamiento)',
                          f'PnL al SL USDC: {sign*(stop-entry)*qty:.8f}',
                          f'PnL al TP USDC: {sign*(target-entry)*qty:.8f}'])
        reference = float(signal['signal_reference_price'])
        if reference > 0:
            lines.append(f'Desviación adversa entrada/señal %: {sign*(entry/reference-1)*100:.6f}')
        candle = float(signal['last_candle_t_5m'])
        if candle > 0 and stamp:
            lines.append(f'Tiempo apertura − vela señal (segundos): {(float(stamp)-candle)/1000:.3f}')
    except (KeyError, TypeError, ValueError):
        pass
    lines.append('sl_ok/tp_ok reflejan la respuesta al instalar protección, no garantizan ejecución futura.')
    return '\n'.join(lines)


def message_parts(text):
    # 1,500 Unicode code points <= 3,000 UTF-16 units, leaving room for headers.
    text = str(text)
    chunks = [text[i:i+1500] for i in range(0, len(text), 1500)] or ['']
    return [f'[{i+1}/{len(chunks)}]\n{chunk}' for i, chunk in enumerate(chunks)] if len(chunks) > 1 else chunks
