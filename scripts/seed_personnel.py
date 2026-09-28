"""Generate a small synthetic personnel set for development and tests."""

from src.db.repositories.personnel_repository import PersonnelRepository
from src.schemas.personnel import normalize_personnel_record


def seed_personnel_records(database=None, count=12):
    repo = PersonnelRepository(database=database)
    sample_units = ['U-01', 'U-02', 'U-05', 'U-07', 'U-12']
    sample_ranks = ['JCO', 'Naik', 'Havildar', 'Captain', 'Subedar']
    posting_types = ['FIELD', 'TRAINING', 'DEPLOYED', 'ADMIN']
    statuses = ['ACTIVE', 'ON_LEAVE', 'ACTIVE', 'TEMPORARILY_INACTIVE']

    generated = []
    for index in range(1, count + 1):
        record = normalize_personnel_record({
            'personnel_id': f'P{index:03d}',
            'unit_id': sample_units[index % len(sample_units)],
            'rank': sample_ranks[index % len(sample_ranks)],
            'service_years': 1 + (index * 2) % 15,
            'posting_type': posting_types[index % len(posting_types)],
            'status': statuses[index % len(statuses)],
        })
        generated.append(repo.create(record))
    return generated


if __name__ == '__main__':
    import os
    from config import get_app_env

    if get_app_env() == 'production':
        raise SystemExit('Synthetic personnel seeding is disabled in production.')
    if os.getenv('SURAKSHAI_ENABLE_DEV_USER_SEED') != '1':
        raise SystemExit('Set SURAKSHAI_ENABLE_DEV_USER_SEED=1 to run the development-only personnel seed.')
    seed_personnel_records()
