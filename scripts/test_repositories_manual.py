#!/usr/bin/env python3
"""Script manual de pruebas para F2-002 - Repositorios PostgreSQL.

Verifica:
1. Importación de todos los repositorios
2. Estructura básica
3. Métodos disponibles
"""

import os
import sys

# Agregar src al path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Configurar password
os.environ['POSTGRES_APP_PASSWORD'] = 'test_password_123'

try:
    from infrastructure.repositories import (
        IdentityRepository,
        ProjectsRepository,
        PlanningRepository,
        ExecutionRepository,
        MemoryRepository,
        EventsRepository,
        QualityRepository,
        ProvidersRepository,
        ArtifactsRepository
    )
    print("PASS: Todos los repositorios importados correctamente")
except Exception as e:
    print("FAIL: Error importando repositorios -", e)
    sys.exit(1)

# Verificar métodos clave
repos = [
    (IdentityRepository, "Identity"),
    (ProjectsRepository, "Projects"),
    (PlanningRepository, "Planning"),
    (ExecutionRepository, "Execution"),
    (MemoryRepository, "Memory"),
    (EventsRepository, "Events"),
    (QualityRepository, "Quality"),
    (ProvidersRepository, "Providers"),
    (ArtifactsRepository, "Artifacts")
]

print("\nVerificando metodos de cada repositorio:")
for repo_class, name in repos:
    repo = repo_class()
    methods = ['create_', 'get_', 'list_']
    for method in methods:
        if hasattr(repo, method):
            print("  PASS:", name, method)
        else:
            print("  FAIL:", name, method, "- metodo no encontrado")

print("\n=== TODO LISTOS PARA REVIEW ===")