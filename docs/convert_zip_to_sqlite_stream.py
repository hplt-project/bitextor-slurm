#!/usr/bin/env python3
"""
Convert zip to SQLite using libarchive for true sequential streaming.
Install: pip install libarchive-c
"""

import sqlite3
import libarchive
from tqdm import tqdm
import subprocess

zip_path = "/fs/bil0/bhaddow/dochplt/bitexting_v3-clean/combined/docs_deduped/en-deduped-docs.zip"
db_path = "/fs/bil0/bhaddow/dochplt/bitexting_v3-clean/combined/docs_deduped/en-docs.db"

# Get approximate file count for progress bar (fast with unzip -Z1)
print("Counting files in zip...")
result = subprocess.run(["unzip", "-Z1", zip_path], capture_output=True, text=True)
file_count = len([l for l in result.stdout.strip().split('\n') if l])
print(f"Found {file_count} files")

conn = sqlite3.connect(db_path)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
conn.execute("PRAGMA cache_size=-2000000")  # 2GB cache
conn.execute("DROP TABLE IF EXISTS docs")  # Start fresh
conn.execute("CREATE TABLE docs (name TEXT, content BLOB)")  # No index yet

batch_size = 10000
batch = []

# libarchive streams through the archive sequentially - no index loading
with tqdm(total=file_count, desc="Converting") as pbar:
    with libarchive.file_reader(zip_path) as archive:
        for entry in archive:
            if entry.isdir:
                continue

            # Read file content as blocks and join
            content = b''.join(entry.get_blocks())
            batch.append((entry.name, content))
            pbar.update(1)

            if len(batch) >= batch_size:
                conn.executemany("INSERT OR REPLACE INTO docs (name, content) VALUES (?, ?)", batch)
                conn.commit()
                batch = []

# Insert remaining
if batch:
    conn.executemany("INSERT OR REPLACE INTO docs (name, content) VALUES (?, ?)", batch)
    conn.commit()

print(f"Inserted {count:,} files. Now building index...")
conn.execute("CREATE UNIQUE INDEX idx_name ON docs(name)")
conn.close()
print(f"Done. Database saved to {db_path}")
