-- ============================================================
-- 014__tenant_edge_guards.sql
-- EXF-F1-003: enforce tenant scope on optional/global domain edges
-- Dependencia: 013__security_defaults_indexes.sql
-- ============================================================

CREATE OR REPLACE FUNCTION public.enforce_model_provider_tenant()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    provider_tenant UUID;
BEGIN
    SELECT tenant_id INTO provider_tenant FROM public.providers WHERE id = NEW.provider_id FOR SHARE;
    IF FOUND AND provider_tenant IS NOT NULL AND provider_tenant IS DISTINCT FROM NEW.tenant_id THEN
        RAISE EXCEPTION 'model tenant must match scoped provider tenant'
            USING ERRCODE = '23514', CONSTRAINT = 'models_provider_same_tenant';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER models_provider_tenant_guard
BEFORE INSERT OR UPDATE OF tenant_id, provider_id ON public.models
FOR EACH ROW EXECUTE FUNCTION public.enforce_model_provider_tenant();

CREATE OR REPLACE FUNCTION public.enforce_provider_tenant_update()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.tenant_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM public.models m
        WHERE m.provider_id = NEW.id AND m.tenant_id IS DISTINCT FROM NEW.tenant_id
    ) THEN
        RAISE EXCEPTION 'provider tenant conflicts with existing model tenants'
            USING ERRCODE = '23514', CONSTRAINT = 'providers_models_same_tenant';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER providers_tenant_update_guard
BEFORE UPDATE OF tenant_id ON public.providers
FOR EACH ROW EXECUTE FUNCTION public.enforce_provider_tenant_update();

CREATE OR REPLACE FUNCTION public.enforce_model_request_tenant()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    model_tenant UUID;
BEGIN
    SELECT tenant_id INTO model_tenant FROM public.models WHERE id = NEW.model_id FOR SHARE;
    IF FOUND AND model_tenant IS NOT NULL AND model_tenant IS DISTINCT FROM NEW.tenant_id THEN
        RAISE EXCEPTION 'model request tenant must match scoped model tenant'
            USING ERRCODE = '23514', CONSTRAINT = 'model_requests_model_same_tenant';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER model_requests_model_tenant_guard
BEFORE INSERT OR UPDATE OF tenant_id, model_id ON public.model_requests
FOR EACH ROW EXECUTE FUNCTION public.enforce_model_request_tenant();

CREATE OR REPLACE FUNCTION public.enforce_model_request_tenant_update()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.tenant_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM public.model_requests r
        WHERE r.model_id = NEW.id AND r.tenant_id IS DISTINCT FROM NEW.tenant_id
    ) THEN
        RAISE EXCEPTION 'model tenant conflicts with existing request tenants'
            USING ERRCODE = '23514', CONSTRAINT = 'models_requests_same_tenant';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER models_request_tenant_update_guard
BEFORE UPDATE OF tenant_id ON public.models
FOR EACH ROW EXECUTE FUNCTION public.enforce_model_request_tenant_update();

CREATE OR REPLACE FUNCTION public.enforce_validation_gate_tenant()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    gate_tenant UUID;
BEGIN
    IF NEW.quality_gate_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT tenant_id INTO gate_tenant FROM public.quality_gates WHERE id = NEW.quality_gate_id FOR SHARE;
    IF FOUND AND gate_tenant IS NOT NULL AND gate_tenant IS DISTINCT FROM NEW.tenant_id THEN
        RAISE EXCEPTION 'validation tenant must match scoped quality gate tenant'
            USING ERRCODE = '23514', CONSTRAINT = 'validations_gate_same_tenant';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER validations_gate_tenant_guard
BEFORE INSERT OR UPDATE OF tenant_id, quality_gate_id ON public.validations
FOR EACH ROW EXECUTE FUNCTION public.enforce_validation_gate_tenant();

CREATE OR REPLACE FUNCTION public.enforce_gate_tenant_update()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.tenant_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM public.validations v
        WHERE v.quality_gate_id = NEW.id AND v.tenant_id IS DISTINCT FROM NEW.tenant_id
    ) THEN
        RAISE EXCEPTION 'quality gate tenant conflicts with existing validation tenants'
            USING ERRCODE = '23514', CONSTRAINT = 'quality_gates_validations_same_tenant';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER quality_gates_validation_tenant_update_guard
BEFORE UPDATE OF tenant_id ON public.quality_gates
FOR EACH ROW EXECUTE FUNCTION public.enforce_gate_tenant_update();

GRANT EXECUTE ON FUNCTION public.enforce_model_provider_tenant() TO exesoft_factory_app;
GRANT EXECUTE ON FUNCTION public.enforce_provider_tenant_update() TO exesoft_factory_app;
GRANT EXECUTE ON FUNCTION public.enforce_model_request_tenant() TO exesoft_factory_app;
GRANT EXECUTE ON FUNCTION public.enforce_model_request_tenant_update() TO exesoft_factory_app;
GRANT EXECUTE ON FUNCTION public.enforce_validation_gate_tenant() TO exesoft_factory_app;
GRANT EXECUTE ON FUNCTION public.enforce_gate_tenant_update() TO exesoft_factory_app;
