import unittest

import api_server
from src.db.repositories.user_repository import UserRepository
from src.security.audit import get_audit_service
from src.services.user_service import UserService
from tests.user_test_support import _Database, _PersonnelLookup, install_test_users


class UserProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.audit = get_audit_service()
        self.audit._events.clear()
        self.database = _Database()
        self.service = UserService(
            UserRepository(self.database),
            _PersonnelLookup({'P001'}),
            self.audit,
        )

    def test_authentication_is_persistent_and_disabled_users_cannot_login(self):
        created = self.service.create_user(
            username='persistent_user',
            password='persistent-password',
            role='PERSONNEL',
            personnel_id='P001',
        )
        self.assertNotIn('password_hash', created)
        self.assertEqual(self.service.authenticate('persistent_user', 'persistent-password')['role'], 'PERSONNEL')
        self.assertIsNone(self.service.authenticate('persistent_user', 'wrong-password'))
        self.service.update_user(created['user_id'], status='DISABLED')
        self.assertIn('USER_DISABLED', [event['action'] for event in self.audit.list_events()])
        restarted = UserService(UserRepository(self.database), _PersonnelLookup({'P001'}), self.audit)
        self.assertIsNone(restarted.authenticate('persistent_user', 'persistent-password'))

    def test_personnel_mapping_and_roles_are_validated(self):
        with self.assertRaises(ValueError):
            self.service.create_user(
                username='bad_mapping', password='long-enough-password',
                role='PERSONNEL', personnel_id='P999',
            )
        with self.assertRaises(ValueError):
            self.service.create_user(
                username='bad_role', password='long-enough-password', role='UNKNOWN',
            )

    def test_admin_api_provisions_and_disables_users_without_leaking_secrets(self):
        install_test_users(api_server)
        client = api_server.app.test_client()
        login = client.post('/auth/login', json={'username': 'demo_admin', 'password': 'demo-admin-password'})
        self.assertEqual(login.status_code, 200, login.get_json())
        admin_headers = {'Authorization': f"Bearer {login.get_json()['access_token']}"}
        created = client.post('/admin/users', headers=admin_headers, json={
            'username': 'api_user', 'password': 'api-password-123',
            'role': 'PERSONNEL', 'personnel_id': 'P001',
        })
        self.assertEqual(created.status_code, 201)
        self.assertNotIn('password_hash', created.get_json())
        user_id = created.get_json()['user_id']
        self.assertEqual(client.get(f'/admin/users/{user_id}', headers=admin_headers).status_code, 200)
        self.assertEqual(client.patch(
            f'/admin/users/{user_id}', headers=admin_headers,
            json={'status': 'DISABLED'},
        ).status_code, 200)
        self.assertEqual(client.post(
            '/auth/login', json={'username': 'api_user', 'password': 'api-password-123'},
        ).status_code, 401)
        personnel_login = client.post('/auth/login', json={'username': 'demo_personnel', 'password': 'demo-personnel-password'})
        personnel_headers = {'Authorization': f"Bearer {personnel_login.get_json()['access_token']}"}
        self.assertEqual(client.get('/admin/users', headers=personnel_headers).status_code, 403)


if __name__ == '__main__':
    unittest.main()
