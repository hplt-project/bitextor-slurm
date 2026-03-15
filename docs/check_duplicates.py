#!/usr/bin/env python

#
# check the docs and alignment files for duplicates.
# are the filenames *_1, *_2, *_3 etc all mutual duplicates
#

import logging
import tqdm
import zipfile

from pathlib import Path

LOG = logging.getLogger(__name__)
DATA_DIR = "/fs/bil0/bhaddow/dochplt/bitexting_v3-clean/combined"
DOCS_DIR = f"{DATA_DIR}/docs"

def main():
  logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s: %(message)s')
  for docs_file in Path(DOCS_DIR).glob("*.zip"):
    LOG.info(f"Examining {docs_file}")
    file_hashes = {} # Maps id to (first seen content hash, lowest suffix)
    count, mismatch = 0, False
    with zipfile.ZipFile(docs_file, 'r') as zf:
      for info in tqdm.tqdm(zf.filelist, total=len(zf.filelist)):
        file = info.filename
        if not file.endswith('.xml'): continue
        split = file.split("_")
        file_id = split[0]
        file_suffix = int(split[1].split(".")[0])
        file_data = hash(zf.read(file))
        if file_id in file_hashes:
          if file_hashes[file_id][0] != file_data:
            mismatch = True
          if file_hashes[file_id][1] > file_suffix:
            file_hashes[file_id][1] = file_suffix
        else:
          file_hashes[file_id] = [file_data, file_suffix]
        count += 1
    LOG.info(f"count: {count}; unique: {len(file_hashes)}  mismatch: {mismatch}")
    # ensure that each id has a version with suffix _1
    count_no_one = 0
    for _, (_,suffix) in file_hashes.items():
      if suffix > 1:
        count_no_one +=1
    LOG.info(f"count with no _1.xml version: {count_no_one}")

if __name__ == "__main__":
  main()
