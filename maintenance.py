from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from app import DB_PATH, init_db


def backup(output: Path) -> None:
    init_db()
    output.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(DB_PATH)
    target = sqlite3.connect(output)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    print(f"备份完成：{output.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="中央端数据维护")
    sub = parser.add_subparsers(dest="command", required=True)
    backup_parser = sub.add_parser("backup")
    backup_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "backup":
        backup(args.output)


if __name__ == "__main__":
    main()
