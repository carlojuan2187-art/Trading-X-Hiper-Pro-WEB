# ============================================================
# FEE SETTLEMENT – Trading X Hyper Pro
# Liquidación REAL del ADMIN (DIARIA – 12:00 AM HORA CUBA)
# ============================================================

import asyncio
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


# ============================================================
# HELPERS
# ============================================================

def _now_cuba() -> datetime:
    return datetime.now(CUBA_TZ)


def _get_cycle_id(dt: datetime) -> str:
    """ID diario A2"""
    return f"ADMIN_{dt.strftime('%Y-%m-%d')}"


def _is_midnight_cuba(dt: datetime) -> bool:
    """Ventana exacta 12:00 – 12:01 AM"""
    return dt.hour == 0 and dt.minute == 0


# ============================================================
# LIQUIDACIÓN DIARIA DEL ADMIN
# ============================================================

async def settle_admin_daily():
    """
    Liquida TODA la fee diaria del admin
    en UNA sola transacción.
    """

    now = _now_cuba()
    cycle_id = _get_cycle_id(now)

    # ========================================================
    # A2 – ANTI DOBLE PAGO
    # ========================================================

    if payment_exists("ADMIN", cycle_id):
        print("⚠ Fee ADMIN ya liquidada hoy. Abortando.")
        return

    total = get_admin_daily_fees()

    if total <= 0:
        print("ℹ No hay fee diaria del admin para liquidar.")
        return

    print(f"🏦 Liquidando fee diaria ADMIN: {total} USDC")

    # ========================================================
    # PAGO REAL ON-CHAIN (VÍA WALLET MANAGER)
    # ========================================================

    tx_hash = pay_admin_fee(total, currency="USDC")

    if not tx_hash:
        print("❌ No se ejecutó el pago ADMIN.")
        return

    # ========================================================
    # REGISTRO A2 (AUDITORÍA)
    # ========================================================

    log_fee_payment(
        payment_type="ADMIN",
        period_id=cycle_id,
        tx_hash=tx_hash,
        amount=total
    )

    reset_daily_fees()

    print(f"✅ Fee ADMIN liquidada | {total} USDC | TX {tx_hash}")


# ============================================================
# LOOP PRINCIPAL
# ============================================================

async def run_settlement_loop():
    """
    Loop independiente.
    Revisa el reloj y liquida a las 12:00 AM Cuba.
    """

    print("🔁 Fee Settlement (ADMIN) iniciado – 12:00 AM Cuba")

    while True:
        try:
            now = _now_cuba()

            if _is_midnight_cuba(now):
                await settle_admin_daily()
                await asyncio.sleep(65)  # evita doble ejecución exacta

        except Exception as e:
            print("❌ Error en fee_settlement:", e)

        await asyncio.sleep(30)


# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    asyncio.run(run_settlement_loop())
