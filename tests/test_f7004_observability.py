import json
import logging
import asyncio
from types import SimpleNamespace

from api import app as api_module
from engine.model_gateway import ModelGateway, ModelRequest, ModelResponse, ProviderRoute
from infrastructure.observability import (
    JsonFormatter, bind_log_context, log_event, reset_log_context,
)
from infrastructure.db import DBConfig
from infrastructure import db
import pytest


def test_structured_log_has_correlations_and_redacts_secrets():
    logger = logging.getLogger('f7004.test')
    record = logger.makeRecord(logger.name, logging.INFO, __file__, 1,
                               'provider token=topsecret Bearer abc123 postgresql://user:dbsecret@localhost/db', (), None)
    record.obs_event = 'test.event'
    record.obs_fields = {'provider': 'mock', 'request_id': 'req-1', 'prompt': 'must not log'}
    token = bind_log_context(project_id='project-1', task_id='task-1')
    try:
        payload = json.loads(JsonFormatter().format(record))
    finally:
        reset_log_context(token)
    assert payload['severity'] == 'INFO'
    assert payload['event'] == 'test.event'
    assert payload['request_id'] == 'req-1'
    assert payload['project_id'] == 'project-1'
    assert payload['task_id'] == 'task-1'
    assert 'topsecret' not in payload['message']
    assert 'abc123' not in payload['message']
    assert 'dbsecret' not in payload['message']
    assert 'prompt' not in payload


def test_logging_failure_is_non_fatal(monkeypatch):
    logger = logging.getLogger('f7004.broken')
    monkeypatch.setattr(logger, 'log', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError()))
    log_event(logger, logging.ERROR, 'test.failure', request_id='r')


@pytest.mark.parametrize('database', [
    'exesoft_factory', 'exesoft_backup_fixture_f7003_20261105',
    'exesoft_restore_f7003_20261105', 'other_database',
])
def test_database_test_guard_rejects_production_fixtures_and_unrelated_databases(
    monkeypatch, database
):
    monkeypatch.setenv('EXESOFT_TEST_DATABASE_REQUIRED', '1')
    monkeypatch.setenv('EXESOFT_TEST_POSTGRES_DB', 'exesoft_test_f7004')
    monkeypatch.setenv('POSTGRES_DB', database)
    monkeypatch.setenv('POSTGRES_APP_PASSWORD', 'not-logged')
    with pytest.raises(ValueError, match='isolated exesoft_test'):
        DBConfig.from_env()


def test_database_test_guard_accepts_only_explicit_test_database(monkeypatch):
    monkeypatch.setenv('EXESOFT_TEST_DATABASE_REQUIRED', '1')
    monkeypatch.setenv('EXESOFT_TEST_POSTGRES_DB', 'exesoft_test_f7004')
    monkeypatch.setenv('POSTGRES_DB', 'exesoft_test_f7004')
    monkeypatch.setenv('POSTGRES_APP_PASSWORD', 'not-logged')
    assert DBConfig.from_env().db == 'exesoft_test_f7004'


def test_pool_reset_clears_cached_database_configuration(monkeypatch):
    monkeypatch.setattr(db, '_pool', None)
    monkeypatch.setattr(db, '_config', object())
    db.reset_pool()
    assert db._config is None


def test_model_gateway_emits_safe_retry_and_success_logs(caplog):
    class Provider:
        def __init__(self):
            self.calls = 0

        def generate(self, request):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError('token=provider-secret')
            return ModelResponse('ok', provider='mock')

    provider = Provider()
    request = ModelRequest(messages=[{'role': 'user', 'content': 'private prompt'}],
                           metadata={'tenant_id': 'tenant-1', 'project_id': 'project-1',
                                     'task_id': 'task-1', 'run_id': 'run-1', 'agent': 'planner'})
    with caplog.at_level(logging.INFO, logger='factory.model_gateway'):
        response = ModelGateway(routes=[ProviderRoute('mock', provider)], max_retries=1).complete(request)
    assert response.content == 'ok'
    rendered = '\n'.join(JsonFormatter().format(record) for record in caplog.records)
    assert 'model.provider.timeout' in rendered
    assert 'model.request.completed' in rendered
    assert 'project-1' in rendered and request.request_id in rendered
    assert 'private prompt' not in rendered
    assert 'provider-secret' not in rendered


def test_health_reports_degraded_postgres(monkeypatch):
    monkeypatch.setattr(api_module.db, 'health_check', lambda: False)
    response = asyncio.run(api_module.health(SimpleNamespace()))
    assert response.status_code == 503
    assert json.loads(response.body) == {
        'status': 'degraded', 'checks': {'postgresql': 'unavailable'}
    }
