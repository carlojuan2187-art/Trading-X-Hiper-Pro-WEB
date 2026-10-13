# ============================================================
# REFERRAL FEE JOB – Trading X Hyper Pro
# Pago semanal de REFERIDOS (Domingo 12:00 PM – Cuba)
# Protección A2 Anti-Doble-Pago
# ============================================================

from datetime import datetime
import pytz

from app.database import (
    get_referrer_weekly,
    reset_weekly_fees,
    payment_exists,
    log_fee_payment,
    users_col,
)
from app.wallet_manager import pay_referral_fee
from app.log_config import system_logger  # ✅ Logger para producción

# ============================================================
# CONFIGURACIÓN HORARIA (CUBA)
# ============================================================

CUBA_TZ = pytz.timezone("America/Havana")

# ============================================================
# HELPERS
# ============================================================

def _get_current_week_id(dt: datetime) -> str:
    """
    Devuelve identificador de semana ISO.
    Ejemplo: 2025-W03
    """
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"

def _is_sunday_12pm_exact(dt: datetime) -> bool:
    """
    Ventana exacta: Domingo 12:00 – 12:01 (Cuba)
    Evita múltiples ejecuciones.
    """
    return (
        dt.weekday() == 6 and
        dt.hour == 12 and
        dt.minute == 0
    )

# ============================================================
# JOB PRINCIPAL
# ============================================================

def run_referral_fee_job():
    """
    Ejecuta el pago semanal de REFERIDOS.
    Se ejecuta SOLO Domingo 12:00 PM (Cuba).
    """

    now_cuba = datetime.now(CUBA_TZ)

    if not _is_sunday_12pm_exact(now_cuba):
        system_logger.info("⏸ Fuera de ventana válida (Domingo 12:00 PM Cuba).")
        return

    week_id = _get_current_week_id(now_cuba)
    period_id = f"REFERRAL_{week_id}"

    system_logger.info(f"🕛 Ejecutando REFERRAL FEE JOB – Periodo {period_id}")

    # ========================================================
    # A2 – ANTI DOBLE PAGO (GLOBAL)
    # ========================================================

    if payment_exists("REFERRAL", period_id):
        system_logger.warning("⚠ Pago de REFERIDOS ya ejecutado. Abortando.")
        return

    # ========================================================
    # OBTENER REFERIDORES ACTIVOS
    # ========================================================

    referrers = users_col.find(
        {"referrer": {"$ne": None}},
        {"_id": 0, "user_id": 1, "wallet": 1}
    )

    paid_any = False

    for ref in referrers:
        referrer_id = ref.get("user_id")
        ref_wallet = ref.get("wallet")

        if not referrer_id or not ref_wallet:
            continue

        total_fee = get_referrer_weekly(referrer_id)

        if total_fee <= 0:
            continue

        # ====================================================
        # PAGO REAL ON-CHAIN
        # ====================================================

        tx_hash = pay_referral_fee(
            referrer_wallet=ref_wallet,
            amount=total_fee,
            currency="USDC"
        )

        if not tx_hash:
            system_logger.error(f"❌ Error pagando referido {referrer_id} | {total_fee} USDC")
            continue

        paid_any = True

        # ====================================================
        # REGISTRO INDIVIDUAL (AUDITORÍA)
        # ====================================================

        log_fee_payment(
            payment_type="REFERRAL",
            period_id=f"{period_id}_{referrer_id}",
            tx_hash=tx_hash,
            amount=total_fee
        )

        system_logger.info(
            f"✅ REFERIDO PAGADO | "
            f"Referrer {referrer_id} | "
            f"{total_fee} USDC | TX {tx_hash}"
        )

    # ========================================================
    # LOCK GLOBAL DEL PERÍODO (A2 REAL)
    # ========================================================

    if paid_any:
        log_fee_payment(
            payment_type="REFERRAL",
            period_id=period_id,
            tx_hash="BATCH_OK",
            amount=0
        )

        reset_weekly_fees()
        system_logger.info("🔄 Fees semanales de referidos reseteadas.")
    else:
        system_logger.info("ℹ No hubo pagos de referidos esta semana.")
