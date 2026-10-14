"""Persistencia de cuentas telefónicas y desafíos de contacto Telegram.

El secreto del navegador NUNCA aparece en el deep link enviado a Telegram.
El contacto debe corresponder al propio remitente de un chat privado.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import phonenumbers
from argon2 import PasswordHasher, exceptions as argon_errors
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.database import db, users_col, create_user, set_referrer

CREDENTIALS = db['phone_credentials']
FLOWS = db['phone_auth_flows']
LIMITS = db['phone_auth_limits']
FLOW_SECONDS = 600
HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
# Dummy hash: login de un teléfono desconocido exige un trabajo equivalente.
DUMMY_HASH = '$argon2id$v=19$m=65536,t=3,p=2$gpneCOVf/+zvnzK+tD4tUQ$0v7CMUlJxZM7KhU1aRy/PAjaHaLk3Vd/vR8RNJACsvQ'


class PhoneAuthError(ValueError):
    def __init__(self, detail: str, status: int = 400):
        self.status = status
        super().__init__(detail)


def init_indexes() -> None:
    CREDENTIALS.create_index('telegram_user_id', unique=True)
    FLOWS.create_index('expires_at', expireAfterSeconds=0)
    FLOWS.create_index([('state', 1), ('expires_at', 1)])
    LIMITS.create_index('expires_at', expireAfterSeconds=0)


def normalize_phone(raw: str) -> str:
    value = (raw or '').strip()
    try:
        parsed = phonenumbers.parse(value, None)
    except phonenumbers.NumberParseException as exc:
        raise PhoneAuthError('Número internacional inválido. Revisa país y prefijo.') from exc
    if not phonenumbers.is_valid_number(parsed):
        raise PhoneAuthError('Número de teléfono inválido para el país seleccionado')
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def normalized_contact(raw: str, expected_phone: str) -> bool:
    # Telegram puede enviar el contacto sin + ni prefijo explícito.
    # Sólo usar país del teléfono pedido en el desafío, y comparar ambos E.164.
    supplied = str(raw or '').strip()
    try:
        if not supplied.startswith('+'):
            requested = phonenumbers.parse(expected_phone, None)
            region = phonenumbers.region_code_for_number(requested)
            parsed = phonenumbers.parse(supplied, region)
            actual = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        else:
            actual = normalize_phone(supplied)
    except (PhoneAuthError, phonenumbers.NumberParseException):
        return False
    return hmac.compare_digest(actual, expected_phone)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def hit_limit(key: str, max_attempts: int, window_seconds: int) -> None:
    # Ventana por clave, con hash para no guardar IP o número en claro.
    now = datetime.now(timezone.utc)
    doc_id = _digest(key)
    current = LIMITS.find_one_and_update(
        {'_id': doc_id, 'expires_at': {'$gt': now}},
        {'$inc': {'hits': 1}}, return_document=ReturnDocument.AFTER,
    )
    if current is None:
        expiry = now + timedelta(seconds=window_seconds)
        try:
            LIMITS.insert_one({'_id': doc_id, 'hits': 1, 'expires_at': expiry})
            return
        except DuplicateKeyError:
            # Un registro caducado puede seguir en Mongo hasta que el TTL lo borre.
            current = LIMITS.find_one_and_update(
                {'_id': doc_id, 'expires_at': {'$lte': now}},
                {'$set': {'hits': 1, 'expires_at': expiry}},
                return_document=ReturnDocument.AFTER,
            )
            if current is None:
                current = LIMITS.find_one_and_update(
                    {'_id': doc_id}, {'$inc': {'hits': 1}},
                    return_document=ReturnDocument.AFTER,
                )
    if current and int(current.get('hits', 0)) > max_attempts:
        raise PhoneAuthError('Demasiados intentos. Vuelve a intentarlo más tarde.', 429)


def _check_password(password: str) -> None:
    if not isinstance(password, str) or not 10 <= len(password) <= 128:
        raise PhoneAuthError('La contraseña debe tener entre 10 y 128 caracteres')


def authenticate_password_login(phone: str, password: str, source_ip: str) -> int:
    """Regreso de usuario previamente verificado: Argon2 y límites; sin Telegram nuevo."""
    _check_password(password)
    number = normalize_phone(phone)
    hit_limit('phone:' + number, 10, 900)
    hit_limit('source:' + str(source_ip), 150, 900)
    existing = CREDENTIALS.find_one({'_id': number})
    try:
        candidate = existing.get('password_hash') if existing else DUMMY_HASH
        matches = HASHER.verify(candidate, password)
        uid = int(existing['telegram_user_id']) if existing and matches and existing.get('verified_at') else 0
    except (argon_errors.VerifyMismatchError, argon_errors.VerificationError,
            argon_errors.InvalidHashError, KeyError, ValueError, TypeError):
        uid = 0
    if uid <= 0:
        raise PhoneAuthError('Teléfono o contraseña incorrectos', 401)
    return uid


def begin_flow(phone: str, password: str, mode: str, referrer: str, source_ip: str) -> tuple[str, str]:
    _check_password(password)
    number = normalize_phone(phone)
    if mode not in ('register', 'login'):
        raise PhoneAuthError('Tipo de acceso inválido')
    hit_limit('phone:' + number, 10, 900)
    hit_limit('source:' + str(source_ip), 150, 900)
    existing = CREDENTIALS.find_one({'_id': number})
    if mode == 'register' and existing:
        raise PhoneAuthError('Este número ya está registrado. Inicia sesión.', 409)
    if mode == 'login':
        try:
            password_hash = existing.get('password_hash') if existing else DUMMY_HASH
            match = HASHER.verify(password_hash, password)
            valid = bool(existing and match)
        except (argon_errors.VerifyMismatchError, argon_errors.VerificationError, argon_errors.InvalidHashError, KeyError, ValueError):
            valid = False
        if not valid:
            # Respuesta uniforme para número o contraseña incorrectos.
            raise PhoneAuthError('Teléfono o contraseña incorrectos', 401)
    flow_id = secrets.token_urlsafe(24)
    browser_secret = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    referrer_id = int(referrer) if str(referrer).isdigit() and 0 < int(referrer) < 10**15 else None
    FLOWS.insert_one({
        '_id': flow_id, 'secret_hash': _digest(browser_secret),
        'phone': number, 'password_hash': HASHER.hash(password) if mode == 'register' else None,
        'mode': mode, 'state': 'pending', 'telegram_user_id': None,
        'referrer_id': referrer_id, 'created_at': now, 'expires_at': now + timedelta(seconds=FLOW_SECONDS),
    })
    return flow_id, browser_secret


def valid_browser_flow(flow_id: str, browser_secret: str, *, verified: bool = False) -> dict:
    if not flow_id or not browser_secret or len(browser_secret) < 30:
        raise PhoneAuthError('Inicia nuevamente la verificación', 401)
    flow = FLOWS.find_one({'_id': flow_id, 'expires_at': {'$gt': datetime.now(timezone.utc)}})
    if not flow or not hmac.compare_digest(str(flow.get('secret_hash') or ''), _digest(browser_secret)):
        raise PhoneAuthError('Verificación vencida o inválida', 401)
    if verified and flow.get('state') != 'verified':
        raise PhoneAuthError('Todavía falta verificar el contacto en Telegram', 409)
    return flow


def confirm_contact(flow_id: str, telegram_user_id: int, raw_phone: str) -> str:
    now = datetime.now(timezone.utc)
    flow = FLOWS.find_one({'_id': flow_id, 'state': 'pending', 'expires_at': {'$gt': now}})
    if not flow:
        return 'expired'
    if not normalized_contact(raw_phone, str(flow['phone'])):
        return 'mismatch'
    uid = int(telegram_user_id)
    cred = CREDENTIALS.find_one({'_id': flow['phone']})
    other = CREDENTIALS.find_one({'telegram_user_id': uid})
    if (cred and int(cred['telegram_user_id']) != uid) or (other and other['_id'] != flow['phone']):
        return 'different_account'
    if flow['mode'] == 'login' and (not cred or int(cred['telegram_user_id']) != uid):
        return 'different_account'
    changed = FLOWS.update_one(
        {'_id': flow_id, 'state': 'pending', 'expires_at': {'$gt': now}},
        {'$set': {'state': 'verified', 'telegram_user_id': uid, 'verified_at': now}},
    )
    return 'ok' if changed.modified_count == 1 else 'expired'


def finish_flow(flow_id: str, browser_secret: str) -> int:
    flow = valid_browser_flow(flow_id, browser_secret, verified=True)
    uid = int(flow['telegram_user_id'])
    phone = flow['phone']
    if flow['mode'] == 'register':
        existing = CREDENTIALS.find_one({'_id': phone})
        other = CREDENTIALS.find_one({'telegram_user_id': uid})
        if existing or (other and other['_id'] != phone):
            raise PhoneAuthError('Cuenta ya vinculada a otro teléfono o Telegram', 409)
        # Preservar el registro existente asociado al Telegram ID.
        create_user(uid, f'user_{uid}')
        try:
            CREDENTIALS.insert_one({'_id': phone, 'telegram_user_id': uid,
                                    'password_hash': flow['password_hash'],
                                    'verified_at': datetime.now(timezone.utc)})
        except DuplicateKeyError as exc:
            raise PhoneAuthError('Este teléfono o Telegram ya tiene una cuenta', 409) from exc
        referrer = flow.get('referrer_id')
        if isinstance(referrer, int) and referrer != uid:
            set_referrer(uid, referrer)
    else:
        cred = CREDENTIALS.find_one({'_id': phone, 'telegram_user_id': uid})
        if not cred:
            raise PhoneAuthError('Cuenta no disponible', 401)
    consumed = FLOWS.update_one({'_id': flow_id, 'state': 'verified', 'secret_hash': _digest(browser_secret),
                                 'expires_at': {'$gt': datetime.now(timezone.utc)}},
                                {'$set': {'state': 'consumed'}, '$unset': {'password_hash': ''}})
    if consumed.modified_count != 1:
        raise PhoneAuthError('Esta verificación ya fue utilizada', 401)
    return uid
