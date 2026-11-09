"""Functional tests for provider-independent model routing."""
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / '.env'
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip())

from engine.agents import GatewayModelAdapter, configured_model_adapter
from engine.model_gateway import (
    ModelGateway, ModelGatewayError, ModelRequest, ModelResponse,
    OpenAICompatibleProvider, ProviderRoute,
)
from engine.postgres_runtime import PostgresRuntime
from infrastructure.repositories.events import EventsRepository

TENANT_ID = uuid.UUID('b66f93cd-2f3a-4215-a2ac-93a59a84b4d2')


class ModelGatewayTests(unittest.TestCase):
    def test_provider_can_be_replaced_without_changing_gateway_contract(self):
        class Provider:
            def generate(self, request):
                self.request = request
                return ModelResponse('response from provider')

        provider = Provider()
        response = ModelGateway(provider).complete(ModelRequest(
            messages=[{'role': 'user', 'content': 'prompt'}]
        ))
        self.assertEqual(response.content, 'response from provider')
        self.assertEqual(provider.request.messages[0]['content'], 'prompt')

    def test_gateway_retries_transient_provider_error_with_a_bound(self):
        class FlakyProvider:
            calls = 0

            def generate(self, request):
                self.calls += 1
                if self.calls < 3:
                    raise ModelGatewayError('temporary', retryable=True)
                return ModelResponse('ready')

        provider = FlakyProvider()
        gateway = ModelGateway(provider, max_retries=2)
        self.assertEqual(gateway.complete(ModelRequest(messages=[])).content, 'ready')
        self.assertEqual(provider.calls, 3)

    def test_gateway_does_not_retry_permanent_error(self):
        class BrokenProvider:
            calls = 0

            def generate(self, request):
                self.calls += 1
                raise ModelGatewayError('bad request')

        provider = BrokenProvider()
        with self.assertRaises(ModelGatewayError):
            ModelGateway(provider, max_retries=4).complete(ModelRequest(messages=[]))
        self.assertEqual(provider.calls, 1)

    def test_capability_and_preference_select_compatible_route(self):
        class Provider:
            def __init__(self, content):
                self.content = content
                self.calls = 0

            def generate(self, request):
                self.calls += 1
                return ModelResponse(self.content)

        basic, vision, preferred = Provider('basic'), Provider('vision'), Provider('preferred')
        gateway = ModelGateway(routes=[
            ProviderRoute('basic', basic, model='small', capabilities=frozenset({'text'}), priority=0),
            ProviderRoute('vision', vision, model='vision-model', capabilities=frozenset({'text', 'vision'}), priority=2),
            ProviderRoute('preferred', preferred, model='large', capabilities=frozenset({'text', 'vision'}), priority=3),
        ], max_retries=0)
        response = gateway.complete(ModelRequest(
            messages=[], required_capabilities=frozenset({'vision'}), preferred_provider='preferred'
        ))
        self.assertEqual(response.content, 'preferred')
        self.assertEqual(preferred.calls, 1)
        self.assertEqual((vision.calls, basic.calls), (0, 0))

    def test_transient_failure_retries_then_falls_back_with_route_trace(self):
        class LimitedProvider:
            def __init__(self):
                self.calls = 0

            def generate(self, request):
                self.calls += 1
                raise ModelGatewayError('rate limited', retryable=True)

        class AvailableProvider:
            def generate(self, request):
                return ModelResponse('fallback answer')

        limited = LimitedProvider()
        gateway = ModelGateway(routes=[
            ProviderRoute('primary', limited, capabilities=frozenset({'text'}), priority=0, max_retries=2),
            ProviderRoute('secondary', AvailableProvider(), capabilities=frozenset({'text'}), priority=1, max_retries=0),
        ], max_retries=0)
        response = gateway.complete(ModelRequest(messages=[], required_capabilities=frozenset({'text'})))
        self.assertEqual(limited.calls, 3)
        self.assertEqual(response.provider, 'secondary')
        self.assertEqual(response.route_trace, (
            'primary:retryable_error', 'primary:retryable_error', 'primary:retryable_error',
            'secondary:success',
        ))

    def test_timeout_uses_bounded_fallback_and_capability_mismatch_is_skipped(self):
        class SlowProvider:
            calls = 0

            def generate(self, request):
                self.calls += 1
                raise TimeoutError()

        class TextProvider:
            calls = 0

            def generate(self, request):
                self.calls += 1
                return ModelResponse('ok')

        slow, text = SlowProvider(), TextProvider()
        gateway = ModelGateway(routes=[
            ProviderRoute('slow', slow, capabilities=frozenset({'text', 'vision'}),
                          priority=0, max_retries=1),
            ProviderRoute('text', text, capabilities=frozenset({'text'}),
                          priority=1, max_retries=0),
        ], max_retries=0)
        response = gateway.complete(ModelRequest(
            messages=[], required_capabilities=frozenset({'text'})
        ))
        self.assertEqual(response.content, 'ok')
        self.assertEqual((slow.calls, text.calls), (2, 1))

        with self.assertRaises(ModelGatewayError) as caught:
            gateway.complete(ModelRequest(messages=[], required_capabilities=frozenset({'audio'})))
        self.assertIn('no_capability_match', caught.exception.route_trace)

    def test_unavailable_route_is_skipped_and_exhaustion_is_sanitized(self):
        class Provider:
            def is_available(self):
                return False

            def generate(self, request):
                raise AssertionError('unavailable provider must not be called')

        gateway = ModelGateway(routes=[ProviderRoute('temporarily_down', Provider())])
        with self.assertRaises(ModelGatewayError) as caught:
            gateway.complete(ModelRequest(messages=[]))
        self.assertIn('temporarily_down:unavailable', caught.exception.route_trace)
        self.assertNotIn('secret', str(caught.exception).lower())

    def test_structured_response_is_parsed_and_invalid_json_rejected(self):
        class Provider:
            def __init__(self, content):
                self.content = content

            def generate(self, request):
                return ModelResponse(self.content)

        response = ModelGateway(Provider('{"answer": 42}')).complete(ModelRequest(
            messages=[], response_format='json'
        ))
        self.assertEqual(response.structured, {'answer': 42})
        with self.assertRaises(ModelGatewayError) as caught:
            ModelGateway(Provider('not json')).complete(ModelRequest(messages=[], response_format='json'))
        self.assertIn('default:provider_error', caught.exception.route_trace)

    def test_agent_adapter_passes_isolation_metadata_and_returns_result(self):
        class Provider:
            def generate(self, request):
                self.request = request
                return ModelResponse('plan')

        provider = Provider()
        adapter = GatewayModelAdapter(ModelGateway(provider))
        result = adapter.execute(
            {'agent': 'pm', 'title': 'Scope'},
            {'name': 'Project', 'request': 'Build it'},
            {'workspace': '.', 'tenant_id': 'tenant-a', 'project_id': 'project-b',
             'task_id': 'task-c', 'run_id': 'run-d'},
        )
        self.assertTrue(result.success)
        self.assertTrue(result.summary.startswith('plan'))
        self.assertIn('default:success', result.summary)
        self.assertEqual(provider.request.metadata, {
            'tenant_id': 'tenant-a', 'project_id': 'project-b',
            'task_id': 'task-c', 'run_id': 'run-d', 'agent': 'pm',
        })

    def test_configured_http_provider_uses_environment_credential_without_logging_it(self):
        response = MagicMock()
        response.__enter__.return_value = response
        secret = 'never-print-this-token'
        with patch.dict(os.environ, {
            'EXESOFT_MODEL_ENDPOINT': 'https://model.example/v1/chat/completions',
            'EXESOFT_MODEL_NAME': 'test-model',
            'EXESOFT_MODEL_API_KEY_ENV': 'TEST_MODEL_SECRET',
            'TEST_MODEL_SECRET': secret,
            'EXESOFT_MODEL_RETRIES': '0',
        }, clear=True), patch('urllib.request.urlopen', return_value=response) as open_url, \
                patch('json.load', return_value={'choices': [{'message': {'content': 'ok'}}]}):
            adapter = configured_model_adapter()
            result = adapter.execute(
                {'agent': 'pm', 'title': 'Scope'}, {'name': 'P', 'request': 'R'}, {'workspace': '.'}
            )
        self.assertTrue(result.success)
        request = open_url.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + secret)
        self.assertNotIn(secret, result.summary)
        self.assertEqual(open_url.call_args.kwargs['timeout'], 90.0)

    def test_environment_route_list_builds_capability_ordered_fallbacks(self):
        definitions = [
            {'name': 'text', 'endpoint': 'https://text.example/chat', 'model': 'text-v1',
             'capabilities': ['text'], 'priority': 0, 'max_retries': 0},
            {'name': 'json', 'endpoint': 'https://json.example/chat', 'model': 'json-v1',
             'capabilities': ['text', 'json'], 'priority': 1, 'max_retries': 1},
        ]
        with patch.dict(os.environ, {
            'EXESOFT_MODEL_ROUTES': json.dumps(definitions),
            'EXESOFT_MODEL_RETRIES': '2',
        }, clear=True):
            adapter = configured_model_adapter()
        self.assertIsInstance(adapter, GatewayModelAdapter)
        self.assertEqual([route.name for route in adapter.gateway.routes], ['text', 'json'])

    def test_no_provider_configuration_fails_closed_without_vendor_default(self):
        with patch.dict(os.environ, {}, clear=True):
            adapter = configured_model_adapter()
        result = adapter.execute({'agent': 'pm'}, {}, {})
        self.assertFalse(result.success)
        self.assertNotIn('FreeLLMAPI', result.summary)

def test_postgres_runtime_routes_agent_call_through_gateway_with_trace_context(isolated_postgres):
    class Provider:
        request = None

        def generate(self, request):
            self.request = request
            return ModelResponse('gateway plan')

    provider = Provider()
    adapter = GatewayModelAdapter(ModelGateway(provider))
    with tempfile.TemporaryDirectory() as tmp:
        runtime = PostgresRuntime(tmp, {'pm': adapter}, tenant_id=TENANT_ID)
        project_id = runtime.orchestrator.create_project(
            'F5-001 Gateway ' + str(uuid.uuid4()), 'Exercise provider gateway routing'
        )
        isolated_postgres(project_id)
        tasks = runtime.orchestrator.plan_skeleton(project_id)
        from plan_approval_test_support import approve_test_plan
        approve_test_plan(TENANT_ID, project_id)
        outcome = runtime.step(project_id)

    assert outcome['state'] == 'done'
    assert provider.request.metadata['tenant_id'] == str(TENANT_ID)
    assert provider.request.metadata['project_id'] == project_id
    assert provider.request.metadata['task_id'] == tasks[0]['id']
    assert provider.request.metadata['run_id'] == outcome['run']
    assert provider.request.request_id == outcome['run']
    trace = EventsRepository(tenant_id=TENANT_ID).execution_trace(
        uuid.UUID(project_id), uuid.UUID(tasks[0]['id'])
    )
    succeeded = next(event for event in trace if event.type == 'task.succeeded')
    assert succeeded.detail['run_id'] == outcome['run']
    assert 'model route:' in outcome['summary']


if __name__ == '__main__':
    unittest.main()
