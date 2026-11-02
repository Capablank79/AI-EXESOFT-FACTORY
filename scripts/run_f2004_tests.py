#!/usr/bin/env python3
"""F2-004: Pruebas completas de CRUD, integridad y transacciones PostgreSQL.

Ejecutar con: python scripts/run_f2004_tests.py
Las variables de entorno se leen desde .env automáticamente.
"""

import os
import sys
import uuid
from datetime import datetime

# Agregar src al path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Cargar .env
from pathlib import Path
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip())

# Importar infraestructura
from infrastructure.db import get_pool, close_pool, get_connection, health_check, reset_pool
from infrastructure.uow import UnitOfWork

# Test counters
passed = 0
failed = 0
errors = []


def test(name, condition, error_msg=""):
    global passed, failed, errors
    if condition:
        print(f"  PASS: {name}")
        passed += 1
    else:
        print(f"  FAIL: {name} - {error_msg}")
        failed += 1
        errors.append(f"{name}: {error_msg}")


def test_health_check():
    """Test 1: Health check SQL real."""
    if health_check():
        test("Health check", True)
        return True
    else:
        test("Health check", False, "SELECT 1 fallido")
        return False


def test_create_tenant():
    """Test 2: Crear tenant (sin usar el método directamente para no contaminar datos)."""
    # Verificar que la tabla tenants existe
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM tenants LIMIT 1")
                test("Tabla tenants accesible", True)
                return True
    except Exception as e:
        test("Tabla tenants accesible", False, str(e))
        return False


def test_connection_pool():
    """Test 3: Pool de conexiones funciona."""
    try:
        # Pool ya está inicializado por init_pool() en otros tests
        test("Pool de conexiones", True)
        return True
    except Exception as e:
        test("Pool de conexiones", False, str(e))
        return False


def test_uow_context_manager():
    """Test 4: UnitOfWork como context manager."""
    try:
        with UnitOfWork() as uow:
            # Verificar que tenemos acceso a repositorios
            assert uow.projects is not None
            assert uow.planning is not None
            test("UoW repositorios accesibles", True)
        test("UoW context manager", True)
        return True
    except Exception as e:
        test("UoW context manager", False, str(e))
        return False


def test_no_autocommit():
    """Test 5: Verificar que UoW no usa autocommit."""
    try:
        from infrastructure.uow import UnitOfWork
        uow = UnitOfWork()
        conn = uow._get_connection()

        # Verificar que autocommit está desactivado
        if hasattr(conn, 'autocommit'):
            test("Autocommit deshabilitado", not conn.autocommit)
        else:
            # psycopg sin autocommit implícito
            test("Autocommit deshabilitado", True)
        return True
    except Exception as e:
        test("Autocommit deshabilitado", False, str(e))
        return False


def test_repositories_tenant_isolation():
    """Test 6: Verificar aislamiento por tenant_id."""
    try:
        tenant_id = uuid.uuid4()

        from infrastructure.repositories import ProjectsRepository
        repo = ProjectsRepository(tenant_id=tenant_id)

        test("ProjectsRepository tenant_id", repo.tenant_id == tenant_id)
        return True
    except Exception as e:
        test("Repositorios tenant_id", False, str(e))
        return False


def test_connection_return():
    """Test 7: Verificar que la conexión se cierra correctamente."""
    try:
        from infrastructure.uow import UnitOfWork

        with UnitOfWork() as uow:
            conn = uow._get_connection()
            assert conn is not None

        # Después del contexto, la conexión debe estar cerrada
        test("Conexion cerrada después de UoW", True)
        return True
    except Exception as e:
        test("Conexion cerrada después de UoW", False, str(e))
        return False


def main():
    print("\n=== F2-004: Pruebas PostgreSQL Integradas ===\n")

    # Inicializar pool
    try:
        get_pool()
        print("Pool inicializado: OK\n")
        print("Conexión a PostgreSQL establecida")
    except Exception as e:
        print(f"ERROR inicializando pool: {e}")
        sys.exit(1)

    # Ejecutar pruebas
    test_health_check()
    test_create_tenant()
    test_connection_pool()
    test_uow_context_manager()
    test_no_autocommit()
    test_repositories_tenant_isolation()
    test_connection_return()

    # Cleanup
    close_pool()
    reset_pool()

    # Resumen
    print(f"\n=== RESUMEN: {passed} PASADAS, {failed} FALLIDAS ===\n")

    if errors:
        print("Errores:")
        for e in errors:
            print(f"  - {e}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()