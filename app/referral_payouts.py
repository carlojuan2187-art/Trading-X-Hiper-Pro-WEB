# ============================================================
# REFERRAL PAYOUTS – Trading X Hyper Pro
# Pago semanal de referidos (PRODUCCIÓN REAL)
# Domingo 12:00 PM – Hora Cuba
# ============================================================

from datetime import datetime
import pytz
import time

from app.database import (
    referral_weekly_fees_col,
    users_col,
    fee_payments_col,
    reset_weekly_fees,
)

from app.hyperliquid_client import send_admin_payment


# ============================================================
# CONFIGURACIÓN DE TIEMPO (CUBA)
# ============================================================

CUBA_TZ = pytz.timezone("America/Havana")


def is_sunday_12pm_cuba_exact() -> bool:
    """
    Ejecuta SOLO en la ventana 12:00:00 – 12:01:00 (Cuba)
    Evita ejecuciones múltiples por cron o loop.
    """
    now = datetime.now(CUBA_TZ)
    return (
        now.weekday() == 6 and
        now.hour == 12 and
        now.minute == 0
    )


def current_week_id() -> str:
    now = datetime.now(CUBA_TZ)
    year, week, _ = now.isocalendar()
    return f"{year}-W{week}"


# ============================================================
# VALIDACIÓN DE PAGO DUPLICADO
# ============================================================

def already_paid(referrer_id: int, week_id: str) -> bool:
    try:
        return fee_payments_col.find_one({
            "type": "referral_weekly",
            "referrer_id": referrer_id,
            "week": week_id
        }) is not None
    except Exception as e:
        print(f"❌ Error validando pago previo para {referrer_id}: {e}")
        return True  # Evita reintentos inseguros


# ============================================================
# PROCESO PRINCIPAL DE PAGO SEMANAL
# ============================================================

def process_weekly_referral_payouts():
    """
    Ejecuta el pago semanal de referidos.
    - Una sola ejecución válida
    - Una transacción por referido
    - Pago REAL on-chain
    """

    if not is_sunday_12pm_cuba_exact():
        print("⏸ Fuera de ventana exacta (domingo 12:00 Cuba).")
        return

    week_id = current_week_id()
    print(f"📆 Ejecutando pagos semanales de referidos – Semana {week_id}")

    # ========================================================
    # AGRUPAR FEES POR REFERIDO
    # ========================================================

    try:
        pipeline = [
            {
                "$group": {
                    "_id": "$referrer_id",
                    "total_amount": {"$sum": "$amount"}
                }
            }
        ]
        referrers = list(referral_weekly_fees_col.aggregate(pipeline))
    except Exception as e:
        print(f"❌ Error obteniendo fees de referidos: {e}")
        return

    if not referrers:
        print("ℹ No hay fees de referidos para pagar.")
        return

    # ========================================================
    # PROCESAR CADA REFERIDO
    # ========================================================

    for ref in referrers:
        referrer_id = ref["_id"]
        amount = round(ref["total_amount"], 6)

        if amount <= 0:
            continue

        try:
            user = users_col.find_one({"user_id": referrer_id})
        except Exception as e:
            print(f"❌ Error leyendo usuario {referrer_id}: {e}")
            continue

        if not user or not user.get("wallet"):
            print(f"⚠ Referido {referrer_id} sin wallet. Omitido.")
            continue

        if already_paid(referrer_id, week_id):
            print(f"🔁 Referido {referrer_id} ya fue pagado esta semana.")
            continue

        wallet = user["wallet"]

        try:
            # ====================================================
            # PAGO REAL ON-CHAIN
            # ====================================================

            tx_hash = send_admin_payment(
                to_wallet=wallet,
                amount=amount
            )

            if not tx_hash:
                raise Exception("TX_HASH inválido – pago no confirmado")

            # ====================================================
            # REGISTRO CONTABLE (AUDITORÍA)
            # ====================================================

            fee_payments_col.insert_one({
                "type": "referral_weekly",
                "referrer_id": referrer_id,
                "amount": amount,
                "week": week_id,
                "tx_hash": tx_hash,
                "date": datetime.utcnow()
            })

            print(f"✅ Pagado referido {referrer_id} → {amount} USDC")

            # Pausa mínima para no saturar servidor / exchange
            time.sleep(0.4)

        except Exception as e:
            print(f"❌ Error pagando referido {referrer_id}: {e}")

    # ========================================================
    # RESET SEMANAL (SOLO DESPUÉS DEL PROCESO)
    # ========================================================

    try:
        reset_weekly_fees()
        print("♻ Fees semanales de referidos reiniciadas.")
    except Exception as e:
        print(f"❌ Error reseteando fees semanales: {e}")


# ============================================================
# ENTRYPOINT MANUAL / CRON / JOB
# ============================================================

if __name__ == "__main__":
    try:
        process_weekly_referral_payouts()
    except Exception as e:
        print(f"❌ Error ejecutando el job semanal de referidos: {e}")
