"""Create a consistent SQLite backup using SQLite online backup API."""
import sqlite3, time
from pathlib import Path
base=Path(__file__).resolve().parent
src=base/"store.db"
if not src.exists(): raise SystemExit("store.db was not found")
folder=base/"backups"; folder.mkdir(exist_ok=True)
dest=folder/f"store-{time.strftime('%Y%m%d-%H%M%S')}.db"
with sqlite3.connect(src) as source, sqlite3.connect(dest) as target: source.backup(target)
print(f"Backup created: {dest}")
