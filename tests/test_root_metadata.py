import importlib
import os
import sys
import unittest


class RootMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['JWT_SECRET_KEY'] = 'root-metadata-test-secret'
        sys.modules.pop('api_server', None)
        cls.api_server = importlib.import_module('api_server')
        cls.client = cls.api_server.app.test_client()

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop('api_server', None)
        os.environ.pop('JWT_SECRET_KEY', None)

    def test_root_describes_current_surakshai_api(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        serialized = str(payload)

        self.assertEqual(payload['api'], 'SURAKSHAI Personnel Welfare Intelligence API')
        self.assertEqual(payload['status'], 'active')
        self.assertEqual(payload['model_info'], {
            'model': 'XGBoost',
            'model_version': 'surakshai-risk-v0.1',
            'feature_count': 31,
            'classes': ['LOW', 'ELEVATED', 'HIGH'],
            'training_data': 'Synthetic development dataset',
        })
        for stale in (
            'Employee Stress Management System',
            'Random Forest Classifier',
            '81.51%',
            'Baseline/Normal',
            'Stressed',
            'Amusement/Relaxed',
            'Meditation',
            '/predict',
            '/employee',
            'legacy /alerts',
        ):
            self.assertNotIn(stale, serialized)

        endpoints = payload['endpoints']
        for route in (
            'GET /personnel/<personnel_id>/risk-prediction',
            'GET /personnel/<personnel_id>/risk-explanation',
            'GET /personnel/<personnel_id>/welfare-recommendations',
            'GET /welfare/alerts',
            'POST /welfare/alerts/evaluate/<personnel_id>',
            'POST /welfare/alerts/<alert_id>/acknowledge',
            'POST /welfare/alerts/<alert_id>/review',
            'POST /welfare/alerts/<alert_id>/intervention',
            'POST /welfare/alerts/<alert_id>/follow-up',
            'POST /welfare/alerts/<alert_id>/resolve',
            'POST /welfare/alerts/<alert_id>/dismiss',
            'PATCH /welfare/interventions/<intervention_id>',
        ):
            self.assertIn(route, endpoints)

    def test_root_catalog_entries_are_registered_routes(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        registered = {
            (rule.rule, rule.endpoint, method)
            for rule in self.api_server.app.url_map.iter_rules()
            for method in rule.methods
        }
        for route in response.get_json()['endpoints']:
            methods, path = route.split(' ', 1)
            endpoint = next(
                rule.endpoint for rule in self.api_server.app.url_map.iter_rules()
                if rule.rule == path and all(
                    (path, rule.endpoint, method) in registered
                    for method in methods.split('/')
                )
            )
            self.assertTrue(all((path, endpoint, method) in registered for method in methods.split('/')))


if __name__ == '__main__':
    unittest.main()
