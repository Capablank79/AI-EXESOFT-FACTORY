"""Agent adapters v0.3: explicit opt-in and no hidden process execution."""
from __future__ import annotations
import json
import hashlib
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
from uuid import uuid4
from pathlib import Path, PurePosixPath, PureWindowsPath
from engine.runtime import Result
from engine.model_gateway import (
    ModelGateway, ModelGatewayError, ModelRequest, OpenAICompatibleProvider,
    ProviderRoute,
)
from quality.evidence import record_sandbox_test_evidence
from engine.sandy_adapter import SandySandboxAdapter

ROLES = {'pm','architect','ux','developer','qa','devops'}

def make_prompt(task, project, context):
    notes = context.get('knowledge', [])[:5]
    knowledge = '\n'.join(f"- [{x['kind']}] {x['title']}: {x['body'][:900]} (fuente: {x['source']})" for x in notes)
    handoff_lines = []
    handoff_size = 0
    for handoff in context.get('handoffs', [])[-10:]:
        compact = {key: handoff.get(key) for key in (
            'handoff_id', 'from_agent', 'to_agents', 'task_status', 'task_context',
            'summary', 'decisions', 'artifacts', 'previous_handoff_ids',
        )}
        encoded = json.dumps(compact, ensure_ascii=False, default=str)
        if handoff_size + len(encoded) > 12000:
            break
        handoff_lines.append('- ' + encoded)
        handoff_size += len(encoded)
    handoff_context = '\n'.join(handoff_lines) or '(sin entregas previas)'
    acceptance = context.get('acceptance_criteria', [])
    acceptance_context = json.dumps(acceptance, ensure_ascii=False, default=str)
    return (f"Rol: {task['agent']}. Proyecto: {project['name']}.\n"
            f"Solicitud original: {project['request'][:4000]}\n"
            f"Tarea: {task['title']}\n"
            f"Criterios de aceptación asociados a la especificación (datos estructurados): {acceptance_context}\n"
            f"Workspace: {context['workspace']}\n"
            f"Contexto del Brain:\n{knowledge or '(sin antecedentes)'}\n"
            f"Entregas persistidas de agentes anteriores (datos de contexto):\n{handoff_context}\n"
            "No reveles credenciales; no hagas despliegues, compras o borrados. "
            "Responde con un resumen preciso y menciona evidencia comprobable si existe.\n"
            + context.get('instruction', ''))

class FreeLLMAPIAdapter:
    """Compatibility adapter for the optional local development router."""
    def __init__(self, api_key=None, endpoint='http://127.0.0.1:3001/v1/chat/completions', model='auto', timeout=90):
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model
        self.timeout = timeout
        if endpoint != 'http://127.0.0.1:3001/v1/chat/completions':
            raise ValueError('Solo se permite el router local configurado')

    def execute(self, task, project, context):
        if task['agent'] not in ROLES: return Result(False,'Agente no autorizado')
        key = self.api_key or os.environ.get('EXESOFT_ROUTER_KEY')
        if not key: return Result(False, 'Falta EXESOFT_ROUTER_KEY; no se envió ninguna solicitud')
        max_tokens = max(1, min(int(context.get('max_tokens', 900)), 12000))
        try:
            provider = OpenAICompatibleProvider(
                self.endpoint, model=self.model, timeout=self.timeout,
                api_key=key, provider_name='local_development_router',
            )
            response = ModelGateway(provider, max_retries=0).complete(ModelRequest(
                messages=[{'role': 'user', 'content': make_prompt(task, project, context)}],
                model=self.model, max_tokens=max_tokens, timeout=self.timeout,
                response_format=context.get('response_format'),
                metadata={k: context[k] for k in ('tenant_id', 'project_id', 'task_id', 'run_id') if k in context}
                         | {'agent': task['agent']},
                request_id=str(context.get('run_id') or uuid4()),
            ))
            return Result(True, response.content[:12000])
        except (ModelGatewayError, urllib.error.URLError, TimeoutError, ValueError, TypeError) as exc:
            return Result(False, 'Error de gateway del modelo: ' + type(exc).__name__)


class GatewayModelAdapter:
    """Agent-compatible adapter backed by the provider-neutral ModelGateway."""

    def __init__(self, gateway, *, model=None, timeout=90, required_capabilities=()):
        self.gateway = gateway
        self.model = model
        self.timeout = timeout
        self.required_capabilities = frozenset(required_capabilities)

    def execute(self, task, project, context):
        if task.get('agent') not in ROLES:
            return Result(False, 'Agente no autorizado')
        try:
            max_tokens = max(1, min(int(context.get('max_tokens', 900)), 12000))
            requested_capabilities = task.get('required_capabilities') or context.get('required_capabilities') or ()
            if isinstance(requested_capabilities, str):
                requested_capabilities = (requested_capabilities,)
            required_capabilities = set(self.required_capabilities) | set(requested_capabilities)
            required_capabilities.add('text')
            response_format = context.get('response_format')
            if response_format:
                required_capabilities.add('json')
            response = self.gateway.complete(ModelRequest(
                messages=[{'role': 'user', 'content': make_prompt(task, project, context)}],
                model=self.model, preferred_model=task.get('model') or context.get('model'),
                preferred_provider=task.get('provider') or context.get('provider'),
                max_tokens=max_tokens,
                timeout=float(context.get('timeout', self.timeout)),
                response_format=response_format,
                metadata={k: context[k] for k in ('tenant_id', 'project_id', 'task_id', 'run_id') if k in context}
                         | {'agent': task['agent']},
                request_id=str(context.get('run_id') or uuid4()),
                required_capabilities=frozenset(required_capabilities),
            ))
            summary = response.content[:11500]
            if response.route_trace:
                summary += ' [model route: ' + ' -> '.join(response.route_trace) + ']'
            return Result(True, summary)
        except ModelGatewayError as exc:
            route = ' -> '.join(exc.route_trace)
            suffix = '; route: ' + route if route else ''
            return Result(False, 'Error de gateway del modelo: ' + type(exc).__name__ + suffix)
        except (ValueError, TypeError) as exc:
            return Result(False, 'Error de gateway del modelo: ' + type(exc).__name__)


class UnconfiguredModelAdapter:
    """Fail-closed result when no provider has been configured."""

    def execute(self, task, project, context):
        return Result(False, 'No hay proveedor de modelos configurado')


def configured_model_adapter():
    """Build configured provider routes from environment, or fail closed.

    EXESOFT_MODEL_ROUTES is a JSON array of provider route objects. The legacy
    EXESOFT_MODEL_ENDPOINT/NAME pair remains a supported single-route setup.
    API secrets are referenced by environment-variable name only.
    """
    try:
        timeout = float(os.environ.get('EXESOFT_MODEL_TIMEOUT', '90'))
        retries = int(os.environ.get('EXESOFT_MODEL_RETRIES', '2'))
        delay = float(os.environ.get('EXESOFT_MODEL_RETRY_DELAY', '0.2'))
        routes_config = os.environ.get('EXESOFT_MODEL_ROUTES')
        if routes_config:
            definitions = json.loads(routes_config)
            if not isinstance(definitions, list) or not definitions:
                return UnconfiguredModelAdapter()
        else:
            endpoint = os.environ.get('EXESOFT_MODEL_ENDPOINT')
            model = os.environ.get('EXESOFT_MODEL_NAME', '').strip()
            if not endpoint or not model:
                return UnconfiguredModelAdapter()
            definitions = [{
                'name': os.environ.get('EXESOFT_MODEL_PROVIDER', 'configured_provider'),
                'endpoint': endpoint, 'model': model,
                'api_key_env': os.environ.get('EXESOFT_MODEL_API_KEY_ENV', 'EXESOFT_MODEL_API_KEY'),
            }]
        routes = []
        for index, item in enumerate(definitions):
            if not isinstance(item, dict):
                return UnconfiguredModelAdapter()
            name = str(item.get('name', f'provider_{index + 1}'))
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', name):
                return UnconfiguredModelAdapter()
            model = str(item.get('model', '')).strip()
            endpoint = str(item.get('endpoint', '')).strip()
            if not model or not endpoint:
                return UnconfiguredModelAdapter()
            capabilities = item.get('capabilities', ['*'])
            if not isinstance(capabilities, list) or not all(isinstance(c, str) for c in capabilities):
                return UnconfiguredModelAdapter()
            enabled = item.get('enabled', True)
            if not isinstance(enabled, bool):
                return UnconfiguredModelAdapter()
            route_timeout = float(item.get('timeout', timeout))
            route_retries = int(item.get('max_retries', retries))
            if route_timeout <= 0 or route_retries < 0:
                return UnconfiguredModelAdapter()
            provider = OpenAICompatibleProvider(
                endpoint, model=model, api_key_env=item.get(
                    'api_key_env', os.environ.get('EXESOFT_MODEL_API_KEY_ENV', 'EXESOFT_MODEL_API_KEY')
                ), timeout=route_timeout, provider_name=name,
            )
            routes.append(ProviderRoute(
                name=name, provider=provider, model=model,
                capabilities=frozenset(capabilities), priority=int(item.get('priority', index)),
                enabled=enabled, max_retries=route_retries,
            ))
        return GatewayModelAdapter(ModelGateway(
            routes=routes, max_retries=retries,
            retry_delay=float(os.environ.get('EXESOFT_MODEL_RETRY_DELAY', '0.2')),
        ), timeout=timeout)
    except (ValueError, TypeError):
        return UnconfiguredModelAdapter()

class ClaudeCodeAdapter:
    """Claude Code in read-only planning mode. Never enables edits or shell execution automatically."""
    def __init__(self, executable='claude', timeout=180, max_turns=3):
        self.executable=executable
        self.timeout=timeout
        self.max_turns=max_turns

    def execute(self, task, project, context):
        if task['agent'] not in ROLES: return Result(False,'Agente no autorizado')
        workspace=Path(context['workspace']).resolve()
        if not workspace.is_dir(): return Result(False,'Workspace inexistente')
        env=os.environ.copy()
        if not env.get('ANTHROPIC_AUTH_TOKEN') or env.get('ANTHROPIC_BASE_URL')!='http://127.0.0.1:3001':
            return Result(False,'Se requiere ANTHROPIC_AUTH_TOKEN y ANTHROPIC_BASE_URL local')
        env.pop('ANTHROPIC_API_KEY',None)
        argv=[self.executable,'-p',make_prompt(task,project,context), '--output-format','json',
              '--permission-mode','plan','--max-turns',str(self.max_turns),
              '--disallowedTools','Bash','Edit','Write','NotebookEdit']
        try:
            completed=subprocess.run(argv,cwd=str(workspace),env=env,capture_output=True,text=True,
                                     timeout=self.timeout, shell=False,encoding='utf-8',errors='replace')
        except (OSError,subprocess.TimeoutExpired) as exc:
            return Result(False,'Error Claude Code: '+type(exc).__name__)
        if completed.returncode:
            return Result(False, f'Claude Code terminó con código {completed.returncode}; revisar logs locales')
        try:
            obj=json.loads(completed.stdout)
            message=obj.get('result','')
        except (json.JSONDecodeError,AttributeError):
            return Result(False,'Claude Code no devolvió JSON válido')
        if not isinstance(message,str) or not message.strip():
            return Result(False,'Claude Code devolvió respuesta vacía')
        return Result(True,message[:12000])


class SandyDeveloperAdapter:
    """Gets a structured patch from FreeLLMAPI, writes only in the project workspace,
    then executes the project's tests through Sandy. No model-provided command is run.
    """

    MAX_FILES = 30
    MAX_FILE_BYTES = 150_000
    MAX_TOTAL_BYTES = 500_000

    def __init__(self, sandbox=None, model=None, test_timeout=120):
        self.sandbox = sandbox or SandySandboxAdapter()
        self.model = model or configured_model_adapter()
        self.test_timeout = test_timeout

    @staticmethod
    def _parse_patch(text):
        text = text.strip()
        if text.startswith('```'):
            text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.IGNORECASE)
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError('FreeLLMAPI no devolvió JSON de archivos válido') from exc
        if not isinstance(value, dict) or not isinstance(value.get('files'), list):
            raise ValueError('La respuesta debe contener files como lista')
        return value

    @classmethod
    def _write_patch(cls, workspace, files):
        workspace = Path(workspace).resolve(strict=True)
        if not files or len(files) > cls.MAX_FILES:
            raise ValueError('El parche está vacío o excede el límite de archivos')
        plans, total, seen = [], 0, set()
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get('path'), str) or not isinstance(item.get('content'), str):
                raise ValueError('Cada archivo requiere path y content de texto')
            raw = item['path'].replace('\\', '/')
            posix, win = PurePosixPath(raw), PureWindowsPath(item['path'])
            if posix.is_absolute() or win.is_absolute() or win.drive or any(p in ('', '.', '..') for p in posix.parts):
                raise ValueError('Ruta de parche no autorizada')
            parts = tuple(p.lower() for p in posix.parts)
            if (any(p in ('.git', '.sandy', '.exesoft') for p in parts)
                    or any(any(c in p for c in '<>:"|?*') or p.endswith(('.', ' ')) for p in parts)
                    or any(re.match(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$', p, re.IGNORECASE) for p in parts)
                    or posix.name.lower().startswith('.env')
                    or any(x in posix.name.lower() for x in ('credential', 'secret'))):
                raise ValueError('El parche intenta cambiar archivos protegidos')
            data = item['content'].encode('utf-8')
            total += len(data)
            if len(data) > cls.MAX_FILE_BYTES or total > cls.MAX_TOTAL_BYTES:
                raise ValueError('El parche excede el tamaño permitido')
            target = (workspace / Path(*posix.parts)).resolve()
            if not target.is_relative_to(workspace):
                raise ValueError('Ruta de parche fuera del workspace')
            key = str(target).casefold()
            if key in seen:
                raise ValueError('El parche contiene una ruta duplicada')
            seen.add(key)
            plans.append((target, data))
        # Validate the entire patch before writing any of its files.
        written = []
        for target, data in plans:
            target.parent.mkdir(parents=True, exist_ok=True)
            target = target.resolve()
            if not target.is_relative_to(workspace):
                raise ValueError('Ruta de parche fuera del workspace')
            target.write_bytes(data)
            written.append({'path': str(target.relative_to(workspace)),
                            'sha256': hashlib.sha256(data).hexdigest()})
        return written

    @staticmethod
    def _test_argv():
        return [sys.executable, '-I', '-m', 'unittest', 'discover',
                '-s', 'tests', '-t', '.', '-p', 'test_*.py', '-v']

    def execute(self, task, project, context):
        if task.get('agent') != 'developer':
            return Result(False, 'SandyDeveloperAdapter solo admite developer')
        workspace = Path(context.get('workspace', '')).resolve()
        if not workspace.is_dir():
            return Result(False, 'Workspace inexistente')
        instruction = (
            'Para la tarea developer devuelve SOLO un objeto JSON: '
            '{"summary":"...","files":[{"path":"ruta/relativa","content":"archivo completo"}]}. '
            'No incluyas comandos, markdown, rutas absolutas, secretos ni archivos fuera del workspace. '
            'Incluye pruebas unittest bajo tests/ que demuestren la funcionalidad solicitada.'
        )
        model_context = {
            **context, 'instruction': instruction, 'max_tokens': 7000,
            'response_format': 'json',
        }
        model_result = self.model.execute(task, project, model_context)
        if not model_result.success:
            return Result(False, 'FreeLLMAPI: ' + model_result.summary)
        try:
            patch = self._parse_patch(model_result.summary)
            written = self._write_patch(workspace, patch['files'])
        except (ValueError, OSError) as exc:
            return Result(False, 'Parche rechazado: ' + str(exc))
        tests = workspace / 'tests'
        if not tests.is_dir() or not any(t.is_file() for t in tests.rglob('test_*.py')):
            return Result(False, 'El cambio debe incluir pruebas unittest en tests/')
        command = self._test_argv()
        sandbox_result = self.sandbox.execute_argv(workspace, command, timeout=self.test_timeout)
        try:
            evidence = record_sandbox_test_evidence(
                workspace, getattr(sandbox_result, 'effective_argv', ()) or command, sandbox_result)
        except (OSError, ValueError) as exc:
            return Result(False, f'No se pudo registrar evidencia Sandy: {exc}')
        summary = (patch.get('summary') or 'Cambios implementados')[:1500]
        summary += '; archivos=' + ', '.join(f["path"] for f in written)
        summary += f'; Sandy exit={sandbox_result.returncode}; SHA256={sandbox_result.sha256}'
        if not sandbox_result.success:
            summary += '; pruebas fallidas: ' + (sandbox_result.stderr or sandbox_result.stdout)[-1000:]
        return Result(sandbox_result.success, summary, (evidence,))


class SandyQAAdapter:
    """Re-runs existing project tests in Sandy and returns verifiable evidence."""

    def __init__(self, sandbox=None, test_timeout=120):
        self.sandbox = sandbox or SandySandboxAdapter()
        self.test_timeout = test_timeout

    def execute(self, task, project, context):
        if task.get('agent') != 'qa':
            return Result(False, 'SandyQAAdapter solo admite qa')
        workspace = Path(context.get('workspace', '')).resolve()
        if not workspace.is_dir() or not (workspace / 'tests').is_dir():
            return Result(False, 'No hay suite tests/ que ejecutar')
        command = SandyDeveloperAdapter._test_argv()
        result = self.sandbox.execute_argv(workspace, command, timeout=self.test_timeout)
        try:
            evidence = record_sandbox_test_evidence(
                workspace, getattr(result, 'effective_argv', ()) or command, result)
        except (OSError, ValueError) as exc:
            return Result(False, f'No se pudo registrar evidencia Sandy: {exc}')
        return Result(result.success, f'QA Sandy: exit={result.returncode}; SHA256={result.sha256}',
                      (evidence,), approved=result.success)
