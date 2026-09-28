import importlib
import os
import unittest

import api_server
from src.services.welfare_dashboard_service import WelfareDashboardService
from tests.user_test_support import install_test_users


class DashboardRepo:
    def __init__(self):
        self.personnel = ['P001', 'P001', 'P002']
        self.risk = {'risk_distribution': {'LOW': 1, 'ELEVATED': 0, 'HIGH': 1}}
        self.alerts = [
            {'status': 'OPEN', 'severity': 'ATTENTION', 'personnel_id': 'P001'},
            {'status': 'RESOLVED', 'severity': 'PRIORITY', 'personnel_id': 'P001'},
        ]
        self.interventions = [
            {'status': 'PLANNED', 'scheduled_follow_up': '2026-10-01', 'personnel_id': 'P001', 'notes': 'private'},
            {'status': 'COMPLETED', 'scheduled_follow_up': '2026-09-01', 'personnel_id': 'P002'},
        ]

    def count_authorized(self):
        return len(set(self.personnel))


class RiskRepo:
    def __init__(self, source):
        self.source = source

    def get_summary(self):
        return self.source.risk


class AlertRepo:
    def __init__(self, source):
        self.source = source

    def dashboard_summary(self):
        active = [record for record in self.source.alerts if record['status'] in {'OPEN', 'ACKNOWLEDGED', 'UNDER_REVIEW', 'ACTION_PLANNED', 'FOLLOW_UP'}]
        return {
            'open': len(active),
            'attention': sum(record['severity'] == 'ATTENTION' for record in active),
            'priority': sum(record['severity'] == 'PRIORITY' for record in active),
        }


class InterventionRepo:
    def __init__(self, source):
        self.source = source

    def dashboard_summary(self):
        active = [record for record in self.source.interventions if record['status'] in {'PLANNED', 'IN_PROGRESS'}]
        return {'active': len(active), 'follow_up': sum(record.get('scheduled_follow_up') is not None for record in active)}


class WelfareDashboardApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global api_server
        cls.previous_secret = os.environ.get('JWT_SECRET_KEY')
        os.environ['JWT_SECRET_KEY'] = 'welfare-dashboard-test-secret'
        api_server = importlib.import_module('api_server')
        cls.client = api_server.app.test_client()
        install_test_users(api_server)
        cls.source = DashboardRepo()
        cls.service = WelfareDashboardService(
            personnel_repository=cls.source,
            risk_repository=RiskRepo(cls.source),
            alert_repository=AlertRepo(cls.source),
            intervention_repository=InterventionRepo(cls.source),
            audit_service=api_server.audit_service,
        )
        api_server.welfare_dashboard_service = cls.service

    @classmethod
    def tearDownClass(cls):
        if cls.previous_secret is None:
            os.environ.pop('JWT_SECRET_KEY', None)
        else:
            os.environ['JWT_SECRET_KEY'] = cls.previous_secret

    def setUp(self):
        api_server.audit_service._events.clear()

    def headers(self, username, password):
        response = self.client.post('/auth/login', json={'username': username, 'password': password})
        self.assertEqual(response.status_code, 200)
        return {'Authorization': f"Bearer {response.get_json()['access_token']}"}

    def test_authentication_and_role_boundaries(self):
        self.assertEqual(self.client.get('/welfare/dashboard/summary').status_code, 401)
        welfare = self.headers('demo_welfare', 'demo-welfare-password')
        admin = self.headers('demo_admin', 'demo-admin-password')
        commander = self.headers('demo_commander', 'demo-commander-password')
        personnel = self.headers('demo_personnel', 'demo-personnel-password')
        self.assertEqual(self.client.get('/welfare/dashboard/summary', headers=welfare).status_code, 200)
        self.assertEqual(self.client.get('/welfare/dashboard/summary', headers=admin).status_code, 200)
        self.assertEqual(self.client.get('/welfare/dashboard/summary', headers=commander).status_code, 403)
        self.assertEqual(self.client.get('/welfare/dashboard/summary', headers=personnel).status_code, 403)

    def test_aggregate_semantics_privacy_and_audit(self):
        welfare = self.headers('demo_welfare', 'demo-welfare-password')
        response = self.client.get('/welfare/dashboard/summary', headers=welfare)
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body['personnel']['total_authorized'], 2)
        self.assertEqual(body['risk'], {'LOW': 1, 'ELEVATED': 0, 'HIGH': 1})
        self.assertEqual(body['alerts'], {'open': 1, 'attention': 1, 'priority': 0})
        self.assertEqual(body['interventions'], {'active': 1, 'follow_up': 1})
        self.assertNotIn('personnel_id', str(body))
        self.assertNotIn('notes', str(body))
        events = api_server.audit_service.list_events()
        self.assertEqual(events[-1]['action'], 'WELFARE_DASHBOARD_SUMMARY_VIEW')
        self.assertNotIn('risk', events[-1])

    def test_empty_sources_return_zero_metrics(self):
        self.source.personnel = []
        self.source.risk = {'risk_distribution': {}}
        self.source.alerts = []
        self.source.interventions = []
        welfare = self.headers('demo_welfare', 'demo-welfare-password')
        body = self.client.get('/welfare/dashboard/summary', headers=welfare).get_json()
        self.assertEqual(body['personnel']['total_authorized'], 0)
        self.assertEqual(body['risk'], {'LOW': 0, 'ELEVATED': 0, 'HIGH': 0})
        self.assertEqual(body['alerts'], {'open': 0, 'attention': 0, 'priority': 0})
        self.assertEqual(body['interventions'], {'active': 0, 'follow_up': 0})


if __name__ == '__main__':
    unittest.main()
