"""Development-only demo account definitions for database bootstrap."""


DEVELOPMENT_USERS = {
    'demo_personnel': {
        'user_id': 'dev-personnel-001',
        'personnel_id': 'P001',
        'username': 'demo_personnel',
        'role': 'PERSONNEL',
        'password': 'demo-personnel-password',
    },
    'mock_personnel': {
        'user_id': 'dev-personnel-900',
        'personnel_id': 'P900',
        'username': 'mock_personnel',
        'role': 'PERSONNEL',
        'password': 'SurakshAI@Personnel2026!',
    },
    'demo_welfare': {
        'user_id': 'dev-welfare-001',
        'username': 'demo_welfare',
        'role': 'WELFARE_OFFICER',
        'password': 'demo-welfare-password',
    },
    'demo_commander': {
        'user_id': 'dev-commander-001',
        'username': 'demo_commander',
        'role': 'COMMANDER',
        'password': 'demo-commander-password',
    },
    'demo_admin': {
        'user_id': 'dev-admin-001',
        'username': 'demo_admin',
        'role': 'ADMIN',
        'password': 'demo-admin-password',
    },
}
