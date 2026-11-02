-- ============================================================
-- 002__identity.sql
-- Estado: MIGRATING
-- Checksum: 8f3c2e1d0a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2
-- Dependencia: 001__domains.sql
-- ============================================================

-- Tabla de inquilinos (multiusuario/empresa)
CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    email_domain TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    max_projects INTEGER CHECK (max_projects > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índices para tenants
CREATE INDEX IF NOT EXISTS idx_tenants_slug ON tenants(slug);
CREATE INDEX IF NOT EXISTS idx_tenants_active ON tenants(is_active);

-- Tabla de usuarios
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    email_verified BOOLEAN NOT NULL DEFAULT FALSE,
    full_name TEXT,
    role TEXT NOT NULL CHECK (role IN ('admin', 'developer', 'qa', 'viewer')),
    auth_provider TEXT NOT NULL CHECK (auth_provider IN ('claude', 'github', 'google', 'password')),
    external_id TEXT, -- ID provisto por el proveedor de auth
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, email)
);

-- Índices para usuarios
CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id);
CREATE INDEX IF NOT EXISTS idx_users_provider ON users(auth_provider, external_id);

-- Tabla de identidades de agentes
CREATE TABLE IF NOT EXISTS agent_identities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL CHECK (type IN ('claude', 'sandy', 'custom')),
    config JSONB, -- configuración específica del agente
    capabilities TEXT[], -- habilidades soportadas
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, name)
);

-- Índices para agent_identities
CREATE INDEX IF NOT EXISTS idx_agent_identities_tenant ON agent_identities(tenant_id);
CREATE INDEX IF NOT EXISTS idx_agent_identities_type ON agent_identities(type);

-- Comentarios
COMMENT ON TABLE tenants IS 'Inquilinos (multiusuario/empresa)';
COMMENT ON TABLE users IS 'Usuarios con acceso al sistema';
COMMENT ON TABLE agent_identities IS 'Identidades de agentes IA registrados';