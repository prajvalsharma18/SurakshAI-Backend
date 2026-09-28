"""Validation and normalization for the SIH personnel domain."""

import re
from datetime import datetime, timezone
from typing import TypedDict

PERSONNEL_POSTING_TYPES = {'FIELD', 'TRAINING', 'DEPLOYED', 'ADMIN', 'OTHER'}
PERSONNEL_STATUSES = {'ACTIVE', 'INACTIVE', 'ON_LEAVE', 'TEMPORARILY_INACTIVE'}


class PersonnelDirectoryItem(TypedDict, total=False):
    """Privacy-minimized personnel shape used by welfare discovery."""

    personnel_id: str
    pseudonymous_reference: str
    unit_id: str
    status: str
    posting_type: str
    created_at: str
    updated_at: str
    external_source: str
    external_personnel_id: str


class PersonnelDirectoryResponse(TypedDict):
    items: list[PersonnelDirectoryItem]
    page: int
    page_size: int
    total: int


def _now_utc_iso():
    return datetime.now(timezone.utc).isoformat()


def validate_personnel_identifier(value):
    if not isinstance(value, str):
        raise ValueError('personnel_id must be a string')
    if not re.fullmatch(r'P\d{3,}', value):
        raise ValueError('personnel_id must follow the pattern P001, P002, ...')
    return value


def normalize_personnel_record(payload):
    if not isinstance(payload, dict):
        raise ValueError('Personnel record must be a dictionary')

    required_fields = ['personnel_id', 'unit_id', 'rank', 'service_years', 'posting_type', 'status']
    missing = [field for field in required_fields if field not in payload]
    if missing:
        raise ValueError(f'Missing required personnel field(s): {", ".join(missing)}')

    record = dict(payload)
    record['personnel_id'] = validate_personnel_identifier(record['personnel_id'])
    if not isinstance(record['unit_id'], str) or not record['unit_id'].strip():
        raise ValueError('unit_id must be a non-empty string')
    if not isinstance(record['rank'], str) or not record['rank'].strip():
        raise ValueError('rank must be a non-empty string')
    if not isinstance(record['service_years'], int) or not 0 <= record['service_years'] <= 60:
        raise ValueError('service_years must be an integer between 0 and 60')
    if record['posting_type'] not in PERSONNEL_POSTING_TYPES:
        raise ValueError(f'posting_type must be one of {sorted(PERSONNEL_POSTING_TYPES)}')
    if record['status'] not in PERSONNEL_STATUSES:
        raise ValueError(f'status must be one of {sorted(PERSONNEL_STATUSES)}')
    if 'external_source' in record and record['external_source'] is not None:
        if not isinstance(record['external_source'], str) or not record['external_source'].strip():
            raise ValueError('external_source must be a non-empty string')
        record['external_source'] = record['external_source'].strip()
    if 'external_personnel_id' in record and record['external_personnel_id'] is not None:
        if not isinstance(record['external_personnel_id'], str) or not record['external_personnel_id'].strip():
            raise ValueError('external_personnel_id must be a non-empty string')
        record['external_personnel_id'] = record['external_personnel_id'].strip()
    if 'external_updated_at' in record and record['external_updated_at'] is not None and not isinstance(record['external_updated_at'], str):
        raise ValueError('external_updated_at must be a string or null')
    if 'last_hrms_sync_at' in record and record['last_hrms_sync_at'] is not None and not isinstance(record['last_hrms_sync_at'], str):
        raise ValueError('last_hrms_sync_at must be a string or null')

    record['created_at'] = record.get('created_at') or _now_utc_iso()
    record['updated_at'] = record.get('updated_at') or _now_utc_iso()
    return record
