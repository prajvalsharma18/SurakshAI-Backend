import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt

import api_server
import config
from config import HRMS_SYNC_RATE_LIMIT, LOGIN_RATE_LIMIT
from src.security.audit import AuditService
from src.security.rate_limit import reset_rate_limits
from src.security.token import TokenError, TokenManager


class SecurityHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = api_server.app.test_client()

    def setUp(self):
        reset_rate_limits()

    def tearDown(self):
        reset_rate_limits()

    def test_security_headers_and_cors_allowlist(self):
        allowed = config.get_cors_allowed_origins()
        chosen = allowed[0] if allowed else None
        response = self.client.get('/', headers={'Origin': chosen} if chosen else {})
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
        self.assertEqual(response.headers['Referrer-Policy'], 'strict-origin-when-cross-origin')
        self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
        if chosen:
            self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), chosen)
        denied = self.client.get('/', headers={'Origin': 'https://not-allowed.invalid'})
        self.assertIsNone(denied.headers.get('Access-Control-Allow-Origin'))

    def test_generic_login_failure_and_rate_limit(self):
        class RejectingUsers:
            def authenticate(self, username, password):
                return None

        with patch.object(api_server, 'user_service', RejectingUsers()), patch.object(api_server, '_ensure_development_users'):
            malformed = self.client.post('/auth/login', json=[])
            self.assertEqual(malformed.status_code, 401)
            failures = [
                self.client.post('/auth/login', json={'username': 'unknown', 'password': 'wrong'})
                for _ in range(max(0, LOGIN_RATE_LIMIT - 2))
            ]
            self.assertTrue(all(response.status_code == 401 for response in failures))
            self.assertEqual(malformed.get_json(), failures[0].get_json())
            final_allowed = self.client.post('/auth/login', json={'username': 'unknown', 'password': 'wrong'})
            self.assertEqual(final_allowed.status_code, 401)
            limited = self.client.post('/auth/login', json={'username': 'unknown', 'password': 'wrong'})
            self.assertEqual(limited.status_code, 429)
            self.assertIn('Retry-After', limited.headers)

    def test_request_body_limit_has_safe_error(self):
        original = api_server.app.config['MAX_CONTENT_LENGTH']
        try:
            api_server.app.config['MAX_CONTENT_LENGTH'] = 8
            response = self.client.post('/auth/login', data='x' * 32, content_type='application/json')
            self.assertEqual(response.status_code, 413)
            self.assertEqual(response.get_json(), {'error': 'Request Entity Too Large'})
        finally:
            api_server.app.config['MAX_CONTENT_LENGTH'] = original

    def test_hrms_route_is_admin_only_and_rate_limited(self):
        class StubSyncService:
            def sync(self, adapter, actor=None):
                return {'processed': 1, 'created': 0, 'updated': 0, 'unchanged': 1, 'deactivated': 0,
                        'conflicts': [], 'results': []}

        test_secret = 'security-hardening-route-test-secret-32bytes'
        record = {
            'external_personnel_id': 'HRMS-TEST-1', 'unit_id': 'UNIT-A', 'rank': 'Officer',
            'service_years': 1, 'posting_type': 'FIELD', 'status': 'ACTIVE',
        }
        with patch.dict(os.environ, {'JWT_SECRET_KEY': test_secret}):
            admin_token, _ = TokenManager().create_access_token({'user_id': 'admin-test', 'username': 'admin', 'role': 'ADMIN'})
            welfare_token, _ = TokenManager().create_access_token({'user_id': 'welfare-test', 'username': 'welfare', 'role': 'WELFARE_OFFICER'})
            headers = {'Authorization': f'Bearer {admin_token}'}
            with patch.object(api_server, '_get_hrms_sync_service', return_value=StubSyncService()):
                denied = self.client.post('/admin/integrations/hrms/sync', json={'records': [record]}, headers={'Authorization': f'Bearer {welfare_token}'})
                self.assertEqual(denied.status_code, 403)
                allowed = [
                    self.client.post('/admin/integrations/hrms/sync', json={'records': [record]}, headers=headers)
                    for _ in range(HRMS_SYNC_RATE_LIMIT)
                ]
                self.assertTrue(all(response.status_code == 200 for response in allowed))
                limited = self.client.post('/admin/integrations/hrms/sync', json={'records': [record]}, headers=headers)
                self.assertEqual(limited.status_code, 429)

    def test_unexpected_errors_are_sanitized(self):
        with api_server.app.test_request_context('/security-test'):
            response, status = api_server.handle_unexpected_error(
                RuntimeError('mongodb://user:secret@host/path C:\\private\\file')
            )
            self.assertEqual(status, 500)
            self.assertEqual(response.get_json(), {'error': 'Internal server error'})
            self.assertNotIn('mongodb://', response.get_data(as_text=True))
            self.assertNotIn('private', response.get_data(as_text=True))

    def test_jwt_expiration_signature_issuer_and_audience(self):
        manager = TokenManager(secret_key='test-security-secret-with-over-32-bytes', expires_minutes=1)
        identity = {'user_id': 'u1', 'username': 'tester', 'role': 'ADMIN'}
        token, _ = manager.create_access_token(identity)
        self.assertEqual(manager.decode_access_token(token)['user_id'], 'u1')
        with self.assertRaises(TokenError):
            manager.decode_access_token('malformed')
        wrong_signature = jwt.encode({'sub': 'u1'}, 'different-secret-with-over-32-bytes', algorithm='HS256')
        with self.assertRaises(TokenError):
            manager.decode_access_token(wrong_signature)
        expired_claims = {
            'sub': 'u1', 'username': 'tester', 'role': 'ADMIN', 'type': 'access',
            'iat': datetime.now(timezone.utc) - timedelta(minutes=2),
            'exp': datetime.now(timezone.utc) - timedelta(minutes=1),
            'iss': manager.issuer, 'aud': manager.audience,
        }
        expired = jwt.encode(expired_claims, manager.secret_key, algorithm='HS256')
        with self.assertRaises(TokenError):
            manager.decode_access_token(expired)
        wrong_audience = dict(expired_claims, exp=datetime.now(timezone.utc) + timedelta(minutes=1), aud='other')
        bad_audience = jwt.encode(wrong_audience, manager.secret_key, algorithm='HS256')
        with self.assertRaises(TokenError):
            manager.decode_access_token(bad_audience)

    def test_production_disables_debug_and_rejects_unsafe_configuration(self):
        safe_env = {
            'APP_ENV': 'production',
            'DEBUG': 'true',
            'JWT_SECRET_KEY': 'jwt-secret-value-that-is-at-least-32-bytes',
            'PSEUDONYMIZATION_SECRET': 'pseudonym-secret-value-at-least-32-bytes',
            'JWT_ALGORITHM': 'HS256',
            'JWT_ACCESS_TOKEN_EXPIRES_MINUTES': '60',
            'CORS_ALLOWED_ORIGINS': 'https://welfare.example.gov',
            'MONGODB_URI': 'mongodb://db.example.gov/personnel',
            'MONGODB_TLS': 'true',
        }
        with patch.dict(os.environ, safe_env, clear=False):
            self.assertFalse(config.get_debug_mode())
            config.validate_production_configuration()
        unsafe_env = dict(safe_env, CORS_ALLOWED_ORIGINS='*')
        with patch.dict(os.environ, unsafe_env, clear=False):
            with self.assertRaises(ValueError):
                config.validate_production_configuration()
        placeholder_env = dict(
            safe_env,
            JWT_SECRET_KEY='replace-with-a-long-random-development-value',
        )
        with patch.dict(os.environ, placeholder_env, clear=False):
            with self.assertRaises(ValueError):
                config.validate_production_configuration()

    def test_production_cannot_bootstrap_development_users(self):
        previous_flag = api_server._development_bootstrap_complete

        class MustNotBootstrap:
            def bootstrap_development_users(self, definitions):
                raise AssertionError('production attempted to seed demo users')

        try:
            api_server._development_bootstrap_complete = False
            with patch.dict(os.environ, {'APP_ENV': 'production', 'SURAKSHAI_ENABLE_DEV_USER_SEED': '1'}):
                with patch.object(api_server, 'user_service', MustNotBootstrap()):
                    api_server._ensure_development_users()
        finally:
            api_server._development_bootstrap_complete = previous_flag

    def test_audit_drops_sensitive_fields_and_values(self):
        audit = AuditService()
        event = audit.record_event(
            action='TEST', result='SUCCESS', password='never-store',
            jwt_token='never-store', mongodb_uri='mongodb://secret',
            safe_count=3, unsafe_message='Bearer token-value',
        )
        rendered = str(event)
        for forbidden in ('never-store', 'mongodb://', 'token-value'):
            self.assertNotIn(forbidden, rendered)
        self.assertEqual(event['safe_count'], 3)


if __name__ == '__main__':
    unittest.main()
