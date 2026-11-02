"""Execute a single supervised local-router call. Needs no additional packages."""
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.live_bridge import supervised_cycle


def main():
    print('Exesoft v0.3.1 — Verificación real (1 tarea PM, texto solamente)')
    print('Router: http://127.0.0.1:3001 | Modelo: gpt-oss-120b')
    key = os.environ.get('EXESOFT_ROUTER_KEY') or getpass.getpass('Clave unificada de FreeLLMAPI (oculta): ')
    try:
        result = supervised_cycle(ROOT, key)
    finally:
        key = None
    print('Proyecto:', result.project_id)
    print('Tarea:', result.task_id)
    print('Ejecución persistida:', result.run_id)
    print('Estado:', result.state)
    print('Registro de conocimiento:', result.knowledge_id if result.knowledge_id else '(sin respuesta exitosa)')
    print('Respuesta:', result.summary[:1600])
    if result.state != 'succeeded' or result.knowledge_id is None:
        print('ERROR: La ejecución no se completó satisfactoriamente.', file=sys.stderr)
        return 1
    print('[OK] Ciclo Brain -> Runtime -> FreeLLMAPI -> Brain confirmado.')
    return 0

if __name__ == '__main__':
    sys.exit(main())
