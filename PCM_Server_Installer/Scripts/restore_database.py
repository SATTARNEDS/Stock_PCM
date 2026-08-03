import argparse
import os
import sqlite3
import sys
from pathlib import Path


def verify_database(database_path: Path) -> None:
    database_path = database_path.resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"ไม่พบฐานข้อมูล: {database_path}")
    connection = sqlite3.connect(f"{database_path.as_uri()}?mode=ro", uri=True, timeout=30)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"integrity_check failed: {result}")
        required_tables = {"products", "product_lots", "transaction_logs"}
        existing_tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing_tables = sorted(required_tables - existing_tables)
        if missing_tables:
            raise RuntimeError(f"ไม่ใช่ฐานข้อมูล PCM หรือขาดตาราง: {', '.join(missing_tables)}")
    finally:
        connection.close()


def restore_database(source_path: Path, destination_path: Path) -> None:
    source_path = source_path.resolve()
    destination_path = destination_path.resolve()
    verify_database(source_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = destination_path.with_suffix(destination_path.suffix + ".restore-partial")
    if temporary_path.exists():
        temporary_path.unlink()

    source = sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True, timeout=30)
    target = sqlite3.connect(temporary_path, timeout=30)
    try:
        source.backup(target, pages=256, sleep=0.05)
        target.commit()
        result = target.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"restored database integrity_check failed: {result}")
    finally:
        target.close()
        source.close()

    os.replace(temporary_path, destination_path)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(destination_path) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    verify_database(destination_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify or safely restore the PCM SQLite database")
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination")
    parser.add_argument("--verify-only", action="store_true")
    arguments = parser.parse_args()
    try:
        source = Path(arguments.source)
        if arguments.verify_only:
            verify_database(source)
            print(f"VERIFY_OK: {source.resolve()}")
        else:
            if not arguments.destination:
                raise ValueError("ต้องระบุ --destination")
            restore_database(source, Path(arguments.destination))
            print(f"RESTORE_OK: {Path(arguments.destination).resolve()}")
    except Exception as error:
        print(f"RESTORE_ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
