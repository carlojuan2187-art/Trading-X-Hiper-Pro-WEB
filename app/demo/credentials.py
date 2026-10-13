"""Requisito de cuenta: credenciales configuradas antes de iniciar DEMO.

Nunca importa la clave privada ni la descifra. No comprueba permisos LIVE,
plan o suscripción: el saldo DEMO es completamente independiente.
"""
from __future__ import annotations


def credential_ready(profile: dict | None) -> bool:
    if not isinstance(profile, dict):
        return False
    return (
        profile.get('wallet_configured') is True
        and profile.get('private_key_configured') is True
        and not profile.get('credential_repair_required', False)
        and str(profile.get('private_key_health') or '').lower() not in {'invalid', 'decrypt_error', 'unsupported_version'}
        and str(profile.get('private_key_storage') or '').lower() == 'encrypted'
    )


def user_ready(user_id: int) -> bool:
    # get_user_profile solo expone banderas, nunca material secreto.
    from app.services.user_service import get_user_profile
    return credential_ready(get_user_profile(int(user_id)))
