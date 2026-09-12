#!/usr/bin/env python
"""Explicit, manual cleanup of OLD unmarked demo-incident duplicate rows.

This is a development convenience only - it is never run automatically by
the application (not at startup, not in Docker, not in tests). A human
runs it deliberately, and it defaults to a dry run.

Usage (from backend/, with the venv active and DATABASE_URL configured):

    python scripts/cleanup_demo_data.py            # dry run - reports only
    python scripts/cleanup_demo_data.py --apply     # actually deletes

See app/demo/cleanup.py for the exact (narrow, conservative) identification
rule used to decide what counts as an "old unmarked demo duplicate".
"""

import argparse
import sys
from pathlib import Path

# Allow running this script directly (`python scripts/cleanup_demo_data.py`)
# from the backend/ directory without needing PYTHONPATH set up first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal  # noqa: E402
from app.demo.cleanup import (  # noqa: E402
    delete_unmarked_demo_duplicates,
    find_unmarked_demo_duplicates,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete the identified rows. Without this flag, only reports what would happen.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        candidates = find_unmarked_demo_duplicates(db)

        if not candidates:
            print("No old unmarked demo-incident duplicates found. Nothing to do.")
            return 0

        print(f"Found {len(candidates)} old unmarked demo-incident duplicate row(s):")
        for event in candidates:
            print(f"  id={event.id}  {event.event_type:<14}  {event.timestamp}")

        if not args.apply:
            print("\nDry run only - no rows were deleted. Re-run with --apply to delete them.")
            return 0

        result = delete_unmarked_demo_duplicates(db, dry_run=False)
        print(f"\nDeleted {len(result.deleted_ids)} row(s): {result.deleted_ids}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
