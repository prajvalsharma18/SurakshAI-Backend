"""Safely remove only one synthetic staging batch from a guarded dev database."""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.seed_staging_dataset import reset_staging_dataset


if __name__ == '__main__':
    deleted = reset_staging_dataset()
    print('Synthetic staging batch reset completed')
    for collection, count in deleted.items():
        if count:
            print(f'{collection}: {count}')
