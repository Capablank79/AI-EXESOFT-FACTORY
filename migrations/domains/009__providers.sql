-- ============================================================
-- 009__providers.sql
-- Estado: MIGRATING
-- Checksum: 1e0d9c8b7a6f5e4d3c2b1a0f9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d9c8b7
-- Dependencia: 008__quality.sql
-- ============================================================

-- Tabla de proveedores de modelos
CREATE TABLE IF NOT EXISTS providers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL CHECK (type IN ('llm', 'embedding', 'image', 'audio', 'file')),
    api_base TEXT,
    auth_method TEXT NOT NULL CHECK (auth_method IN ('api_key', 'open_router', 'anthropic', 'azure_oai', 'custom')),
    config JSONB, -- Configuración del proveedor
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, slug)
);

-- Índices para providers
CREATE INDEX IF NOT EXISTS idx_providers_tenant ON providers(tenant_id);
CREATE INDEX IF NOT EXISTS idx_providers_type ON providers(type);
CREATE INDEX IF NOT EXISTS idx_providers_active_default ON providers(is_active, is_default) WHERE is_active = TRUE;

-- Tabla de modelos
CREATE TABLE IF NOT EXISTS models (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    provider_id UUID NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    model_id TEXT NOT NULL, -- ID del modelo en el proveedor
    capabilities TEXT[], -- Habilidades: reason, tools, images, etc.
    input_tokens_limit INTEGER CHECK (input_tokens_limit > 0),
    output_tokens_limit INTEGER CHECK (output_tokens_limit > 0),
    cost_per_1k_input DECIMAL(10,6),
    cost_per_1k_output DECIMAL(10,6),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, provider_id, model_id)
);

-- Índices para models
CREATE INDEX IF NOT EXISTS idx_models_tenant ON models(tenant_id);
CREATE INDEX IF NOT EXISTS idx_models_provider ON models(provider_id);
CREATE INDEX IF NOT EXISTS idx_models_capabilities ON models USING GIN(capabilities);

-- Tabla de solicitudes de modelo (para seguimiento)
CREATE TABLE IF NOT EXISTS model_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    model_id UUID NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    task_id UUID REFERENCES tasks(id) ON DELETE CASCADE,
    prompt TEXT, -- Prompt enviado
    response TEXT, -- Respuesta recibida
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost DECIMAL(12,6),
    duration_ms BIGINT,
    status TEXT NOT NULL CHECK (status IN ('success', 'failed', 'timeout', 'blocked')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índices para model_requests
CREATE INDEX IF NOT EXISTS idx_model_requests_tenant ON model_requests(tenant_id);
CREATE INDEX IF NOT EXISTS idx_model_requests_model ON model_requests(model_id);
CREATE INDEX IF NOT EXISTS idx_model_requests_task ON model_requests(task_id);

-- Comentarios
COMMENT ON TABLE providers IS 'Proveedores de modelos IA registrados';
COMMENT ON TABLE models IS 'Modelos específicos disponibles por provider';
COMMENT ON TABLE model_requests IS 'Registro de solicitudes a modelos (trazabilidad de costos)';