"""Repositorio de identidad - EXESOFT AI FACTORY.

Tablas: tenants, users, agent_identities

Autor: Claude Code - EXESOFT AI FACTORY
"""

import uuid
from typing import Optional, List
from dataclasses import dataclass
import psycopg
from psycopg import rows
from infrastructure.db import get_connection
from infrastructure.repositories.base import Repository


@dataclass
class Tenant:
    id: uuid.UUID
    name: str
    slug: str
    email_domain: Optional[str] = None
    is_active: bool = True
    max_projects: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class User:
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    email_verified: bool = False
    full_name: Optional[str] = None
    role: str = "developer"
    auth_provider: str = "claude"
    external_id: Optional[str] = None
    is_active: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class AgentIdentity:
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    slug: str
    type: str
    config: Optional[dict] = None
    capabilities: Optional[List[str]] = None
    is_active: bool = True
    created_at: Optional[str] = None


class IdentityRepository(Repository):
    """Repository para tenencia, usuarios y agentes."""

    def __init__(self, tenant_id: Optional[uuid.UUID] = None, _conn: Optional[psycopg.Connection] = None):
        super().__init__(tenant_id=tenant_id, _conn=_conn)

    # ============== TENANTS ==============
    def create_tenant(
        self,
        name: str,
        slug: str,
        email_domain: Optional[str] = None,
        max_projects: Optional[int] = None
    ) -> Tenant:
        """Crear un nuevo tenant."""
        query = """
        INSERT INTO tenants (name, slug, email_domain, max_projects)
        VALUES (%s, %s, %s, %s)
        RETURNING id, name, slug, email_domain, is_active, max_projects, created_at, updated_at
        """
        params = [name, slug, email_domain, max_projects]

        # Si hay conexión compartida, usarla; si no, usar pool
        if self._external_conn:
            with self._external_conn.cursor(row_factory=rows.dict_row) as cur:
                cur.execute(query, params)
                row = cur.fetchone()
                return Tenant(**row)
        else:
            from infrastructure.db import get_connection
            with get_connection() as conn:
                with conn.cursor(row_factory=rows.dict_row) as cur:
                    cur.execute(query, params)
                    row = cur.fetchone()
                    return Tenant(**row)

    def get_tenant_by_slug(self, slug: str) -> Optional[Tenant]:
        """Buscar tenant por slug."""
        row = self._fetch_one("SELECT * FROM tenants WHERE slug = %s", [slug])
        return Tenant(**row) if row else None

    def get_tenant(self, tenant_id: uuid.UUID) -> Optional[Tenant]:
        """Obtener tenant por ID."""
        row = self._fetch_one("SELECT * FROM tenants WHERE id = %s", [str(tenant_id)])
        return Tenant(**row) if row else None

    def list_tenants(self, active_only: bool = True) -> List[Tenant]:
        """Listar tenants."""
        query = "SELECT * FROM tenants"
        params = []
        if active_only:
            query += " WHERE is_active = TRUE"
        query += " ORDER BY created_at DESC"

        rows = self._fetch_all(query, params)
        return [Tenant(**row) for row in rows]

    # ============== USERS ==============
    def create_user(
        self,
        tenant_id: uuid.UUID,
        email: str,
        full_name: Optional[str] = None,
        role: str = "developer",
        auth_provider: str = "claude",
        external_id: Optional[str] = None,
        conn: Optional[psycopg.Connection] = None
    ) -> User:
        """Crear un nuevo usuario."""
        query = """
        INSERT INTO users (tenant_id, email, full_name, role, auth_provider, external_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (tenant_id, email) DO UPDATE SET
            full_name = EXCLUDED.full_name,
            role = EXCLUDED.role
        RETURNING *
        """
        params = [str(tenant_id), email, full_name, role, auth_provider, external_id]

        use_conn = conn or self._external_conn
        if use_conn:
            with use_conn.cursor(row_factory=rows.dict_row) as cur:
                cur.execute(query, params)
                row = cur.fetchone()
                return User(**row)
        else:
            with self._get_connection() as c:
                with c.cursor(row_factory=rows.dict_row) as cur:
                    cur.execute(query, params)
                    row = cur.fetchone()
                    return User(**row)

    def get_user(self, user_id: uuid.UUID) -> Optional[User]:
        """Obtener usuario por ID."""
        row = self._fetch_one("SELECT * FROM users WHERE id = %s", [str(user_id)])
        return User(**row) if row else None

    def get_user_by_email(self, tenant_id: uuid.UUID, email: str) -> Optional[User]:
        """Buscar usuario por email dentro de un tenant."""
        row = self._fetch_one(
            "SELECT * FROM users WHERE tenant_id = %s AND email = %s",
            [str(tenant_id), email]
        )
        return User(**row) if row else None

    def list_users(self, tenant_id: uuid.UUID, active_only: bool = True) -> List[User]:
        """Listar usuarios de un tenant."""
        query = "SELECT * FROM users WHERE tenant_id = %s"
        params = [str(tenant_id)]
        if active_only:
            query += " AND is_active = TRUE"
        query += " ORDER BY full_name, email"

        rows = self._fetch_all(query, params)
        return [User(**row) for row in rows]

    # ============== AGENT IDENTITIES ==============
    def create_agent_identity(
        self,
        tenant_id: uuid.UUID,
        name: str,
        agent_type: str,
        slug: Optional[str] = None,
        config: Optional[dict] = None,
        capabilities: Optional[List[str]] = None
    ) -> AgentIdentity:
        """Crear identidad de agente."""
        if slug is None:
            slug = name.lower().replace(" ", "_")

        query = """
        INSERT INTO agent_identities (tenant_id, name, slug, type, config, capabilities)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *
        """
        params = [str(tenant_id), name, slug, agent_type, config, capabilities]

        use_conn = self._external_conn
        if use_conn:
            with use_conn.cursor(row_factory=rows.dict_row) as cur:
                cur.execute(query, params)
                row = cur.fetchone()
                return AgentIdentity(**row)
        else:
            with self._get_connection() as conn:
                with conn.cursor(row_factory=rows.dict_row) as cur:
                    cur.execute(query, params)
                    row = cur.fetchone()
                    return AgentIdentity(**row)

    def get_agent_identity(self, identity_id: uuid.UUID) -> Optional[AgentIdentity]:
        """Obtener identidad de agente por ID."""
        row = self._fetch_one("SELECT * FROM agent_identities WHERE id = %s", [str(identity_id)])
        return AgentIdentity(**row) if row else None

    def get_agent_identity_by_slug(self, tenant_id: uuid.UUID, slug: str) -> Optional[AgentIdentity]:
        """Buscar identidad de agente por slug dentro de un tenant."""
        row = self._fetch_one(
            "SELECT * FROM agent_identities WHERE tenant_id = %s AND slug = %s",
            [str(tenant_id), slug]
        )
        return AgentIdentity(**row) if row else None

    def list_agent_identities(self, tenant_id: uuid.UUID, active_only: bool = True) -> List[AgentIdentity]:
        """Listar identidades de agentes de un tenant."""
        query = "SELECT * FROM agent_identities WHERE tenant_id = %s"
        params = [str(tenant_id)]
        if active_only:
            query += " AND is_active = TRUE"
        query += " ORDER BY name"

        rows = self._fetch_all(query, params)
        return [AgentIdentity(**row) for row in rows]
