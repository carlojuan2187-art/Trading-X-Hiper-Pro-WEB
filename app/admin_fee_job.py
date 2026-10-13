# ============================================================
# ADMIN FEE JOB – Trading X Hyper Pro
# Pago diario ADMIN (12:00 AM) – A2 Anti-Doble-Pago
# ============================================================

from datetime import datetime
import pytz

from app.database import (
    get_admin_daily_fees,
    reset_daily_fees,
    payment_exists,
    log_fee_payment,
)

from app.wallet_manager import pay_admin_fee


# ============================================================
# CONFIGURACIÓN HORARIA (CUBA)
# ============================================================

CUBA_TZ = pytz.timezone("America/Havana")

# ✅ Ventana segura (en minutos) para no depender del segundo exacto
MIDNIGHT_WINDOW_MINUTES = 5


# ============================================================
# HELPERS
# ============================================================

def _now_cuba() -> datetime:
    return datetime.now(CUBA_TZ)


def _is_midnight_cuba_window(dt: datetime) -> bool:
    """
    Ventana segura de ejecución:
    12:00 AM (00:00) hora Cuba dentro de un margen razonable.
    - Permite 00:00 a 00:05 (por defecto) para tolerar delays normales de cron/server.
    """
    return dt.hour == 0 and 0 <= dt.minute <= int(MIDNIGHT_WINDOW_MINUTES)


# ============================================================
# JOB PRINCIPAL
# ============================================================

def run_admin_fee_job():
    """
    Ejecuta el pago diario del ADMIN con protección A2.
    Se debe ejecutar en la ventana de 12:00 AM (Cuba).
    """

    now_cuba = _now_cuba()

    # ========================================================
    # VALIDACIÓN HORARIA (VENTANA SEGURA)
    # ========================================================

    if not _is_midnight_cuba_window(now_cuba):
        print("⏸ No es ventana de 12:00 AM (Cuba). Job ADMIN no ejecutado.")
        return

    period_id = f"ADMIN_{now_cuba.strftime('%Y-%m-%d')}"

    print(f"🕛 Ejecutando ADMIN FEE JOB – Periodo {period_id}")

    # ========================================================
    # A2 – ANTI DOBLE PAGO
    # ========================================================

    if payment_exists("ADMIN", period_id):
        print("⚠ Pago ADMIN ya ejecutado. Abortando.")
        return

    # ========================================================
    # OBTENER TOTAL ACUMULADO DEL DÍA
    # ========================================================

    total_fee = get_admin_daily_fees()

    if total_fee <= 0:
        print("ℹ No hay fee ADMIN para pagar hoy.")
        return

    # ========================================================
    # PAGO REAL ON-CHAIN
    # ========================================================

    tx_hash = pay_admin_fee(total_fee, currency="USDC")

    if not tx_hash:
        print("⚠ No se ejecutó pago (tx_hash vacío).")
        return

    # ========================================================
    # REGISTRO A2 (AUDITORÍA)
    # ========================================================

    log_fee_payment(
        payment_type="ADMIN",
        period_id=period_id,
        tx_hash=tx_hash,
        amount=total_fee
    )

    # ========================================================
    # RESET DIARIO
    # ========================================================

    reset_daily_fees()

    print(f"✅ ADMIN FEE PAGADO | {total_fee} USDC | TX {tx_hash}")
