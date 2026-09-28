import unittest

import api_server
from src.db.repositories.personnel_repository import PersonnelRepository
from src.services.personnel_service import PersonnelService
from tests.user_test_support import install_test_users


class FakeCollection:
    def __init__(self):
        self.records = []

    def create_index(self, *args, **kwargs):
        return None

    def find(self, query=None):
        return [dict(record) for record in self.records]

    def find_one(self, query):
        for record in self.records:
            if all(record.get(key) == value for key, value in query.items()):
                return dict(record)
        return None

    def insert_one(self, record):
        self.records.append(dict(record))


class FakeDatabase:
    def __init__(self):
        self.collection = FakeCollection()

    def get_collection(self, name):
        return self.collection


class PersonnelDirectoryApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = api_server.app.test_client()
        install_test_users(api_server)
        cls.database = FakeDatabase()
        cls.service = PersonnelService(
            repository=PersonnelRepository(cls.database),
            audit_service=api_server.audit_service,
        )
        api_server.personnel_service = cls.service

    def setUp(self):
        self.service.repository.collection.records.clear()
        api_server.audit_service._events.clear()
        for personnel_id, unit_id in (('P001', 'UNIT-A'), ('P002', 'UNIT-A'), ('P003', 'UNIT-B')):
            self.service.create_personnel({
                'personnel_id': personnel_id,
                'unit_id': unit_id,
                'rank': 'Officer',
                'service_years': 5,
                'posting_type': 'FIELD',
                'status': 'ACTIVE',
                'password_hash': 'must-not-leak',
                'secret': 'must-not-leak',
            })
        api_server.audit_service._events.clear()

    def headers(self, username, password):
        response = self.client.post('/auth/login', json={'username': username, 'password': password})
        self.assertEqual(response.status_code, 200)
        return {'Authorization': f"Bearer {response.get_json()['access_token']}"}

    def test_authentication_and_role_boundaries(self):
        self.assertEqual(self.client.get('/welfare/personnel').status_code, 401)
        welfare = self.headers('demo_welfare', 'demo-welfare-password')
        admin = self.headers('demo_admin', 'demo-admin-password')
        commander = self.headers('demo_commander', 'demo-commander-password')
        personnel = self.headers('demo_personnel', 'demo-personnel-password')
        self.assertEqual(self.client.get('/welfare/personnel', headers=welfare).status_code, 200)
        self.assertEqual(self.client.get('/welfare/personnel', headers=admin).status_code, 200)
        self.assertEqual(self.client.get('/welfare/personnel', headers=personnel).status_code, 403)
        self.assertEqual(self.client.get('/welfare/personnel', headers=commander).status_code, 403)

    def test_pagination_search_and_validation(self):
        welfare = self.headers('demo_welfare', 'demo-welfare-password')
        response = self.client.get('/welfare/personnel?page=2&page_size=2', headers=welfare)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['page'], 2)
        self.assertEqual(response.get_json()['page_size'], 2)
        self.assertEqual(response.get_json()['total'], 3)
        self.assertEqual(len(response.get_json()['items']), 1)
        search = self.client.get('/welfare/personnel?search=P002', headers=welfare)
        self.assertEqual(search.status_code, 200)
        self.assertEqual([item['personnel_id'] for item in search.get_json()['items']], ['P002'])
        self.assertEqual(self.client.get('/welfare/personnel?page_size=101', headers=welfare).status_code, 400)
        self.assertEqual(self.client.get('/welfare/personnel?search=$gt', headers=welfare).status_code, 400)

    def test_resource_authorization_privacy_and_audit(self):
        welfare = self.headers('demo_welfare', 'demo-welfare-password')
        personnel = self.headers('demo_personnel', 'demo-personnel-password')
        commander = self.headers('demo_commander', 'demo-commander-password')
        item = self.client.get('/welfare/personnel/P001', headers=welfare)
        self.assertEqual(item.status_code, 200)
        body = item.get_json()
        self.assertEqual(body['personnel_id'], 'P001')
        self.assertNotIn('password_hash', body)
        self.assertNotIn('secret', body)
        self.assertNotIn('recommendations', body)
        self.assertNotIn('shap', body)
        self.assertEqual(self.client.get('/welfare/personnel/P001', headers=personnel).status_code, 200)
        self.assertEqual(self.client.get('/welfare/personnel/P002', headers=personnel).status_code, 403)
        self.assertEqual(self.client.get('/welfare/personnel/P001', headers=commander).status_code, 403)
        actions = [event['action'] for event in api_server.audit_service.list_events()]
        self.assertIn('WELFARE_PERSONNEL_VIEW', actions)
        self.assertIn('ACCESS_DENIED', actions)

    def test_missing_record_returns_not_found(self):
        welfare = self.headers('demo_welfare', 'demo-welfare-password')
        self.assertEqual(self.client.get('/welfare/personnel/P999', headers=welfare).status_code, 404)


if __name__ == '__main__':
    unittest.main()
