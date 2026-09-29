"""Poll persistent model-training jobs outside the Flask request process."""

import argparse
from pathlib import Path
import sys
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ml.training_service import ModelTrainingService, TrainingJobRepository
from src.db.mongodb import get_database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--poll-interval-seconds', type=float, default=2.0)
    parser.add_argument('--once', action='store_true', help='process at most one queued job and exit')
    args = parser.parse_args()
    if not 0.1 <= args.poll_interval_seconds <= 60:
        parser.error('--poll-interval-seconds must be between 0.1 and 60')

    service = ModelTrainingService(TrainingJobRepository(get_database()))
    worker_id = f'training-worker-{uuid4().hex[:12]}'
    while True:
        job_id = service.process_next(worker_id=worker_id)
        if args.once:
            return 0
        if job_id is None:
            time.sleep(args.poll_interval_seconds)


if __name__ == '__main__':
    raise SystemExit(main())
