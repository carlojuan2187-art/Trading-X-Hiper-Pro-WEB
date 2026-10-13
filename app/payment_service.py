from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, Optional
from uuid import uuid4

from pymongo import ReturnDocument

from app import config as app_config
from app.bep20_verifier import VerificationConfigError, verify_payment
from app.config import (
    get_payment_configuration_status,
    get_payment_network,
    get_payment_order_ttl_minutes,
    get_payment_receiver_address,
    get_payment_token_contract,
    get_payment_token_symbol,
)
from app.database import (
    apply_payment_premium_purchase,
    cancel_open_payment_orders_for_user,
    get_active_payment_order_for_user,
    get_payment_order_by_id,
    payment_orders_col,
    payment_verification_logs_col,
    subscription_events_col,
    log_user_activity,
)

logger = logging.getLogger(__name__)

OPEN_ORDER_STATUSES = {"awaiting_payment", "verification_in_progress", "paid_unconfirmed"}
VERIFYABLE_ORDER_STATUSES = {"awaiting_payment", "paid_unconfirmed"}
VERIFICATION_LOCK_STALE_AFTER = timedelta(minutes=3)
ORDER_REUSE_MIN_REMAINING = timedelta(minutes=5)
_DECIMAL_QUANT = Decimal("0.001")
PREMIUM_PLAN = "premium"
PREMIUM_PRICE_TABLE = {15: 5.0, 30: 10.0}


def utcnow() -> datetime:
    return datetime.utcnow()


def _quantize_amount(value: Decimal) -> Decimal:
    return value.quantize(_DECIMAL_QUANT, rounding=ROUND_DOWN)


def format_payment_amount(value: Decimal | float | str) -> str:
    return f"{_quantize_amount(Decimal(str(value))):.3f}"


def _validate_premium_days(days: int) -> int:
    try:
        value = int(days)
    except Exception as exc:
        raise ValueError("Duración inválida para la suscripción premium") from exc
    if value not in PREMIUM_PRICE_TABLE:
        raise ValueError("Duración inválida para la suscripción premium")
    return value


def get_premium_price(days: int) -> float:
    return float(PREMIUM_PRICE_TABLE[_validate_premium_days(days)])


def get_payment_catalog() -> dict:
    return {
        "premium": [
            {"plan": PREMIUM_PLAN, "days": days, "price_usdt": price}
            for days, price in sorted(PREMIUM_PRICE_TABLE.items())
        ]
    }


def build_unique_amount_candidates(base_price: float, user_id: int, *, limit: int = 999) -> list[Decimal]:
    try:
        base = Decimal(str(base_price))
    except Exception as exc:
        raise ValueError("Precio base inválido") from exc
    if base <= 0:
        raise ValueError("Precio base inválido")
    user_id = int(user_id)
    if user_id <= 0:
        raise ValueError("user_id inválido")

    get_unique_max_delta = getattr(app_config, "get_payment_unique_max_delta", None)
    try:
        configured_max_delta = get_unique_max_delta() if callable(get_unique_max_delta) else 0.150
    except Exception:
        configured_max_delta = 0.150
    max_delta = Decimal(str(configured_max_delta))
    max_suffix = int((max_delta * Decimal("1000")).to_integral_value(rounding=ROUND_DOWN))
    max_suffix = max(1, min(max_suffix, 150))
    max_candidates = max(1, min(int(limit), max_suffix))
    start_suffix = user_id % max_suffix
    if start_suffix <= 0:
        start_suffix = 1
    candidates: list[Decimal] = []
    for offset in range(0, max_candidates):
        suffix_int = ((start_suffix + offset - 1) % max_suffix) + 1
        candidates.append(_quantize_amount(base + (Decimal(suffix_int) / Decimal("1000"))))
    return candidates


def _seconds_until(value: Optional[datetime], *, now: Optional[datetime] = None) -> Optional[int]:
    if not isinstance(value, datetime):
        return None
    remaining = int((value - (now or utcnow())).total_seconds())
    return max(0, remaining)


def _current_payment_configuration() -> Dict[str, str]:
    return {
        "network": str(get_payment_network() or "").strip().lower(),
        "token_symbol": str(get_payment_token_symbol() or "").strip().upper(),
        "token_contract": str(get_payment_token_contract() or "").strip().lower(),
        "deposit_address": str(get_payment_receiver_address() or "").strip().lower(),
    }


def _existing_order_matches_current_payment_configuration(order: Optional[Dict[str, Any]]) -> bool:
    if not order:
        return False
    current = _current_payment_configuration()
    return (
        str(order.get("network") or "").strip().lower() == current["network"]
        and str(order.get("token_symbol") or "").strip().upper() == current["token_symbol"]
        and str(order.get("token_contract") or "").strip().lower() == current["token_contract"]
        and str(order.get("deposit_address") or "").strip().lower() == current["deposit_address"]
    )


def _existing_order_reissue_reason(order: Optional[Dict[str, Any]], *, now: Optional[datetime] = None) -> Optional[str]:
    if not order:
        return None
    status = str(order.get("status") or "")
    if status != "awaiting_payment":
        return None
    expires_in_seconds = _seconds_until(order.get("expires_at"), now=now)
    if expires_in_seconds is not None and expires_in_seconds < int(ORDER_REUSE_MIN_REMAINING.total_seconds()):
        return "reissued_for_short_ttl"
    if not _existing_order_matches_current_payment_configuration(order):
        return "reissued_for_payment_config_change"
    return None


def _existing_order_blocks_replacement(order: Optional[Dict[str, Any]]) -> bool:
    if not order:
        return False
    status = str(order.get("status") or "")
    if status == "verification_in_progress":
        return True
    if status == "paid_unconfirmed":
        return bool(order.get("matched_tx_hash")) or int(order.get("confirmations") or 0) > 0
    return False


def _next_unique_amount(base_price: float, user_id: int) -> Decimal:
    for amount in build_unique_amount_candidates(base_price, user_id):
        exists = payment_orders_col.find_one({
            "amount_usdt": float(amount),
            "status": {"$in": list(OPEN_ORDER_STATUSES)},
        })
        if not exists:
            return amount
    raise RuntimeError("No se pudo generar un monto único de pago dentro del rango permitido")


def _is_order_expired(order: Optional[Dict[str, Any]], *, now: Optional[datetime] = None) -> bool:
    if not order:
        return False
    expires_at = order.get("expires_at")
    if not isinstance(expires_at, datetime):
        return False
    return expires_at < (now or utcnow())


def _mark_order_status(order_id: str, status: str, *, reason: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> None:
    payload: Dict[str, Any] = {
        "status": status,
        "updated_at": utcnow(),
        "verification_lock_token": None,
        "verification_started_at": None,
    }
    if reason is not None:
        payload["last_verification_reason"] = reason
    if extra:
        payload.update(extra)
    payment_orders_col.update_one({"order_id": str(order_id)}, {"$set": payload})


def _new_payment_order(*, order_id: str, user_id: int, days: int, base_price_usdt: float, amount_usdt: float, network: str, token_symbol: str, token_contract: str, deposit_address: str, expires_at: datetime) -> Dict[str, Any]:
    now = utcnow()
    return {
        "order_id": str(order_id),
        "user_id": int(user_id),
        "plan": PREMIUM_PLAN,
        "days": int(days),
        "base_price_usdt": float(base_price_usdt),
        "amount_usdt": float(amount_usdt),
        "network": str(network),
        "token_symbol": str(token_symbol),
        "token_contract": str(token_contract).lower(),
        "deposit_address": str(deposit_address).lower(),
        "status": "awaiting_payment",
        "verification_attempts": 0,
        "verification_started_at": None,
        "verification_lock_token": None,
        "last_verification_reason": None,
        "matched_from": None,
        "matched_to": None,
        "matched_amount": None,
        "confirmations": 0,
        "confirmed_at": None,
        "expires_at": expires_at,
        "created_at": now,
        "updated_at": now,
    }


def _new_payment_verification_log(*, order_id: str, user_id: int, status: str, reason: str, tx_hash: Optional[str] = None, from_address: Optional[str] = None, to_address: Optional[str] = None, amount_usdt: Optional[float] = None, confirmations: Optional[int] = None, raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    now = utcnow()
    return {
        "order_id": str(order_id),
        "user_id": int(user_id),
        "status": str(status),
        "reason": str(reason),
        "tx_hash": tx_hash,
        "from_address": (from_address or "").lower() or None,
        "to_address": (to_address or "").lower() or None,
        "amount_usdt": float(amount_usdt) if amount_usdt is not None else None,
        "confirmations": int(confirmations) if confirmations is not None else None,
        "raw": raw or {},
        "created_at": now,
        "updated_at": now,
    }


def _load_effective_active_order(user_id: int) -> Optional[Dict[str, Any]]:
    order = get_active_payment_order_for_user(int(user_id))
    if order and _is_order_expired(order):
        _mark_order_status(str(order.get("order_id")), "expired", reason="order_expired")
        return None
    return order


def _payment_purchase_already_applied(order_id: str, user_id: int) -> bool:
    try:
        existing = subscription_events_col.find_one({
            "user_id": int(user_id),
            "event_type": "purchase",
            "source": "payment_bep20",
            "order_id": str(order_id),
        })
        return bool(existing)
    except Exception:
        return False


def _finalize_completed_order_if_needed(order_id: str, *, tx_hash: Optional[str] = None, verification: Optional[Dict[str, Any]] = None, reason: str = "payment_confirmed") -> Optional[Dict[str, Any]]:
    verification = verification or {}
    now = utcnow()
    update_doc = {
        "status": "completed",
        "last_verification_reason": reason,
        "matched_tx_hash": tx_hash or verification.get("tx_hash"),
        "matched_from": verification.get("from_address"),
        "matched_to": verification.get("to_address"),
        "matched_amount": verification.get("amount_usdt"),
        "confirmations": int(verification.get("confirmations") or 0),
        "confirmed_at": now,
        "verification_lock_token": None,
        "verification_started_at": None,
        "updated_at": now,
    }
    return payment_orders_col.find_one_and_update(
        {"order_id": str(order_id), "status": {"$ne": "completed"}},
        {"$set": update_doc},
        return_document=ReturnDocument.AFTER,
    ) or get_payment_order_by_id(str(order_id))


def _acquire_verification_lock(order_id: str, user_id: int) -> tuple[Optional[Dict[str, Any]], str]:
    now = utcnow()
    lock_token = uuid4().hex[:16]
    stale_before = now - VERIFICATION_LOCK_STALE_AFTER
    order = payment_orders_col.find_one_and_update(
        {
            "order_id": str(order_id),
            "user_id": int(user_id),
            "$or": [
                {"status": {"$in": list(VERIFYABLE_ORDER_STATUSES)}},
                {"status": "verification_in_progress", "verification_started_at": {"$lt": stale_before}},
                {"status": "verification_in_progress", "verification_started_at": {"$exists": False}},
            ],
        },
        {
            "$set": {
                "status": "verification_in_progress",
                "verification_started_at": now,
                "verification_lock_token": lock_token,
                "updated_at": now,
            },
            "$inc": {"verification_attempts": 1},
        },
        return_document=ReturnDocument.AFTER,
    )
    return order, lock_token


def _update_locked_order(order_id: str, lock_token: str, values: Dict[str, Any]) -> None:
    payload = dict(values)
    payload.setdefault("updated_at", utcnow())
    payload.setdefault("verification_lock_token", None)
    payload.setdefault("verification_started_at", None)
    payment_orders_col.update_one(
        {"order_id": str(order_id), "verification_lock_token": lock_token},
        {"$set": payload},
    )


def create_payment_order(user_id: int, days: int, plan: str = PREMIUM_PLAN) -> Dict[str, Any]:
    user_id = int(user_id)
    if user_id <= 0:
        raise ValueError("user_id inválido")
    if str(plan or PREMIUM_PLAN).strip().lower() != PREMIUM_PLAN:
        raise ValueError("Solo existe suscripción premium en este bot")

    payment_config = get_payment_configuration_status()
    if not payment_config.get("ready"):
        missing = ", ".join(str(item) for item in (payment_config.get("missing_keys") or []) if item)
        raise RuntimeError(f"Configuración de pagos incompleta: {missing or 'payment_config_missing'}")

    days = _validate_premium_days(days)
    base_price = get_premium_price(days)

    existing_open = _load_effective_active_order(user_id)
    if existing_open:
        existing_days = int(existing_open.get("days") or 0)
        if existing_days == days:
            reissue_reason = _existing_order_reissue_reason(existing_open)
            if reissue_reason:
                cancel_open_payment_orders_for_user(user_id, reason=reissue_reason)
            else:
                return existing_open
        else:
            if _existing_order_blocks_replacement(existing_open):
                return existing_open
            cancel_open_payment_orders_for_user(user_id, reason="superseded_by_new_order")

    amount = _next_unique_amount(base_price, user_id)
    now = utcnow()
    order = _new_payment_order(
        order_id=uuid4().hex[:12],
        user_id=user_id,
        days=days,
        base_price_usdt=float(base_price),
        amount_usdt=float(amount),
        network=get_payment_network(),
        token_symbol=get_payment_token_symbol(),
        token_contract=get_payment_token_contract(),
        deposit_address=get_payment_receiver_address(),
        expires_at=now + timedelta(minutes=get_payment_order_ttl_minutes()),
    )
    payment_orders_col.insert_one(order)
    log_user_activity(user_id, 'Orden de pago generada', f'Se generó una orden premium por {days} día(s) por {format_payment_amount(amount)} USDT.', tone='info', event_type='payment_order_created', metadata={'order_id': order['order_id'], 'days': days, 'amount_usdt': float(amount)})
    return order


def get_payment_order(order_id: str, *, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    return get_payment_order_by_id(str(order_id), user_id=user_id)


def cancel_payment_order(order_id: str, user_id: int) -> bool:
    result = payment_orders_col.update_one(
        {"order_id": str(order_id), "user_id": int(user_id), "status": {"$in": list(OPEN_ORDER_STATUSES)}},
        {"$set": {"status": "cancelled", "last_verification_reason": "cancelled_by_user", "updated_at": utcnow()}},
    )
    cancelled = bool(result.modified_count)
    if cancelled:
        log_user_activity(int(user_id), 'Orden de pago cancelada', 'La orden abierta fue cancelada manualmente desde la MiniApp.', tone='warning', event_type='payment_order_cancelled', metadata={'order_id': str(order_id)})
    return cancelled


def _write_verification_log(order: Dict[str, Any], verification: Dict[str, Any]) -> None:
    doc = _new_payment_verification_log(
        order_id=order["order_id"],
        user_id=order["user_id"],
        status=verification.get("status") or "unknown",
        reason=verification.get("reason") or "unknown",
        tx_hash=verification.get("tx_hash"),
        from_address=verification.get("from_address"),
        to_address=verification.get("to_address"),
        amount_usdt=verification.get("amount_usdt"),
        confirmations=verification.get("confirmations"),
        raw=verification,
    )
    payment_verification_logs_col.insert_one(doc)


def expire_stale_payment_orders() -> int:
    now = utcnow()
    result = payment_orders_col.update_many(
        {"status": {"$in": list(OPEN_ORDER_STATUSES)}, "expires_at": {"$lt": now}},
        {"$set": {"status": "expired", "last_verification_reason": "order_expired", "updated_at": now}},
    )
    return int(result.modified_count or 0)


def confirm_payment_order(order_id: str, user_id: int) -> Dict[str, Any]:
    order = get_payment_order(order_id, user_id=user_id)
    if not order:
        return {"ok": False, "reason": "order_not_found"}

    if order.get("status") == "completed":
        return {"ok": True, "reason": "already_completed", "order": order}
    if order.get("status") == "cancelled":
        return {"ok": False, "reason": "order_cancelled", "order": order}
    if _is_order_expired(order):
        _mark_order_status(order_id, "expired", reason="order_expired")
        return {"ok": False, "reason": "order_expired", "order": get_payment_order(order_id, user_id=user_id)}
    if _payment_purchase_already_applied(order_id, user_id):
        finalized = _finalize_completed_order_if_needed(order_id, reason="activation_already_applied")
        return {"ok": True, "reason": "already_completed", "order": finalized or get_payment_order(order_id, user_id=user_id)}

    order, lock_token = _acquire_verification_lock(order_id, user_id)
    if not order:
        current = get_payment_order(order_id, user_id=user_id)
        if current and current.get("status") == "completed":
            return {"ok": True, "reason": "already_completed", "order": current}
        if current and current.get("status") == "verification_in_progress":
            retry_after_seconds = _seconds_until((current.get("verification_started_at") or utcnow()) + VERIFICATION_LOCK_STALE_AFTER)
            return {"ok": False, "reason": "verification_in_progress", "order": current, "retry_after_seconds": retry_after_seconds}
        if current and current.get("status") == "cancelled":
            return {"ok": False, "reason": "order_cancelled", "order": current}
        if current and _is_order_expired(current):
            _mark_order_status(order_id, "expired", reason="order_expired")
            return {"ok": False, "reason": "order_expired", "order": get_payment_order(order_id, user_id=user_id)}
        return {"ok": False, "reason": "order_not_available", "order": current}

    try:
        verification = verify_payment(order)
    except VerificationConfigError as exc:
        _update_locked_order(order_id, lock_token, {"status": "awaiting_payment", "last_verification_reason": "payment_config_missing"})
        return {"ok": False, "reason": "payment_config_missing", "message": str(exc)}
    except Exception as exc:
        logger.error("Error verificando pago %s: %s", order_id, exc, exc_info=True)
        _update_locked_order(order_id, lock_token, {"status": "awaiting_payment", "last_verification_reason": "verification_error"})
        return {"ok": False, "reason": "verification_error", "message": str(exc)}

    try:
        _write_verification_log(order, verification)
    except Exception as exc:
        logger.warning('No se pudo registrar el log de verificación para %s: %s', order_id, exc, exc_info=True)


    status = verification.get("status")
    if status == "not_found":
        _update_locked_order(order_id, lock_token, {"status": "awaiting_payment", "last_verification_reason": verification.get("reason")})
        return {"ok": False, "reason": verification.get("reason"), "order": get_payment_order(order_id, user_id=user_id)}

    if status == "unconfirmed":
        _update_locked_order(order_id, lock_token, {
            "status": "paid_unconfirmed",
            "last_verification_reason": verification.get("reason"),
            "matched_tx_hash": verification.get("tx_hash"),
            "matched_from": verification.get("from_address"),
            "matched_to": verification.get("to_address"),
            "matched_amount": verification.get("amount_usdt"),
            "confirmations": int(verification.get("confirmations") or 0),
        })
        return {"ok": False, "reason": verification.get("reason"), "order": get_payment_order(order_id, user_id=user_id)}

    tx_hash = verification.get("tx_hash")
    duplicate = payment_orders_col.find_one({
        "matched_tx_hash": tx_hash,
        "order_id": {"$ne": order_id},
        "status": {"$in": ["verification_in_progress", "paid_unconfirmed", "completed"]},
    })
    if duplicate:
        _update_locked_order(order_id, lock_token, {"status": "awaiting_payment", "last_verification_reason": "tx_already_used"})
        return {"ok": False, "reason": "tx_already_used", "order": get_payment_order(order_id, user_id=user_id)}

    if _payment_purchase_already_applied(order_id, user_id):
        finalized = _finalize_completed_order_if_needed(order_id, tx_hash=tx_hash, verification=verification, reason="activation_already_applied")
        return {"ok": True, "reason": "already_completed", "order": finalized, "verification": verification}

    try:
        activation = apply_payment_premium_purchase(
            user_id=user_id,
            days=int(order["days"]),
            order_id=str(order_id),
            tx_hash=tx_hash,
            amount_usdt=float(order["amount_usdt"]),
            metadata={
                "network": order.get("network"),
                "token_symbol": order.get("token_symbol"),
                "base_price_usdt": float(order["base_price_usdt"]),
            },
        )
    except Exception as exc:
        logger.error('Error aplicando la compra premium para order_id=%s: %s', order_id, exc, exc_info=True)
        _update_locked_order(order_id, lock_token, {"status": "awaiting_payment", "last_verification_reason": "activation_exception"})
        return {"ok": False, "reason": "activation_exception", "message": 'No se pudo aplicar la activación premium'}

    if not activation.get("ok"):
        _update_locked_order(order_id, lock_token, {"status": "awaiting_payment", "last_verification_reason": "activation_failed"})
        return {"ok": False, "reason": "activation_failed", "message": activation.get("message")}

    try:
        updated = _finalize_completed_order_if_needed(order_id, tx_hash=tx_hash, verification=verification, reason=verification.get("reason") or "payment_confirmed")
    except Exception as exc:
        logger.error('Error finalizando la orden %s: %s', order_id, exc, exc_info=True)
        _update_locked_order(order_id, lock_token, {"status": "paid_unconfirmed", "last_verification_reason": "finalize_exception", "matched_tx_hash": tx_hash})
        return {"ok": False, "reason": "finalize_exception", "message": 'El pago fue detectado, pero la orden no pudo cerrarse todavía'}

    return {"ok": True, "reason": "payment_confirmed", "order": updated, "verification": verification, "activation": activation}
