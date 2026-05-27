"""Autenticación LDAP para la interfaz web.

Usa la librería ldap3 para:
1. Buscar al usuario por su email en el LDAP
2. Intentar bind con sus credenciales
3. Verificar pertenencia a grupo de admins (opcional)
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class LDAPConfig:
    host: str = "ldap://localhost"
    port: int = 389
    use_tls: bool = False
    bind_dn: str = ""           # DN de cuenta de servicio para búsquedas
    bind_password: str = ""
    base_dn: str = ""
    user_filter: str = "(mail={username})"
    group_admin_dn: str = ""    # DN del grupo de admins (vacío = sin restricción)

    @classmethod
    def from_env(cls) -> "LDAPConfig":
        return cls(
            host=os.environ.get("LDAP_HOST", "ldap://localhost"),
            port=int(os.environ.get("LDAP_PORT", 389)),
            use_tls=os.environ.get("LDAP_USE_TLS", "false").lower() == "true",
            bind_dn=os.environ.get("LDAP_BIND_DN", ""),
            bind_password=os.environ.get("LDAP_BIND_PASSWORD", ""),
            base_dn=os.environ.get("LDAP_BASE_DN", ""),
            user_filter=os.environ.get("LDAP_USER_FILTER", "(mail={username})"),
            group_admin_dn=os.environ.get("LDAP_GROUP_ADMIN", ""),
        )

    @classmethod
    def from_web_config(cls, cfg: dict) -> "LDAPConfig":
        ld = cfg.get("ldap", {})
        return cls(
            host=ld.get("host") or os.environ.get("LDAP_HOST", "ldap://localhost"),
            port=int(ld.get("port") or os.environ.get("LDAP_PORT", 389)),
            use_tls=bool(ld.get("use_tls")) if ld.get("use_tls") is not None else os.environ.get("LDAP_USE_TLS", "false").lower() == "true",
            bind_dn=ld.get("bind_dn") or os.environ.get("LDAP_BIND_DN", ""),
            bind_password=ld.get("bind_password") or os.environ.get("LDAP_BIND_PASSWORD", ""),
            base_dn=ld.get("base_dn") or os.environ.get("LDAP_BASE_DN", ""),
            user_filter=ld.get("user_filter") or os.environ.get("LDAP_USER_FILTER", "(mail={username})"),
            group_admin_dn=ld.get("group_admin_dn") or os.environ.get("LDAP_GROUP_ADMIN", ""),
        )


@dataclass
class AuthUser:
    email: str
    display_name: str
    is_admin: bool = False
    ldap_dn: str = ""


def authenticate(email: str, password: str, ldap_cfg: LDAPConfig) -> Tuple[bool, Optional[AuthUser]]:
    """Autentica un usuario via LDAP.

    Flujo:
    1. Conectar al LDAP como cuenta de servicio (bind_dn)
    2. Buscar el DN del usuario por su email
    3. Hacer bind con las credenciales del usuario
    4. Verificar pertenencia a grupo de admins (si está configurado)

    Returns:
        (success: bool, user: AuthUser | None)
    """
    try:
        from ldap3 import Server, Connection, SIMPLE, SUBTREE, ALL_ATTRIBUTES, Tls
        import ssl
    except ImportError:
        logger.error("ldap3 no instalado. pip install ldap3")
        return False, None

    if not email or not password:
        return False, None

    try:
        # Construir servidor
        tls = None
        if ldap_cfg.use_tls:
            tls = Tls(validate=ssl.CERT_NONE)  # En prod usar CERT_REQUIRED

        server = Server(
            ldap_cfg.host,
            port=ldap_cfg.port,
            use_ssl=ldap_cfg.use_tls,
            tls=tls,
            connect_timeout=5,
        )

        # === Paso 1: Bind como cuenta de servicio ===
        svc_conn = Connection(
            server,
            user=ldap_cfg.bind_dn,
            password=ldap_cfg.bind_password,
            authentication=SIMPLE,
            auto_bind=True,
        )

        # === Paso 2: Buscar DN del usuario ===
        search_filter = ldap_cfg.user_filter.format(username=email)
        svc_conn.search(
            search_base=ldap_cfg.base_dn,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=["mail", "cn", "displayName", "memberOf"],
        )

        if not svc_conn.entries:
            logger.warning(f"Usuario no encontrado en LDAP: {email}")
            svc_conn.unbind()
            return False, None

        entry = svc_conn.entries[0]
        user_dn = entry.entry_dn
        display_name = str(entry.displayName) if hasattr(entry, "displayName") and entry.displayName else \
                       str(entry.cn) if hasattr(entry, "cn") and entry.cn else email

        # Verificar si es admin
        is_admin = False
        if ldap_cfg.group_admin_dn:
            member_of = []
            if hasattr(entry, "memberOf") and entry.memberOf:
                member_of = [str(g).lower() for g in entry.memberOf]
            is_admin = ldap_cfg.group_admin_dn.lower() in member_of

        svc_conn.unbind()

        # === Paso 3: Bind con credenciales del usuario ===
        user_conn = Connection(
            server,
            user=user_dn,
            password=password,
            authentication=SIMPLE,
            auto_bind=False,
        )

        if not user_conn.bind():
            logger.warning(f"Credenciales inválidas para: {email}")
            user_conn.unbind()
            return False, None

        user_conn.unbind()

        user = AuthUser(
            email=email,
            display_name=display_name,
            is_admin=is_admin,
            ldap_dn=user_dn,
        )
        logger.info(f"Login exitoso: {email} (admin={is_admin})")
        return True, user

    except Exception as e:
        logger.error(f"Error LDAP para {email}: {e}", exc_info=True)
        return False, None


ADMIN_DEMO = {
    "admin@example.com": {
        "password": "admin123",
        "display_name": "Administrador",
        "is_admin": True,
    },
}


def authenticate_demo(email: str, password: str, local_users: dict = None) -> Tuple[bool, Optional[AuthUser]]:
    """Autenticación demo para desarrollo (sin LDAP real).

    Primero verifica en el admin hardcodeado (inmutable),
    luego busca en los usuarios locales configurados via web.json.
    """
    admin_data = ADMIN_DEMO.get(email.lower())
    if admin_data:
        if admin_data["password"] == password:
            return True, AuthUser(
                email=email.lower(),
                display_name=admin_data["display_name"],
                is_admin=admin_data["is_admin"],
            )
        return False, None

    users = local_users or {}
    user_data = users.get(email.lower())
    if not user_data or user_data.get("password") != password:
        return False, None

    return True, AuthUser(
        email=email.lower(),
        display_name=user_data.get("display_name", email),
        is_admin=user_data.get("is_admin", False),
    )
