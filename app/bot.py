# ============================================================
# BOT PRINCIPAL – TRADING X HYPER PRO
# Archivo 8/9 – Sistema de control vía Telegram (VERSIÓN FINAL)
# MOD: Capital manual eliminado. El bot opera usando el balance real del exchange.
# ============================================================

import asyncio
import logging
import os

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from app.config import TELEGRAM_BOT_TOKEN, BOT_NAME, MINIAPP_URL

# ============================================================
# ENV (Admin / Bot)
# ============================================================
BOT_USERNAME = os.getenv("BOT_USERNAME", "TradingXHiperPro_bot")
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0") or "0")
ADMIN_WHATSAPP_LINK = os.getenv("ADMIN_WHATSAPP_LINK", "").strip()

from app import database as db_module
from app.database import (
    has_accepted_terms,
    create_user,
    save_user_wallet,
    save_user_private_key,
    set_trading_status,
    get_user_wallet,
    get_user_private_key,
    user_is_ready,
    get_user_trades,
    # Referidos (solo conteo)
    set_referrer,
    get_referral_valid_count,
    # Planes
    ensure_access_on_activate,
    is_user_registered,
    activate_premium_plan,
    # Admin visual
    get_admin_visual_stats,
    get_last_operation,
    get_admin_trade_stats,
    reset_admin_trade_stats_epoch,
    # Stats por usuario (admin)
    get_user_trade_stats,
    reset_user_trade_stats_epoch,
    publish_runtime_component,
    describe_runtime_identity,
)

from app.hyperliquid_client import get_balance
from app.trading_loop import trading_loop


def _strategy_overview_fallback(user_id: int | None = None, limit_recent_events: int = 25) -> dict:
    return {
        'scope_user_id': int(user_id) if user_id is not None else None,
        'counts': {},
        'breakdown': {'execution_mode': {}, 'strategy': {}, 'regime': {}, 'event_type': {}},
        'recent_events': [],
        'partial': True,
        'unavailable': True,
        'error': 'strategy_telemetry_unavailable',
    }


def _strategy_events_fallback(
    user_id: int | None = None,
    *,
    limit: int = 100,
    event_type: str | None = None,
    execution_mode: str | None = None,
    symbol: str | None = None,
    strategy_id: str | None = None,
    regime_id: str | None = None,
) -> list[dict]:
    return []


get_strategy_runtime_overview = getattr(db_module, 'get_strategy_runtime_overview', _strategy_overview_fallback)
get_strategy_router_events = getattr(db_module, 'get_strategy_router_events', _strategy_events_fallback)



# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

_RUNTIME_ALERTS: dict[str, float] = {}


def _build_runtime_issue_message(component: str, outcome: dict) -> str:
    identity = (outcome or {}).get('identity') or {}
    parts = [
        '⚠️ Runtime heartbeat con fallo',
        f"Componente: {component}",
        f"Rol: {identity.get('process_role') or 'unknown'}",
        f"Instancia: {identity.get('runtime_instance') or 'unknown'}",
        f"DB: {identity.get('db_name') or 'unknown'}",
    ]
    mongo_target = identity.get('mongo_target')
    if mongo_target:
        parts.append(f"Mongo: {mongo_target}")
    errors = []
    if outcome.get('primary_error'):
        errors.append(f"primary={outcome['primary_error']}")
    if outcome.get('shadow_error'):
        errors.append(f"shadow={outcome['shadow_error']}")
    if outcome.get('readback_error'):
        errors.append(f"readback={outcome['readback_error']}")
    if errors:
        parts.append('Errores: ' + ' | '.join(errors[:3]))
    if outcome.get('verified') is False:
        parts.append('Verificación: readback no confirmado')
    return '\n'.join(parts)


async def _send_runtime_issue_alert(context: ContextTypes.DEFAULT_TYPE, component: str, outcome: dict, cooldown_seconds: int = 300):
    try:
        admin_id = int(ADMIN_TELEGRAM_ID or 0)
    except Exception:
        admin_id = 0
    if admin_id <= 0:
        return

    dedupe_key = f"runtime:{component}:{(outcome or {}).get('primary_error')}:{(outcome or {}).get('shadow_error')}:{(outcome or {}).get('readback_error')}"
    now = asyncio.get_running_loop().time()
    last_sent = float(_RUNTIME_ALERTS.get(dedupe_key) or 0.0)
    if now - last_sent < float(cooldown_seconds or 300):
        return
    _RUNTIME_ALERTS[dedupe_key] = now


def _fmt_int(value) -> str:
    try:
        return f"{int(value or 0):,}".replace(',', '.')
    except Exception:
        return '0'


def _strategy_bucket_line(label: str, bucket: dict | None, *, shadow: bool = False) -> str:
    data = bucket or {}
    signals = int(data.get('signals_total') or 0)
    selected = int(data.get('selected_total') or 0)
    opened = int(data.get('trades_opened_total') or 0)
    shadow_hits = int(data.get('shadow_signal_total') or 0)
    if shadow:
        return f"• {label}: shadow={_fmt_int(shadow_hits)} | señales={_fmt_int(signals)} | eventos={_fmt_int(data.get('events_total') or 0)}"
    return f"• {label}: señales={_fmt_int(signals)} | select={_fmt_int(selected)} | abiertas={_fmt_int(opened)}"


def _event_icon(event_type: str | None) -> str:
    mapping = {
        'trade_opened': '🟢',
        'signal_selected': '🎯',
        'signal_blocked_risk': '🛑',
        'shadow_opportunity': '👻',
        'router_blocked': '🚫',
        'strategy_rejected': '⚪',
        'signal_weak': '🪫',
        'scanner_no_signal': '📭',
        'cycle_timeout': '⏱️',
    }
    return mapping.get(str(event_type or '').strip().lower(), '•')


def _compact_strategy_event(event: dict | None) -> str:
    item = event or {}
    symbol = str(item.get('symbol') or '?').upper()
    strategy_id = str(item.get('strategy_id') or 'unknown').strip().lower()
    regime_id = str(item.get('regime_id') or 'unknown').strip().lower()
    event_type = str(item.get('event_type') or 'event').strip().lower()
    direction = str(item.get('direction') or '').strip().lower()
    shadow_summary = item.get('shadow_summary') if isinstance(item.get('shadow_summary'), dict) else {}
    signal_summary = item.get('signal_summary') if isinstance(item.get('signal_summary'), dict) else {}
    extra = item.get('extra') if isinstance(item.get('extra'), dict) else {}

    if event_type == 'shadow_opportunity':
        direction = str(shadow_summary.get('direction') or direction or '-').lower()
        score = shadow_summary.get('score')
        score_txt = f" | score={float(score):.2f}" if isinstance(score, (int, float)) else ''
        return f"{_event_icon(event_type)} {symbol} | {strategy_id} | {regime_id} | {direction or '-'}{score_txt}"

    if event_type == 'signal_blocked_risk':
        risk_reason = str(extra.get('risk_reason') or signal_summary.get('risk_reason') or '-').strip()
        risk_reason = risk_reason[:32]
        return f"{_event_icon(event_type)} {symbol} | {strategy_id} | {regime_id} | {risk_reason or '-'}"

    if event_type == 'router_blocked':
        router_reason = str(extra.get('router_reason') or extra.get('reject_reason') or item.get('reason') or signal_summary.get('router_decision') or '-').strip()
        candidate_regime = str(extra.get('router_candidate_regime') or signal_summary.get('router_candidate_regime') or '').strip().lower()
        confidence = extra.get('router_candidate_confidence')
        detail_parts = [router_reason]
        if candidate_regime:
            detail_parts.append(f"cand={candidate_regime}")
        try:
            if confidence is not None and confidence != '':
                detail_parts.append(f"cf={float(confidence):.2f}")
        except Exception:
            pass
        router_txt = ' '.join([part for part in detail_parts if part]).strip()[:56]
        return f"{_event_icon(event_type)} {symbol} | {strategy_id} | {regime_id} | {router_txt or '-'}"

    if event_type == 'strategy_rejected':
        reject_reason = str(extra.get('reason') or extra.get('reject_reason') or item.get('reason') or signal_summary.get('router_decision') or '-').strip()
        reject_reason = reject_reason[:32]
        return f"{_event_icon(event_type)} {symbol} | {strategy_id} | {regime_id} | {reject_reason or '-'}"

    detail = direction or '-'
    return f"{_event_icon(event_type)} {symbol} | {strategy_id} | {regime_id} | {detail}"


def _build_strategy_status_text(overview: dict, recent_live: list[dict], recent_shadow: list[dict], recent_blocked: list[dict]) -> str:
    counts = overview.get('counts') if isinstance(overview.get('counts'), dict) else {}
    breakdown = overview.get('breakdown') if isinstance(overview.get('breakdown'), dict) else {}
    strategy_breakdown = breakdown.get('strategy') if isinstance(breakdown.get('strategy'), dict) else {}
    regime_breakdown = breakdown.get('regime') if isinstance(breakdown.get('regime'), dict) else {}
    event_type_breakdown = breakdown.get('event_type') if isinstance(breakdown.get('event_type'), dict) else {}

    lines = [
        '🧠 STRATEGY / REGIME STATUS',
        '───────────────────────────',
        f"Usuarios runtime: {_fmt_int(counts.get('unique_users'))} | Símbolos: {_fmt_int(counts.get('unique_symbols'))} | Filas: {_fmt_int(counts.get('summary_rows'))}",
        f"Eventos: {_fmt_int(counts.get('events_total'))} | Live: {_fmt_int(counts.get('live_events_total'))} | Shadow: {_fmt_int(counts.get('shadow_events_total'))}",
        f"Señales: {_fmt_int(counts.get('signals_total'))} | Seleccionadas: {_fmt_int(counts.get('selected_total'))} | Abiertas: {_fmt_int(counts.get('trades_opened_total'))}",
        f"Blocked risk: {_fmt_int(event_type_breakdown.get('signal_blocked_risk'))} | Shadow opps: {_fmt_int(event_type_breakdown.get('shadow_opportunity'))} | Regime changes: {_fmt_int(counts.get('regime_changes_total'))}",
        f"Router blocked: {_fmt_int(event_type_breakdown.get('router_blocked'))} | Strategy rejected: {_fmt_int(event_type_breakdown.get('strategy_rejected'))} | Weak: {_fmt_int(event_type_breakdown.get('signal_weak'))}",
        f"No-signal cycles: {_fmt_int(event_type_breakdown.get('scanner_no_signal'))} | Timeouts: {_fmt_int(event_type_breakdown.get('cycle_timeout'))}",
        '',
        '📡 ESTRATEGIAS',
        _strategy_bucket_line('breakout_reset', strategy_breakdown.get('breakout_reset')),
        _strategy_bucket_line('liquidity_sweep_reversal', strategy_breakdown.get('liquidity_sweep_reversal')),
        _strategy_bucket_line('range_mean_reversion', strategy_breakdown.get('range_mean_reversion'), shadow=True),
        '',
        '🧭 REGÍMENES',
        f"• trend_continuation: {_fmt_int((regime_breakdown.get('trend_continuation') or {}).get('events_total'))}",
        f"• volatile_sweep: {_fmt_int((regime_breakdown.get('volatile_sweep') or {}).get('events_total'))}",
        f"• range: {_fmt_int((regime_breakdown.get('range') or {}).get('events_total'))}",
        f"• unknown: {_fmt_int((regime_breakdown.get('unknown') or {}).get('events_total'))}",
    ]

    if recent_live:
        lines.extend(['', '🔥 ÚLTIMOS LIVE'])
        lines.extend(_compact_strategy_event(item) for item in recent_live[:4])
    if recent_shadow:
        lines.extend(['', '👻 ÚLTIMOS SHADOW'])
        lines.extend(_compact_strategy_event(item) for item in recent_shadow[:4])
    if recent_blocked:
        lines.extend(['', '🛑 ÚLTIMOS BLOQUEADOS'])
        lines.extend(_compact_strategy_event(item) for item in recent_blocked[:4])

    msg = '\n'.join(lines).strip()
    if len(msg) > 3900:
        msg = msg[:3890].rstrip() + '\n…'
    return msg


async def strategy_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or int(user.id) != ADMIN_TELEGRAM_ID:
        await update.effective_message.reply_text('⛔ Acceso no autorizado.')
        return

    try:
        overview = get_strategy_runtime_overview(None, limit_recent_events=10) or {}
        recent_live = get_strategy_router_events(None, limit=4, execution_mode='live') or []
        recent_shadow = get_strategy_router_events(None, limit=4, event_type='shadow_opportunity') or []
        recent_blocked = (get_strategy_router_events(None, limit=2, event_type='signal_blocked_risk') or []) + (get_strategy_router_events(None, limit=2, event_type='router_blocked') or [])
        message = _build_strategy_status_text(overview, recent_live, recent_shadow, recent_blocked[:4])
    except Exception as exc:
        logging.exception('Fallo /strategy')
        message = f'⚠ No se pudo construir el resumen estratégico: {exc}'

    await update.effective_message.reply_text(message)

    try:
        await context.bot.send_message(chat_id=admin_id, text=_build_runtime_issue_message(component, outcome))
    except Exception as exc:
        logging.error('No se pudo enviar alerta de runtime component=%s error=%s', component, exc)


# ============================================================
# MENÚ PRINCIPAL
# ============================================================

def _launcher_menu(user_id: int | None = None):
    kb = []
    kb.append([InlineKeyboardButton("📜 Ver políticas", callback_data="policies")])
    if MINIAPP_URL:
        kb.append([InlineKeyboardButton("🚀 Abrir MiniApp", web_app=WebAppInfo(url=MINIAPP_URL))])
    return InlineKeyboardMarkup(kb)


def main_menu(user_id: int | None = None):
    return _launcher_menu(user_id)


def _miniapp_entry_text(*, terms_accepted: bool = False, is_admin: bool = False) -> str:
    base = (
        f"🚀 *Bienvenido a {BOT_NAME}*\n\n"
        "El bot automático diseñado para operar en *Hyperliquid* con una experiencia más seria, rápida y profesional.\n\n"
        "✅ Ejecuta trading automático sin depender de que estés conectado\n"
        "✅ Gestiona la operativa desde una *MiniApp* visual y centralizada\n"
        "✅ Controla wallet, clave, estado operativo, historial y rendimiento desde un solo panel\n"
        "✅ Mantén Telegram como canal de alertas, avisos y acceso rápido\n\n"
        "Para empezar correctamente:\n"
        "1. *Lee las Políticas* aquí en Telegram\n"
        "2. Entra en la *MiniApp* y *confirma su aceptación*\n"
        "3. Completa la configuración y activa la operativa"
    )
    if not terms_accepted:
        base += "\n\n⚠️ *Las Políticas son obligatorias.* Debes leerlas aquí y luego confirmar su aceptación dentro de la MiniApp para poder trabajar el bot."
    else:
        base += "\n\n✅ Tu confirmación de políticas ya fue registrada. Solo falta abrir la MiniApp para continuar."
    if MINIAPP_URL:
        base += "\n\n🚀 Pulsa *Abrir MiniApp* para entrar al panel operativo."
    if is_admin:
        base += "\n\n🛠 Tu acceso administrativo también se gestiona desde la MiniApp."
    return base


async def _send_miniapp_redirect_message(target, user_id: int, title: str, as_edit: bool = True):
    text = (
        f"{title}\n\n"
        "Esta acción ahora se gestiona desde la *MiniApp* para mantener una sola interfaz operativa y evitar configuraciones duplicadas.\n\n"
        "📜 Acepta las *Políticas* si aún no lo has hecho.\n"
        "🚀 Después entra en la *MiniApp* para continuar."
    )
    markup = _launcher_menu(user_id)
    if as_edit:
        await target.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
    else:
        await target.reply_text(text, parse_mode="Markdown", reply_markup=markup)


# ============================================================
# /START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user
    user_id = user.id
    create_user(user.id, user.username)

    # Sistema de referidos (solo desde enlace /start)
    if context.args:
        ref = context.args[0]
        if ref.isdigit() and int(ref) != user.id:
            set_referrer(user.id, int(ref))

    terms_accepted = has_accepted_terms(user_id)
    await update.message.reply_text(
        _miniapp_entry_text(terms_accepted=terms_accepted, is_admin=(user_id == ADMIN_TELEGRAM_ID)),
        reply_markup=_launcher_menu(user_id),
        parse_mode="Markdown"
    )


async def miniapp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    create_user(update.effective_user.id, update.effective_user.username)
    terms_accepted = has_accepted_terms(user_id)
    await update.message.reply_text(
        _miniapp_entry_text(terms_accepted=terms_accepted, is_admin=(user_id == ADMIN_TELEGRAM_ID)),
        reply_markup=_launcher_menu(user_id),
        parse_mode="Markdown",
    )



# ============================================================
# DASHBOARD
# ============================================================
async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await _send_miniapp_redirect_message(q, q.from_user.id, "📊 *Dashboard trasladado a la MiniApp*")


# ============================================================
# SUBMENÚ WALLET / PRIVATE KEY
# ============================================================

async def wallet_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await _send_miniapp_redirect_message(q, q.from_user.id, "💳 *Configuración trasladada a la MiniApp*")


async def set_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data.clear()
    await _send_miniapp_redirect_message(q, q.from_user.id, "🔗 *La wallet ahora se configura en la MiniApp*")


async def set_pk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data.clear()
    await _send_miniapp_redirect_message(q, q.from_user.id, "🔐 *La private key ahora se configura en la MiniApp*")


# ============================================================
# INPUTS
# ============================================================

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    legacy_flow_keys = [
        "awaiting_activate_plan_id",
        "awaiting_user_stats_id",
        "awaiting_wallet",
        "awaiting_pk",
    ]
    had_legacy_state = any(context.user_data.get(key) for key in legacy_flow_keys)
    context.user_data.clear()

    if had_legacy_state:
        await update.message.reply_text(
            "⚠️ Ese flujo de Telegram ya fue retirado. Continúa desde la MiniApp para evitar inconsistencias.",
            reply_markup=_launcher_menu(user_id),
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text(
        "💡 Este bot ahora se usa desde la MiniApp. Telegram queda para alertas, notificaciones y acceso rápido.",
        reply_markup=_launcher_menu(user_id),
        parse_mode="Markdown",
    )


# ============================================================
# ACTIVAR / DESACTIVAR TRADING
# ============================================================

async def activate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await _send_miniapp_redirect_message(q, q.from_user.id, "▶ *La activación de trading ahora vive en la MiniApp*")

async def deactivate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await _send_miniapp_redirect_message(q, q.from_user.id, "⏸ *La pausa de trading ahora vive en la MiniApp*")

# ============================================================
# OPERACIONES
# ============================================================

async def operations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await _send_miniapp_redirect_message(q, q.from_user.id, "📈 *Las operaciones y el track record ahora se consultan en la MiniApp*")

# ============================================================
# REFERIDOS
# ============================================================

async def referrals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await _send_miniapp_redirect_message(q, q.from_user.id, "👥 *Los referidos ahora se consultan desde la MiniApp*")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_TELEGRAM_ID:
        await q.edit_message_text("⛔ Acceso no autorizado.", reply_markup=_launcher_menu(q.from_user.id))
        return
    await _send_miniapp_redirect_message(q, q.from_user.id, "🛠 *La administración operativa ahora se hace desde la MiniApp*")

async def activate_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != ADMIN_TELEGRAM_ID:
        await q.edit_message_text("⛔ Acceso no autorizado.", reply_markup=main_menu(q.from_user.id))
        return

    context.user_data.clear()
    context.user_data["awaiting_activate_plan_id"] = True
    await q.edit_message_text(
        "✅ *Activar Plan Premium*\n\nIngresa el *ID de Telegram* del usuario:",
        parse_mode="Markdown"
    )


# ============================================================
# ADMIN – INFORMACIÓN VISUAL
# ============================================================

async def admin_visual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != ADMIN_TELEGRAM_ID:
        await q.edit_message_text("⛔ Acceso no autorizado.", reply_markup=main_menu(q.from_user.id))
        return

    stats = get_admin_visual_stats() or {}
    msg = (
        "📊 *INFORMACIÓN VISUAL (ADMIN)*\n"
        "───────────────────────────\n"
        f"👤 Total registrados: `{stats.get('total_users', 0)}`\n"
        f"🆓 Free antiguos: `{stats.get('free_old', 0)}`\n"
        f"💎 Premium activos: `{stats.get('premium_active', 0)}`\n"
        f"⌛ Premium vencidos: `{stats.get('premium_expired', 0)}`\n"
        "───────────────────────────"
    )

    await q.edit_message_text(msg, reply_markup=main_menu(q.from_user.id), parse_mode="Markdown")




# ============================================================
# ADMIN – ESTADÍSTICAS DE TRADING (24h / 7d / 30d)
# ============================================================

def _format_pf(pf):
    try:
        if pf == float("inf"):
            return "∞"
        return f"{float(pf):.2f}"
    except Exception:
        return "0.00"



# ============================================================
# ADMIN – ESTADÍSTICAS POR USUARIO (24h / 7d / 30d)
# ============================================================

def _admin_user_stats_menu():
    kb = [
        [
            InlineKeyboardButton("📅 24h", callback_data="admin_user_stats_24h"),
            InlineKeyboardButton("📆 7d", callback_data="admin_user_stats_7d"),
            InlineKeyboardButton("🗓 30d", callback_data="admin_user_stats_30d"),
        ],
        [InlineKeyboardButton("♻️ Reset Usuario", callback_data="admin_user_stats_reset_confirm")],
        [InlineKeyboardButton("⬅ Volver", callback_data="admin_panel")],
    ]
    return InlineKeyboardMarkup(kb)


async def admin_user_stats_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != ADMIN_TELEGRAM_ID:
        await q.edit_message_text("⛔ Acceso no autorizado.", reply_markup=main_menu(q.from_user.id))
        return

    context.user_data.clear()
    context.user_data["awaiting_user_stats_id"] = True
    await q.edit_message_text(
        "👤 *ESTADÍSTICAS POR USUARIO*\n\nIngresa el *ID de Telegram* del usuario:",
        parse_mode="Markdown"
    )


async def _admin_user_stats_show(update: Update, context: ContextTypes.DEFAULT_TYPE, label: str, hours: int):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != ADMIN_TELEGRAM_ID:
        await q.edit_message_text("⛔ Acceso no autorizado.", reply_markup=main_menu(q.from_user.id))
        return

    target_id = context.user_data.get("user_stats_target_id")
    if not target_id:
        await q.edit_message_text(
            "👤 *ESTADÍSTICAS POR USUARIO*\n\n⚠️ No hay usuario seleccionado.\nPulsa el botón nuevamente e ingresa el ID.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👤 Elegir Usuario", callback_data="admin_user_stats_start")],[InlineKeyboardButton("⬅ Volver", callback_data="admin_panel")]]),
        )
        return

    stats = get_user_trade_stats(user_id=int(target_id), hours=int(hours)) or {}

    total = int(stats.get("total", 0) or 0)
    wins = int(stats.get("wins", 0) or 0)
    losses = int(stats.get("losses", 0) or 0)
    break_evens = int(stats.get("break_evens", 0) or 0)
    win_rate = float(stats.get("win_rate", 0.0) or 0.0)
    win_rate_decisive = float(stats.get("win_rate_decisive", 0.0) or 0.0)
    pnl_total = float(stats.get("pnl_total", 0.0) or 0.0)
    gross_profit = float(stats.get("gross_profit", 0.0) or 0.0)
    gross_loss = float(stats.get("gross_loss", 0.0) or 0.0)
    pf = stats.get("profit_factor", 0.0)

    if total == 0:
        msg = (
            f"👤 *ESTADÍSTICAS POR USUARIO*\n"
            f"Usuario: `{int(target_id)}`\n"
            "───────────────────────────\n"
            f"Período: *{label}*\n"
            "Sin datos en este período.\n"
            "───────────────────────────"
        )
    else:
        msg = (
            f"👤 *ESTADÍSTICAS POR USUARIO*\n"
            f"Usuario: `{int(target_id)}`\n"
            "───────────────────────────\n"
            f"📈 *ESTADÍSTICAS ({label})*\n"
            "───────────────────────────\n"
            f"🧾 Trades: `{total}`\n"
            f"✅ Wins: `{wins}`\n"
            f"❌ Losses: `{losses}`\n"
            f"⚪ BreakEven: `{break_evens}`\n"
            f"🎯 WinRate: `{win_rate:.2f}%`\n"
            f"🎯 WinRate s/BE: `{win_rate_decisive:.2f}%`\n"
            "───────────────────────────\n"
            f"💰 PnL Neto: `{pnl_total:.6f}` USDC\n"
            f"🟢 Ganancias: `{gross_profit:.6f}`\n"
            f"🔴 Pérdidas: `{gross_loss:.6f}`\n"
            f"📊 Profit Factor: `{_format_pf(pf)}`\n"
            "───────────────────────────"
        )

    await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=_admin_user_stats_menu())


async def admin_user_stats_24h(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _admin_user_stats_show(update, context, "24h", 24)


async def admin_user_stats_7d(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _admin_user_stats_show(update, context, "7 días", 24 * 7)


async def admin_user_stats_30d(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _admin_user_stats_show(update, context, "30 días", 24 * 30)


async def admin_user_stats_reset_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != ADMIN_TELEGRAM_ID:
        await q.edit_message_text("⛔ Acceso no autorizado.", reply_markup=main_menu(q.from_user.id))
        return

    target_id = context.user_data.get("user_stats_target_id")
    if not target_id:
        await q.edit_message_text(
            "⚠️ No hay usuario seleccionado.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👤 Elegir Usuario", callback_data="admin_user_stats_start")],[InlineKeyboardButton("⬅ Volver", callback_data="admin_panel")]]),
        )
        return

    kb = [[
        InlineKeyboardButton("✅ Sí, resetear", callback_data="admin_user_stats_reset_do"),
        InlineKeyboardButton("❌ Cancelar", callback_data="admin_user_stats_24h"),
    ]]

    msg = (
        "♻️ *RESETEAR ESTADÍSTICAS (USUARIO)*\n"
        "───────────────────────────\n"
        f"Usuario: `{int(target_id)}`\n\n"
        "Esto reinicia el conteo de estadísticas (24h/7d/30d)\n"
        "a partir de *ahora* para este usuario.\n\n"
        "✅ El historial de operaciones NO se borra.\n"
        "Solo cambia el punto de inicio para el panel de estadísticas.\n"
        "───────────────────────────\n"
        "¿Confirmas el reset?"
    )

    await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))


async def admin_user_stats_reset_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != ADMIN_TELEGRAM_ID:
        await q.edit_message_text("⛔ Acceso no autorizado.", reply_markup=main_menu(q.from_user.id))
        return

    target_id = context.user_data.get("user_stats_target_id")
    if not target_id:
        await q.edit_message_text(
            "⚠️ No hay usuario seleccionado.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👤 Elegir Usuario", callback_data="admin_user_stats_start")],[InlineKeyboardButton("⬅ Volver", callback_data="admin_panel")]]),
        )
        return

    try:
        reset_user_trade_stats_epoch(int(target_id))
        msg = (
            "✅ *Stats del usuario reseteadas*\n"
            f"Usuario: `{int(target_id)}`\n\n"
            "Desde este momento las estadísticas (24h/7d/30d)\n"
            "comienzan en *cero* para este usuario."
        )
    except Exception as e:
        msg = f"⚠ No se pudo resetear stats del usuario: `{e}`"

    await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=_admin_user_stats_menu())

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != ADMIN_TELEGRAM_ID:
        await q.edit_message_text("⛔ Acceso no autorizado.", reply_markup=main_menu(q.from_user.id))
        return

    kb = [
        [InlineKeyboardButton("📅 Últimas 24h", callback_data="admin_stats_24h")],
        [InlineKeyboardButton("📆 Últimos 7 días", callback_data="admin_stats_7d")],
        [InlineKeyboardButton("🗓 Últimos 30 días", callback_data="admin_stats_30d")],
        [InlineKeyboardButton("♻️ Reset Stats", callback_data="admin_stats_reset_confirm")],
        [InlineKeyboardButton("⬅ Volver", callback_data="admin_panel")],
    ]
    await q.edit_message_text(
        "📈 *ESTADÍSTICAS DE TRADING (ADMIN)*\nSelecciona el período:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def _admin_stats_show(update: Update, context: ContextTypes.DEFAULT_TYPE, label: str, hours: int):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != ADMIN_TELEGRAM_ID:
        await q.edit_message_text("⛔ Acceso no autorizado.", reply_markup=main_menu(q.from_user.id))
        return

    stats = get_admin_trade_stats(hours=hours) or {}

    total = int(stats.get("total", 0) or 0)
    wins = int(stats.get("wins", 0) or 0)
    losses = int(stats.get("losses", 0) or 0)
    break_evens = int(stats.get("break_evens", 0) or 0)
    win_rate = float(stats.get("win_rate", 0.0) or 0.0)
    win_rate_decisive = float(stats.get("win_rate_decisive", 0.0) or 0.0)
    pnl_total = float(stats.get("pnl_total", 0.0) or 0.0)
    gross_profit = float(stats.get("gross_profit", 0.0) or 0.0)
    gross_loss = float(stats.get("gross_loss", 0.0) or 0.0)
    pf = stats.get("profit_factor", 0.0)

    if total == 0:
        msg = (
            f"📈 *ESTADÍSTICAS ({label})*\n"
            "───────────────────────────\n"
            "Sin datos en este período.\n"
            "───────────────────────────"
        )
    else:
        msg = (
            f"📈 *ESTADÍSTICAS ({label})*\n"
            "───────────────────────────\n"
            f"🧾 Trades: `{total}`\n"
            f"✅ Wins: `{wins}`\n"
            f"❌ Losses: `{losses}`\n"
            f"⚪ BreakEven: `{break_evens}`\n"
            f"🎯 WinRate: `{win_rate:.2f}%`\n"
            f"🎯 WinRate s/BE: `{win_rate_decisive:.2f}%`\n"
            "───────────────────────────\n"
            f"💰 PnL Neto: `{pnl_total:.6f}` USDC\n"
            f"🟢 Ganancias: `{gross_profit:.6f}`\n"
            f"🔴 Pérdidas: `{gross_loss:.6f}`\n"
            f"📊 Profit Factor: `{_format_pf(pf)}`\n"
            "───────────────────────────"
        )

    kb = [
        [InlineKeyboardButton("📅 24h", callback_data="admin_stats_24h"),
         InlineKeyboardButton("📆 7d", callback_data="admin_stats_7d"),
         InlineKeyboardButton("🗓 30d", callback_data="admin_stats_30d")],
        [InlineKeyboardButton("♻️ Reset Stats", callback_data="admin_stats_reset_confirm")],
        [InlineKeyboardButton("⬅ Volver", callback_data="admin_stats")],
    ]

    await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))


async def admin_stats_24h(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _admin_stats_show(update, context, "24h", 24)


async def admin_stats_7d(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _admin_stats_show(update, context, "7 días", 24 * 7)


async def admin_stats_30d(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _admin_stats_show(update, context, "30 días", 24 * 30)


async def admin_stats_reset_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != ADMIN_TELEGRAM_ID:
        await q.edit_message_text("⛔ Acceso no autorizado.", reply_markup=main_menu(q.from_user.id))
        return

    kb = [
        [
            InlineKeyboardButton("✅ Sí, resetear", callback_data="admin_stats_reset_do"),
            InlineKeyboardButton("❌ Cancelar", callback_data="admin_stats"),
        ]
    ]

    msg = (
        "♻️ *RESETEAR ESTADÍSTICAS*\n"
        "───────────────────────────\n"
        "Esto reinicia el conteo de estadísticas (24h/7d/30d)\n"
        "a partir de *ahora*.\n\n"
        "✅ El historial de operaciones NO se borra.\n"
        "Solo cambia el punto de inicio para el panel de estadísticas.\n"
        "───────────────────────────\n"
        "¿Confirmas el reset?"
    )

    await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))


async def admin_stats_reset_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != ADMIN_TELEGRAM_ID:
        await q.edit_message_text("⛔ Acceso no autorizado.", reply_markup=main_menu(q.from_user.id))
        return

    try:
        reset_admin_trade_stats_epoch()
        msg = (
            "✅ *Stats reseteadas*\n"
            "Desde este momento las estadísticas (24h/7d/30d)\n"
            "comienzan en *cero* para la nueva configuración."
        )
    except Exception as e:
        msg = f"⚠ No se pudo resetear stats: `{e}`"

    kb = [
        [InlineKeyboardButton("📈 Ver Estadísticas", callback_data="admin_stats")],
        [InlineKeyboardButton("⬅ Admin", callback_data="admin_panel")],
    ]
    await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))


# ============================================================
# POLÍTICAS
# ============================================================

POLICY_TEXT = (
    "📜 *POLÍTICAS Y CONDICIONES DE USO*\n"
    "───────────────────────────\n"
    "1) Este bot es una herramienta automática y NO es asesoría financiera.\n"
    "2) El trading de criptomonedas implica alto riesgo de pérdida parcial o total del capital.\n"
    "3) El usuario es el único responsable de su capital, su cuenta del exchange y sus credenciales (wallet/private key).\n"
    "4) Resultados pasados NO garantizan resultados futuros.\n"
    "5) El bot ejecuta operaciones automáticamente mientras el trading esté activo.\n"
    "6) Queda prohibido el uso para fraude, suplantación, lavado de dinero o actividades ilegales.\n"
    "7) No se aceptarán reclamaciones por pérdidas financieras derivadas de la volatilidad del mercado.\n"
    "8) El servicio puede suspenderse si se detecta abuso.\n"
    "───────────────────────────\n"
    "Léelas con atención. La *confirmación final* que habilita la operativa se registra dentro de la *MiniApp*.\n"
)

async def policies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    kb = [
        [InlineKeyboardButton("✅ Ya las leí", callback_data="policies_accept")],
        [InlineKeyboardButton("⬅️ Volver", callback_data="back")],
    ]
    await q.edit_message_text(POLICY_TEXT, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def policies_accept(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    already_confirmed = has_accepted_terms(q.from_user.id)
    message = (
        "📜 *Políticas revisadas.*\n\n"
        "El siguiente paso es abrir la *MiniApp* y usar *Confirmar aceptación* para registrar el consentimiento que habilita la operativa."
    )
    if already_confirmed:
        message = (
            "✅ *La confirmación de políticas ya está registrada.*\n\n"
            "Puedes volver a la *MiniApp* para seguir operando con normalidad."
        )
    await q.edit_message_text(
        message,
        parse_mode="Markdown",
        reply_markup=main_menu(q.from_user.id),
    )

# ============================================================
# INFO
# ============================================================

async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = q.from_user.id
    msg = (
        "ℹ️ *INFORMACIÓN DEL SERVICIO*\n"
        "───────────────────────────\n"
        "• Telegram queda para notificaciones, alertas y acceso rápido.\n"
        "• La configuración, control, historial y administración se gestionan desde la MiniApp.\n"
        "• Si cambias wallet, private key, plan o estado operativo, hazlo desde la MiniApp para mantener coherencia."
    )
    await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=_launcher_menu(user_id))

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        _miniapp_entry_text(
            terms_accepted=has_accepted_terms(q.from_user.id),
            is_admin=(q.from_user.id == ADMIN_TELEGRAM_ID),
        ),
        reply_markup=_launcher_menu(q.from_user.id),
        parse_mode="Markdown",
    )

# ============================================================
# ROUTER
# ============================================================

routes = {
    "dashboard": dashboard,
    "wallet_menu": wallet_menu,
    "set_wallet": set_wallet,
    "set_pk": set_pk,
    "activate": activate,
    "deactivate": deactivate,
    "operations": operations,
    "referrals": referrals,
    "activate_plan": activate_plan,
    "admin_visual": admin_visual,
    "admin_stats": admin_stats,
    "admin_stats_24h": admin_stats_24h,
    "admin_stats_7d": admin_stats_7d,
    "admin_stats_30d": admin_stats_30d,
    "admin_stats_reset_confirm": admin_stats_reset_confirm,
    "admin_stats_reset_do": admin_stats_reset_do,
    "admin_user_stats_start": admin_user_stats_start,
    "admin_user_stats_24h": admin_user_stats_24h,
    "admin_user_stats_7d": admin_user_stats_7d,
    "admin_user_stats_30d": admin_user_stats_30d,
    "admin_user_stats_reset_confirm": admin_user_stats_reset_confirm,
    "admin_user_stats_reset_do": admin_user_stats_reset_do,
    "admin_panel": admin_panel,
    "info": info,
    "policies": policies,
    "policies_accept": policies_accept,
    "back": back_to_main,
}


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    handler = routes.get(data)
    if handler:
        await handler(update, context)


# ============================================================
# MAIN
# ============================================================

async def bot_runtime_heartbeat(context: ContextTypes.DEFAULT_TYPE):
    outcome = await asyncio.to_thread(publish_runtime_component,
        'telegram_bot',
        'online',
        metadata={
            'bot_username': BOT_USERNAME,
            'miniapp_url_configured': bool(MINIAPP_URL),
            'admin_telegram_id': int(ADMIN_TELEGRAM_ID or 0),
        },
        verify_readback=True,
    )
    if not outcome.get('healthy'):
        logging.error('Heartbeat telegram_bot no saludable: %s', outcome)
        await _send_runtime_issue_alert(context, 'telegram_bot', outcome)


def run_bot():

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("panel", start))
    app.add_handler(CommandHandler("miniapp", miniapp_command))
    app.add_handler(CommandHandler("strategy", strategy_status_command))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # ✅ CORRECCIÓN CRÍTICA:
    # Se pasa la MISMA Application al trading loop
    startup_runtime = publish_runtime_component(
        'telegram_bot',
        'online',
        metadata={
            'bot_username': BOT_USERNAME,
            'miniapp_url_configured': bool(MINIAPP_URL),
            'admin_telegram_id': int(ADMIN_TELEGRAM_ID or 0),
            'phase': 'startup',
        },
        verify_readback=False,
    )
    if not startup_runtime.get('ok'):
        logging.error('No se pudo publicar heartbeat startup telegram_bot: %s', startup_runtime)
    logging.info('Runtime identity bot: %s', describe_runtime_identity())

    app.job_queue.run_repeating(bot_runtime_heartbeat, interval=30, first=1)
    app.job_queue.run_once(
        lambda ctx: asyncio.create_task(trading_loop(app)),
        when=3
    )

    print("🤖 Trading X Hyper Pro – Bot ejecutándose...")
    app.run_polling()


# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    run_bot()
