"""Stop the old app, then migrate its SQLite file using the new .env MySQL settings."""

import argparse
from pathlib import Path

from kbagent.config import Settings
from kbagent.migration import migrate_sqlite

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    counts = migrate_sqlite(args.source, Settings())
    print("Migration committed; source preserved. Rows copied:", counts)
    print("Legacy uploads contained extracted text only; original bytes cannot be reconstructed.")
