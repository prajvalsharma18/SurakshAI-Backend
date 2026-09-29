import os
import unittest
from unittest.mock import patch

import api_server
import config


class CorsConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = api_server.app.test_client()

    def test_development_defaults_and_explicit_override(self):
        with patch.dict(os.environ, {'APP_ENV': 'development'}, clear=True):
            self.assertEqual(
                config.get_cors_allowed_origins(),
                ['http://localhost:8083', 'http://127.0.0.1:8083'],
            )

        with patch.dict(
            os.environ,
            {
                'APP_ENV': 'development',
                'CORS_ALLOWED_ORIGINS': 'http://localhost:8090',
            },
            clear=True,
        ):
            self.assertEqual(
                config.get_cors_allowed_origins(),
                ['http://localhost:8090'],
            )

    def test_production_does_not_default_to_local_origins(self):
        with patch.dict(os.environ, {'APP_ENV': 'production'}, clear=True):
            self.assertEqual(config.get_cors_allowed_origins(), [])

    def test_allowed_origin_receives_header_on_actual_response(self):
        response = self.client.get(
            '/',
            headers={'Origin': 'http://localhost:8083'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get('Access-Control-Allow-Origin'),
            'http://localhost:8083',
        )

    def test_allowed_development_origins_and_preflight_headers(self):
        for origin in ('http://localhost:8083', 'http://127.0.0.1:8083'):
            for path, method in (
                ('/auth/login', 'POST'),
                ('/consent', 'GET'),
                ('/consent', 'POST'),
            ):
                with self.subTest(origin=origin, path=path, method=method):
                    response = self.client.options(
                        path,
                        headers={
                            'Origin': origin,
                            'Access-Control-Request-Method': method,
                            'Access-Control-Request-Headers': 'authorization,content-type',
                        },
                    )
                    self.assertIn(response.status_code, (200, 204))
                    self.assertEqual(
                        response.headers.get('Access-Control-Allow-Origin'),
                        origin,
                    )
                    self.assertIn(
                        method,
                        response.headers.get('Access-Control-Allow-Methods', ''),
                    )
                    allowed_headers = response.headers.get(
                        'Access-Control-Allow-Headers', ''
                    ).lower()
                    self.assertIn('authorization', allowed_headers)
                    self.assertIn('content-type', allowed_headers)

    def test_disallowed_origin_has_no_allow_origin_header(self):
        response = self.client.options(
            '/auth/login',
            headers={
                'Origin': 'https://not-allowed.invalid',
                'Access-Control-Request-Method': 'POST',
                'Access-Control-Request-Headers': 'authorization,content-type',
            },
        )
        self.assertIsNone(response.headers.get('Access-Control-Allow-Origin'))
