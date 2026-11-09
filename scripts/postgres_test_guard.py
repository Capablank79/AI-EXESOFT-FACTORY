"""Fail-closed identity check shared by standalone PostgreSQL test scripts."""
import os


EXPECTED_DATABASE = 'exesoft_test_f7004'
EXPECTED_TENANT_ID = 'b66f93cd-2f3a-4215-a2ac-93a59a84b4d2'
EXPECTED_TENANT = ('EXF-F7-004 synthetic test tenant', 'exf-f7-004-synthetic-test', True)


def require_isolated_test_database():
    if (os.environ.get('POSTGRES_DB') != EXPECTED_DATABASE
            or os.environ.get('EXESOFT_TEST_POSTGRES_DB') != EXPECTED_DATABASE
            or os.environ.get('EXESOFT_BACKUP_TEST_ENABLED') != '0'):
        raise SystemExit(
            'Refusing PostgreSQL test writes: configure only exesoft_test_f7004 '
            'and disable backup/restore tests.'
        )

    os.environ['EXESOFT_TEST_DATABASE_REQUIRED'] = '1'
    from infrastructure import db

    db.reset_pool()
    try:
        if db.get_config().db != EXPECTED_DATABASE:
            raise RuntimeError('configured database identity mismatch')
        with db.get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT current_database()')
                actual_database = cursor.fetchone()[0]
                if actual_database != EXPECTED_DATABASE:
                    raise RuntimeError('connected database identity mismatch')
                cursor.execute(
                    'SELECT name, slug, is_active FROM tenants WHERE id = %s',
                    (EXPECTED_TENANT_ID,),
                )
                tenant = cursor.fetchone()
        if tenant != EXPECTED_TENANT:
            raise RuntimeError('synthetic tenant marker mismatch')
    except Exception as exc:
        db.reset_pool()
        raise SystemExit(
            f'Refusing PostgreSQL test writes: identity verification failed ({type(exc).__name__}).'
        ) from None

