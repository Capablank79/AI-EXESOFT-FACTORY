#!/usr/bin/env python3
"""Script manual de pruebas para F2-001 - Capa de conexión PostgreSQL.

Ejecuta pruebas sin pytest para verificar:
1. Configuracion desde .env
2. Pool de conexiones
3. Health check
4. Context managers
5. Cierre ordenado
"""

import os
import sys

# Agregar src al path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from infrastructure import db as db_module

# Configurar password para tests
os.environ['POSTGRES_APP_PASSWORD'] = 'test_password_123'
os.environ['POSTGRES_APP_USER'] = 'exesoft_factory_app'
os.environ['POSTGRES_HOST'] = '127.0.0.1'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_DB'] = 'exesoft_factory'

# Reset del modulo
db_module._config = None
db_module.reset_pool()

passed = 0
failed = 0

def test(name, condition, error_msg=""):
    global passed, failed
    if condition:
        print("  PASS:", name)
        passed += 1
    else:
        print("  FAIL:", name, "-", error_msg)
        failed += 1

print("\n=== PRUEBAS F2-001: Capa de Conexion PostgreSQL ===\n")

print("1. test_connection_basic - Conexion basica:")
url = db_module.get_db_url()
test("URL construida correctamente",
     url.startswith('postgresql://exesoft_factory_app:') and
     '127.0.0.1:5432/exesoft_factory' in url)

config = db_module.get_config()
test("Config user correcto", config.user == 'exesoft_factory_app')
test("Config port es entero", isinstance(config.port, int))
test("Config port valor", config.port == 5432)

print("\n2. test_pool_reuse - Pool de conexiones:")
pool = db_module.get_pool()
test("Pool creado", pool is not None)
stats = db_module.get_pool_stats()
test("Pool stats status opened", stats['status'] == 'opened')
test("Pool stats min_size=2", stats['min_size'] == 2)
test("Pool stats max_size=10", stats['max_size'] == 10)
test("Pool stats timeout=30.0", stats['timeout'] == 30.0)

print("\n3. test_connection_close - Cierre ordenado:")
pool1 = db_module.get_pool()
test("Pool1 creado", pool1 is not None)
db_module.close_pool()
test("Pool cerrado (None)", db_module._pool is None)

print("\n4. test_connection_recovery - Recuperacion:")
db_module.reset_pool()
pool2 = db_module.get_pool()
test("Pool reopen creado", pool2 is not None)
test("Pool diferente tras reset", pool1 is not pool2)

print("\n5. test_health_check - Health check SQL real:")
result = db_module.health_check()
test("health_check callable", callable(db_module.health_check))
test("health_check retorna bool", isinstance(result, bool))

print("\n6. test_pool_configurable_timeouts - Timeouts configurables:")
custom_config = db_module.PoolConfig(min_size=5, max_size=20, timeout=60.0)
db_module.configure(custom_config)
db_module.reset_pool()
pool3 = db_module.get_pool()
stats3 = db_module.get_pool_stats()
test("min_size personalizado", stats3['min_size'] == 5)
test("max_size personalizado", stats3['max_size'] == 20)
test("timeout personalizado", stats3['timeout'] == 60.0)

print("\n7. test_context_manager - Context manager:")
with db_module.get_connection() as conn:
    test("Conexion obtenida desde pool", conn is not None)

print("\n8. test_test_connection - Funcion test_connection:")
result = db_module.test_connection()
test("test_connection retorna dict", isinstance(result, dict))
test("test_connection tiene success", 'success' in result)

# Cleanup
db_module.reset_pool()

print("\n=== RESULTADO: %d PASADAS, %d FALLIDAS ===\n" % (passed, failed))

if failed > 0:
    sys.exit(1)