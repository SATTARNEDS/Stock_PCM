import argparse
import os
import sqlite3
import sys
from pathlib import Path


def create_verified_backup(source_path: Path, destination_path: Path) -> None:
    source_path = source_path.resolve()
    destination_path = destination_path.resolve()
    temporary_path = destination_path.with_suffix(destination_path.suffix + ".partial")

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if temporary_path.exists():
        temporary_path.unlink()

    source = sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True, timeout=30)
    target = sqlite3.connect(temporary_path, timeout=30)
    try:
        source.execute("PRAGMA busy_timeout=30000")
        source.backup(target, pages=256, sleep=0.05)
        target.commit()
        integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"backup integrity check failed: {integrity}")
    finally:
        target.close()
        source.close()

    os.replace(temporary_path, destination_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a verified online SQLite backup.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    arguments = parser.parse_args()

    try:
        create_verified_backup(Path(arguments.source), Path(arguments.destination))
    except Exception as error:
        print(f"BACKUP_ERROR: {error}", file=sys.stderr)
        return 1

    print(f"BACKUP_OK: {Path(arguments.destination).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
