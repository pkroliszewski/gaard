from pathlib import Path
import sqlite3


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "demo.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"
SEED_PATH = BASE_DIR / "seed.sql"


def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()

    connection = sqlite3.connect(DB_PATH)

    try:
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        connection.executescript(SEED_PATH.read_text(encoding="utf-8"))
        connection.commit()
    finally:
        connection.close()

    print(f"Demo database created: {DB_PATH}")


if __name__ == "__main__":
    main()