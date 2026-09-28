import unittest

import api_server
from tests.user_test_support import install_test_users
from src.services.risk_history_service import RiskHistoryService
from src.welfare.repositories import RiskPredictionRepository


class RiskCollection:
    def __init__(self):
        self.records = []

    def create_index(self, *args, **kwargs):
        return None

    def find(self, query=None):
        query = query or {}
        result = []
        for record in self.records:
            if record.get('personnel_id') != query.get('personnel_id', record.get('personnel_id')):
                continue
            date_filter = query.get('reference_date', {})
            if date_filter.get('$gte') and record['reference_date'] < date_filter['$gte']:
                continue
            if date_filter.get('$lte') and record['reference_date'] > date_filter['$lte']:
                continue
            result.append(dict(record))
        return result

    def get_records(self):
        return self.records


class RiskDatabase:
    def __init__(self):
        self.collection = RiskCollection()

    def get_collection(self, name):
        return self.collection


class RiskHistoryApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = api_server.app.test_client()
        install_test_users(api_server)
        cls.database = RiskDatabase()
        cls.repository = RiskPredictionRepository(cls.database)
        cls.service = RiskHistoryService(cls.repository, api_server.audit_service)
        api_server.risk_history_service = cls.service

    def setUp(self):
        self.repository.collection.records[:] = [
            {'prediction_id': 'p1', 'personnel_id': 'P001', 'reference_date': '2026-09-20', 'risk_category': 'LOW', 'model_version': 'v1', 'data_mode': 'OPERATIONAL_ONLY', 'created_at': '2026-09-20T10:00:00+00:00', 'feature_vector': {'secret': True}},
            {'prediction_id': 'p2', 'personnel_id': 'P001', 'reference_date': '2026-09-25', 'risk_category': 'HIGH', 'model_version': 'v1', 'data_mode': 'OPERATIONAL_ONLY', 'created_at': '2026-09-25T10:00:00+00:00'},
            {'prediction_id': 'p3', 'personnel_id': 'P002', 'reference_date': '2026-09-24', 'risk_category': 'ELEVATED', 'model_version': 'v1', 'data_mode': 'OPERATIONAL_ONLY', 'created_at': '2026-09-24T10:00:00+00:00'},
        ]
        api_server.audit_service._events.clear()

    def headers(self, username, password):
        response = self.client.post('/auth/login', json={'username': username, 'password': password})
        self.assertEqual(response.status_code, 200)
        return {'Authorization': f"Bearer {response.get_json()['access_token']}"}

    def test_history_auth_roles_and_empty_data(self):
        self.assertEqual(self.client.get('/personnel/P001/risk-history').status_code, 401)
        personnel = self.headers('demo_personnel', 'demo-personnel-password')
        welfare = self.headers('demo_welfare', 'demo-welfare-password')
        commander = self.headers('demo_commander', 'demo-commander-password')
        self.assertEqual(self.client.get('/personnel/P001/risk-history', headers=personnel).status_code, 200)
        self.assertEqual(self.client.get('/personnel/P002/risk-history', headers=personnel).status_code, 403)
        self.assertEqual(self.client.get('/personnel/P001/risk-history', headers=welfare).status_code, 200)
        self.assertEqual(self.client.get('/personnel/P001/risk-history', headers=commander).status_code, 403)
        self.repository.collection.records.clear()
        empty = self.client.get('/personnel/P001/risk-history', headers=welfare).get_json()
        self.assertEqual(empty['items'], [])
        self.assertEqual(empty['total'], 0)

    def test_history_bounds_dates_and_privacy(self):
        welfare = self.headers('demo_welfare', 'demo-welfare-password')
        response = self.client.get('/personnel/P001/risk-history?page=1&page_size=1&reference_date_from=2026-09-21&reference_date_to=2026-09-26', headers=welfare)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['items'][0]['risk_category'], 'HIGH')
        self.assertNotIn('feature_vector', response.get_json()['items'][0])
        self.assertEqual(self.client.get('/personnel/P001/risk-history?page_size=101', headers=welfare).status_code, 400)
        self.assertEqual(self.client.get('/personnel/P001/risk-history?reference_date_from=bad', headers=welfare).status_code, 400)
        self.assertEqual(self.client.get('/personnel/P001/risk-history?reference_date_from=2026-09-27&reference_date_to=2026-09-20', headers=welfare).status_code, 400)

    def test_summary_counts_unique_personnel_from_latest_predictions(self):
        welfare = self.headers('demo_welfare', 'demo-welfare-password')
        admin = self.headers('demo_admin', 'demo-admin-password')
        commander = self.headers('demo_commander', 'demo-commander-password')
        response = self.client.get('/welfare/risk-summary', headers=welfare)
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body['total_personnel'], 2)
        self.assertEqual(body['risk_distribution'], {'LOW': 0, 'ELEVATED': 1, 'HIGH': 1})
        self.assertEqual(self.client.get('/welfare/risk-summary', headers=admin).status_code, 200)
        self.assertEqual(self.client.get('/welfare/risk-summary', headers=commander).status_code, 403)
        actions = [event['action'] for event in api_server.audit_service.list_events()]
        self.assertIn('WELFARE_RISK_SUMMARY_VIEW', actions)


if __name__ == '__main__':
    unittest.main()
