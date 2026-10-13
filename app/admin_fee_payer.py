# ============================================================
# ADMIN FEE PAYER – Trading X Hyper Pro
# Pago diario del administrador (12:00 AM Cuba)
# ============================================================

from datetime import datetime, timedelta
import pytz

from app.database import (
    get_unpaid_admin_fees,
    mark_admin_fee_paid,
)
from app.hyperliquid_client import transfer_admin_fee


# ============================================================
# ZONA HORARIA CUBA
# ============================================================

CUBA_TZ = pytz.timezone("America/Havana")


def get_daily_cycle_id():
    """
    Retorna el ciclo diario en formato YYYY-MM-DD
    basado en hora de Cuba.
    """
    now_cuba = datetime.now(CUBA_TZ)
    return now_cuba.strftime("%Y-%m-%d")


# ============================================================
# PAGO DIARIO ADMIN
# ============================================================

def pay_admin_daily_fee():
    """
    Paga TODA la fee acumulada del día en UNA sola transacción.
    Idempotente: si ya se pagó, no hace nada.
    """

    cycle_id = get_daily_cycle_id()

    unpaid_fees = get_unpaid_admin_fees(cycle_id)

    if not unpaid_fees:
        print(f"✅ Admin fee ya pagada o no existe para {cycle_id}")
        return

    total_amount = round(sum(f["amount"] for f in unpaid_fees), 6)

    if total_amount <= 0:
        print("⚠ Monto admin fee inválido.")
        return

    # ========================================================
    # TRANSFERENCIA REAL ON-CHAIN (UNA SOLA)
    # ========================================================

    tx_hash = transfer_admin_fee(total_amount)

    if not tx_hash:
        print("❌ Error pagando admin fee.")
        return

    # ========================================================
    # MARCAR COMO PAGADO (A PRUEBA DE REINICIOS)
    # ========================================================

    mark_admin_fee_paid(cycle_id, tx_hash)

    print(
        f"💰 Admin fee pagada correctamente | "
        f"Ciclo: {cycle_id} | "
        f"Monto: {total_amount} | "
        f"TX: {tx_hash}"
    )
