"""Separate process keeps the desktop responsive and GPU memory recoverable."""
import argparse
import json
from pathlib import Path
import sys
import traceback

from .domain import Job
from .engine import Cancelled
from .pipeline import process


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('job')
    args = parser.parse_args()
    path = Path(args.job)
    cancel_file = path.with_suffix('.cancel')

    def notify(**event):
        print(json.dumps(event, ensure_ascii=True), flush=True)

    try:
        job = Job.from_dict(json.loads(path.read_text(encoding='utf-8')))
        process(job, notify, cancel_file.exists)
        return 0
    except Cancelled:
        notify(stage='Cancelled', detail='Completed repair chunks are saved. Start the same job to resume.', cancelled=True)
        return 2
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        notify(stage='Error', detail=str(error), error=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
