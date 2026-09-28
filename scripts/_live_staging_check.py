"""Run guarded staging population and live HTTP verification without cleanup."""

import collections
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import get_database_name, validate_synthetic_seed_configuration
from scripts.seed_staging_dataset import SEED_COLLECTIONS, _batch_id, _collection, seed_staging_dataset
from src.db.mongodb import get_database


def request(base_url, method, path, *, token=None, data=None, timeout=90):
    headers = {}
    body = None
    if data is not None:
        body = json.dumps(data).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    if token:
        headers['Authorization'] = f'Bearer {token}'
    call = Request(base_url + path, data=body, headers=headers, method=method)
    try:
        response = urlopen(call, timeout=timeout)
    except HTTPError as error:
        return error.code, error.read(), dict(error.headers)
    with response:
        return response.status, response.read(), dict(response.headers)


def expect(base_url, label, path, token, expected):
    result = request(base_url, 'GET', path, token=token)
    if result[0] != expected:
        raise RuntimeError(f'{label}: expected HTTP {expected}, received {result[0]}')
    print(f'{label}: HTTP {result[0]}', flush=True)
    return result


def login(base_url, username, password, role, personnel_id=None):
    status, body, _ = request(
        base_url,
        'POST',
        '/auth/login',
        data={'username': username, 'password': password},
    )
    if status != 200:
        raise RuntimeError(f'{role} login failed with HTTP {status}')
    payload = json.loads(body)
    user = payload.get('user') or {}
    if user.get('role') != role:
        raise RuntimeError(f'{role} login returned an unexpected role')
    if personnel_id is not None and user.get('personnel_id') != personnel_id:
        raise RuntimeError('Staging personnel login did not map to the requested personnel')
    token = payload.get('access_token')
    if not token:
        raise RuntimeError(f'{role} login did not issue an access token')
    print(f'Login {role}: verified; personnel mapping checked={personnel_id is not None}', flush=True)
    return token


def verify_pdf(result, consent_state, personnel_id):
    if result[0] != 200:
        return False
    if not result[1].startswith(b'%PDF'):
        raise RuntimeError(f'{consent_state} welfare report is not a PDF')
    if result[2].get('Content-Type', '').split(';')[0] != 'application/pdf':
        raise RuntimeError(f'{consent_state} welfare report has an unexpected content type')
    if not importlib.util.find_spec('pypdf'):
        print(f'Report ({consent_state}): PDF generated; text privacy check unavailable (pypdf missing)', flush=True)
        return True
    from pypdf import PdfReader

    text = '\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(result[1])).pages)
    if personnel_id in text:
        raise RuntimeError('Welfare report exposed an internal personnel identifier')
    if consent_state == 'without current consent' and 'unavailable under current consent' not in text.lower():
        raise RuntimeError('No-consent welfare report omitted its consent-aware wellness notice')
    print(f'Report ({consent_state}): generated; privacy checks passed', flush=True)
    return True


def verify_live_api(personnel_ids, passwords, provider_configured):
    from werkzeug.serving import make_server
    import api_server

    server = make_server('127.0.0.1', 0, api_server.app, threaded=True)
    base_url = f'http://127.0.0.1:{server.server_port}'
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    own_id, other_id, no_consent_id = personnel_ids[0], personnel_ids[1], personnel_ids[21]
    try:
        personnel_token = login(
            base_url,
            f'stg_personnel_{own_id.lower()}',
            passwords['PERSONNEL'],
            'PERSONNEL',
            own_id,
        )
        welfare_token = login(
            base_url,
            'stg_welfare_officer',
            passwords['WELFARE_OFFICER'],
            'WELFARE_OFFICER',
        )
        commander_token = login(
            base_url,
            'stg_commander_001',
            passwords['COMMANDER'],
            'COMMANDER',
        )
        admin_token = login(
            base_url,
            'stg_admin_001',
            passwords['ADMIN'],
            'ADMIN',
        )

        expect(base_url, 'PERSONNEL consent', '/consent', personnel_token, 200)
        for label, path in (
            ('PERSONNEL own risk prediction', f'/personnel/{own_id}/risk-prediction'),
            ('PERSONNEL own risk history', f'/personnel/{own_id}/risk-history'),
            ('PERSONNEL own SHAP explanation', f'/personnel/{own_id}/risk-explanation'),
            ('PERSONNEL own wellness', '/personnel/me/wellness'),
            ('PERSONNEL own alerts', f'/personnel/{own_id}/alerts'),
        ):
            result = expect(base_url, label, path, personnel_token, 200)
            if 'SHAP' in label and not json.loads(result[1]).get('explanation'):
                raise RuntimeError('PERSONNEL SHAP endpoint returned no explanation')

        for label, path in (
            ('PERSONNEL cross-personnel prediction denied', f'/personnel/{other_id}/risk-prediction'),
            ('PERSONNEL cross-personnel history denied', f'/personnel/{other_id}/risk-history'),
            ('PERSONNEL cross-personnel explanation denied', f'/personnel/{other_id}/risk-explanation'),
            ('PERSONNEL cross-personnel wellness denied', f'/personnel/{other_id}/wellness'),
            ('PERSONNEL cross-personnel alerts denied', f'/personnel/{other_id}/alerts'),
            ('PERSONNEL cross-personnel report denied', f'/personnel/{other_id}/welfare-report'),
            ('PERSONNEL dashboard denied', '/welfare/dashboard/summary'),
        ):
            expect(base_url, label, path, personnel_token, 403)

        welfare_endpoints = (
            ('WELFARE_OFFICER directory', '/welfare/personnel?page=1&page_size=50', 200),
            ('WELFARE_OFFICER dashboard', '/welfare/dashboard/summary', 200),
            ('WELFARE_OFFICER risk summary', '/welfare/risk-summary', 200),
            ('WELFARE_OFFICER alerts', '/welfare/alerts', 200),
            ('WELFARE_OFFICER consented wellness', f'/personnel/{own_id}/wellness', 200),
            ('WELFARE_OFFICER no-consent wellness denial', f'/personnel/{no_consent_id}/wellness', 403),
            ('WELFARE_OFFICER risk prediction', f'/personnel/{own_id}/risk-prediction', 200),
            ('WELFARE_OFFICER risk history', f'/personnel/{own_id}/risk-history', 200),
            ('WELFARE_OFFICER SHAP', f'/personnel/{own_id}/risk-explanation', 200),
            ('WELFARE_OFFICER personnel alerts', f'/personnel/{own_id}/alerts', 200),
        )
        for label, path, expected in welfare_endpoints:
            result = expect(base_url, label, path, welfare_token, expected)
            if label.endswith('SHAP') and not json.loads(result[1]).get('explanation'):
                raise RuntimeError('WELFARE_OFFICER SHAP endpoint returned no explanation')

        recommendation = request(
            base_url,
            'GET',
            f'/personnel/{own_id}/welfare-recommendations',
            token=welfare_token,
        )
        if recommendation[0] == 200:
            items = json.loads(recommendation[1]).get('recommendations', [])
            if not items or any(not item.get('sources') for item in items):
                raise RuntimeError('Recommendation endpoint returned no source-grounded recommendations')
            print(f'RAG/LLM recommendation: live verified; grounded items={len(items)}', flush=True)
        elif not provider_configured and recommendation[0] == 503:
            print('RAG/LLM: not verified; external provider is not configured (HTTP 503)', flush=True)
        else:
            print(f'RAG/LLM: not verified; service returned HTTP {recommendation[0]}', flush=True)

        for personnel_id, consent_state in (
            (own_id, 'with wellness consent'),
            (no_consent_id, 'without current consent'),
        ):
            result = request(
                base_url,
                'GET',
                f'/personnel/{personnel_id}/welfare-report',
                token=welfare_token,
                timeout=120,
            )
            if result[0] == 200:
                verify_pdf(result, consent_state, personnel_id)
            else:
                print(f'Report ({consent_state}): not verified; HTTP {result[0]}', flush=True)

        expect(base_url, 'ADMIN user administration', '/admin/users', admin_token, 200)
        expect(base_url, 'WELFARE_OFFICER user administration denied', '/admin/users', welfare_token, 403)
        expect(base_url, 'COMMANDER operational summary', '/operational/summary', commander_token, 200)
        expect(base_url, 'COMMANDER welfare dashboard denied', '/welfare/dashboard/summary', commander_token, 403)
        expect(base_url, 'COMMANDER individual risk denied', f'/personnel/{own_id}/risk-prediction', commander_token, 403)
        print('RBAC: role policy checks completed.', flush=True)
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()


def main():
    validate_synthetic_seed_configuration()
    if get_database_name() != 'surakshai_staging':
        raise RuntimeError('Refusing any database other than surakshai_staging')
    database = get_database()
    if database is None:
        raise RuntimeError('MongoDB is not configured')
    database.client.admin.command('ping')
    batch_id = _batch_id()
    print(f'Staging MongoDB reachable; database={get_database_name()}; batch={batch_id}', flush=True)

    report = seed_staging_dataset(database=database)
    personnel_ids = [f'P{900 + index:03d}' for index in range(report['personnel_count'])]
    print(
        'Seed complete: '
        f"personnel={report['personnel_count']}; users={report['users_created']}; "
        f"roles={report['users_by_role']}; operational_records={sum(report['operational_records'].values())}; "
        f"wellness={report['wellness_assessments']}; risk_predictions={report['risk_predictions']}; "
        f"alerts={report['alerts']}; interventions={report['interventions']}",
        flush=True,
    )

    passwords = {
        'PERSONNEL': os.environ['SURAKSHAI_STAGING_PERSONNEL_PASSWORD'],
        'WELFARE_OFFICER': os.environ['SURAKSHAI_STAGING_WELFARE_PASSWORD'],
        'COMMANDER': os.environ['SURAKSHAI_STAGING_COMMANDER_PASSWORD'],
        'ADMIN': os.environ['SURAKSHAI_STAGING_ADMIN_PASSWORD'],
    }
    provider_configured = all(
        os.getenv(key)
        for key in ('SURAKSHAI_LLM_BASE_URL', 'SURAKSHAI_LLM_MODEL', 'SURAKSHAI_LLM_API_KEY')
    )
    print(f'LLM provider configured={provider_configured}; provider details withheld.', flush=True)
    verify_live_api(personnel_ids, passwords, provider_configured)

    for name in SEED_COLLECTIONS:
        count = _collection(database, name).count_documents({'seed_batch_id': batch_id})
        print(f'{name} batch count={count}', flush=True)
        if count == 0 and name in {
            'personnel_identity', 'users', 'duty_records', 'workload_records',
            'risk_predictions', 'consents', 'wellness_assessments',
        }:
            raise RuntimeError(f'Required staging collection {name} is empty')

    role_counts = collections.Counter(
        user.get('role')
        for user in database.get_collection('users').find(
            {'seed_batch_id': batch_id},
            {'_id': 0, 'role': 1},
        )
    )
    print(f'Persisted staging role counts={dict(role_counts)}', flush=True)
    if role_counts != {
        'PERSONNEL': 30,
        'WELFARE_OFFICER': 3,
        'COMMANDER': 2,
        'ADMIN': 2,
    }:
        raise RuntimeError('Persisted staging user-role counts failed verification')
    print('FINAL DATABASE STATE: STAGING DATABASE LEFT POPULATED', flush=True)


if __name__ == '__main__':
    main()
