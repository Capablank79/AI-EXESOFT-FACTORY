#!/usr/bin/env python3
"""Prueba de conexión PostgreSQL para EXESOFT AI FACTORY."""
import os
import psycopg

# Leer .env manualmente
env_path = r"C:\AI-Factory\.env"
config = {
    'user': 'exesoft_factory_migrator',
    'password': 'amor2030',
    'host': '127.0.0.1',
    'port': '5432',
    'db': 'exesoft_factory'
}

if os.path.exists(env_path):
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                if key == 'POSTGRES_MIGRATOR_USER':
                    config['user'] = value
                elif key == 'POSTGRES_MIGRATOR_PASSWORD':
                    config['password'] = value
                elif key == 'POSTGRES_HOST':
                    config['host'] = value
                elif key == 'POSTGRES_PORT':
                    config['port'] = value
                elif key == 'POSTGRES_DB':
                    config['db'] = value

# Construir URL
db_url = f"postgresql://{config['user']}:{config['password']}@{config['host']}:{config['port']}/{config['db']}"

# No imprimir password completo
print(f"Probando conexión a PostgreSQL...")
print(f"Usuario: {config['user']}")
print(f"Host: {config['host']}:{config['port']}")
print(f"Base: {config['db']}")
print()

try:
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user, current_database()")
            user, db = cur.fetchone()
            print("CONEXION EXITOSA!")
            print(f"Usuario conectado: {user}")
            print(f"Base de datos: {db}")
except Exception as e:
    print(f"CONEXION FALLIDA: {e}")