#!/usr/bin/env python

#
# Remove the duplicated entries from the docs and alignments
#

import gzip
import logging
import re
import xml.etree.ElementTree as ET
import zipfile

from pathlib import Path
from tqdm import tqdm

LOG = logging.getLogger(__name__)
DATA_DIR = "/fs/bil0/bhaddow/dochplt/bitexting_v3-clean/combined"
DOCS_DIR = f"{DATA_DIR}/docs"
ALIGN_DIR = f"{DATA_DIR}/alignments"


def remove_suffix_1(path: str) -> str:
  """Remove _1 from paths like 'bg/document_1.xml' -> 'bg/document.xml'"""
  return re.sub(r'_1\.xml$', '.xml', path)


def process_docs_file(docs_file: Path, new_docs_dir: Path) -> bool:
  """Process a single docs zip file, keeping only *_1.xml files.

  Returns True on success, False on error.
  """
  try:
    new_zip_path = new_docs_dir / docs_file.name
    with zipfile.ZipFile(docs_file, 'r') as zf_in:
      namelist = zf_in.namelist()
      with zipfile.ZipFile(new_zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf_out:
        for name in tqdm(namelist, desc=f"  {docs_file.name}", unit="files"):
          if name.endswith('_1.xml'):
            content = zf_in.read(name)
            new_name = remove_suffix_1(name)
            zf_out.writestr(new_name, content)
    return True
  except Exception as e:
    LOG.error(f"Error processing {docs_file}: {e}")
    return False


def process_align_file(align_file: Path, new_align_dir: Path) -> bool:
  """Process a single alignment gz file, keeping only linkGrps where both
  fromDoc and toDoc end in _1.xml.

  Uses streaming to handle large files without loading entire tree into memory.

  Returns True on success, False on error.
  """
  try:
    new_align_path = new_align_dir / align_file.name

    # Get uncompressed file size for progress bar (estimate from compressed size)
    compressed_size = align_file.stat().st_size

    with gzip.open(align_file, 'rt', encoding='utf-8') as f_in, \
         gzip.open(new_align_path, 'wt', encoding='utf-8') as f_out:

      # Write XML header
      f_out.write('<?xml version="1.0" encoding="utf-8"?>\n')
      f_out.write('<!DOCTYPE cesAlign PUBLIC "-//CES//DTD XML cesAlign//EN" "">\n')
      f_out.write('<cesAlign version="1.0">\n')

      # Use iterparse to stream through the XML
      context = ET.iterparse(f_in, events=('end',))

      kept = 0
      removed = 0
      with tqdm(desc=f"  {align_file.name}", unit="linkGrps") as pbar:
        for event, elem in context:
          if elem.tag == 'linkGrp':
            from_doc = elem.get('fromDoc', '')
            to_doc = elem.get('toDoc', '')

            if from_doc.endswith('_1.xml') and to_doc.endswith('_1.xml'):
              # Update paths and write to output
              elem.set('fromDoc', remove_suffix_1(from_doc))
              elem.set('toDoc', remove_suffix_1(to_doc))
              f_out.write('  ')
              f_out.write(ET.tostring(elem, encoding='unicode'))
              f_out.write('\n')
              kept += 1
            else:
              removed += 1

            pbar.update(1)
            pbar.set_postfix(kept=kept, removed=removed)

            # Clear the element to free memory
            elem.clear()

      f_out.write('</cesAlign>\n')

    LOG.info(f"  {align_file.name}: kept {kept}, removed {removed}")
    return True
  except Exception as e:
    LOG.error(f"Error processing {align_file}: {e}")
    return False


def main():
  logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s: %(message)s')

  new_docs_dir = Path(DOCS_DIR + "_deduped")
  LOG.info(f"Deduplicating the docs files into {new_docs_dir}")

  # Create new_docs_dir if it doesn't exist
  new_docs_dir.mkdir(parents=True, exist_ok=True)

  docs_errors = 0
  for docs_file in Path(DOCS_DIR).glob("*.zip"):
    LOG.info(f"Processing {docs_file}")
    if not process_docs_file(docs_file, new_docs_dir):
      docs_errors += 1

  LOG.info(f"Docs processing complete. Errors: {docs_errors}")

  new_align_dir = Path(ALIGN_DIR + "_deduped")
  LOG.info(f"Deduplicating the alignments file into {new_align_dir}")

  # Create new_align_dir if it doesn't exist
  new_align_dir.mkdir(parents=True, exist_ok=True)

  align_errors = 0
  for align_file in Path(ALIGN_DIR).glob("*.gz"):
    LOG.info(f"Processing {align_file}")
    if not process_align_file(align_file, new_align_dir):
      align_errors += 1

  LOG.info(f"Alignment processing complete. Errors: {align_errors}")
  LOG.info(f"Total errors: {docs_errors + align_errors}")


if __name__ == "__main__":
  main()

