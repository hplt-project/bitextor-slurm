import argparse
import zipfile
from pathlib import Path
from collections import defaultdict
import re
import hashlib
from tqdm import tqdm


def get_base_filename(filename: str) -> str:
    """
    Extract the base filename without any trailing _N suffix and the .xml extension.
    For example:
      - "foo.xml"        -> "foo"
      - "foo_1.xml"      -> "foo"
      - "en/foo.xml"     -> "foo"  (only the file name is used)
    """
    match = re.match(r"(.+?)(?:_\d+)?\.xml$", filename)
    return match.group(1) if match else filename.rsplit('.', 1)[0]


def compute_hash_from_data(data: bytes) -> str:
    """Compute SHA-256 hash from file data."""
    return hashlib.sha256(data).hexdigest()


def process_zip_file_pass1(zip_path: Path, lang_code: str) -> tuple:
    """
    First pass: Process a single zip file and return metadata only (no file content).
    Returns: (lang_file_paths, english_files_metadata)
    """
    lang_file_paths = []  # List of internal_path only
    english_files_metadata = []  # List of (base_name, internal_path, hash) - NO file_data

    print(f"Processing zip file {zip_path}")
    with zipfile.ZipFile(zip_path, 'r') as zf:
        # Get all XML files at once
        xml_files = [f for f in zf.namelist() if f.endswith('.xml')]

        for file in tqdm(xml_files, ncols=80):
            parts = Path(file).parts
            if not parts:
                continue

            if parts[0] == "en":
                # English file: compute hash but don't store content
                file_data = zf.read(file)
                base = get_base_filename(Path(file).name)
                hash_val = compute_hash_from_data(file_data)
                english_files_metadata.append((base, file, hash_val))
                del file_data  # Free memory immediately

            elif parts[0] == lang_code:
                # Language-specific file: only store path
                lang_file_paths.append(file)

    return lang_file_paths, english_files_metadata


def write_lang_files_from_zip(zip_path: Path, lang_file_paths: list, output_path: Path, lang_code: str):
    """Re-read and write language-specific files to output zip."""
    if not lang_file_paths:
        return
    print(f"Writing language-specific files to {output_path}...")
    with zipfile.ZipFile(zip_path, 'r') as zf:
        with zipfile.ZipFile(output_path, 'w', compression=zipfile.ZIP_DEFLATED) as outzip:
            for internal_path in tqdm(lang_file_paths, ncols=80):
                file_data = zf.read(internal_path)
                parts = Path(internal_path).parts
                # Remove the language code folder from the path
                new_path = Path(*parts[1:]).as_posix() if len(parts) > 1 else Path(internal_path).name
                outzip.writestr(new_path, file_data)




def main():
    parser = argparse.ArgumentParser(
        description="Process language-specific deduped zip files. "
                    "Extract non-English content to separate zip files and combine all English content after deduplication."
    )
    parser.add_argument(
        '--root', default='/scratch/project_465001864/bitexting_v3/sharded_data/clean',
        help="Root folder containing language subdirectories ending in {langcode}-en"
    )
    args = parser.parse_args()

    root_path = Path(args.root)
    
    # Create combined output directory
    combined_dir = root_path / "combined" / "docs"
    combined_dir.mkdir(parents=True, exist_ok=True)

    # Find all folders ending with {langcode}-en
    lang_en_dirs = sorted([d for d in root_path.iterdir() 
                          if d.is_dir() and d.name.endswith('-en')])
    
    if not lang_en_dirs:
        print("No folders ending with '-en' found")
        return

    # Collect all zip files to process
    zip_tasks = []
    for lang_dir in lang_en_dirs:
        lang_code = lang_dir.name[:-3]  # Remove '-en' suffix
        deduped_zips = list(lang_dir.glob("*.all_docs.zip"))  # not doing deduped_all_docs as we've skipped that step
        if deduped_zips:
            zip_tasks.append((deduped_zips[0], lang_code))
        else:
            print(f"No deduped zip found in {lang_dir}")

    if not zip_tasks:
        print("No zip files to process")
        return

    # ========== PASS 1: Collect metadata only (no file content in memory) ==========
    print("Pass 1: Collecting metadata and writing language files...")
    all_english_metadata = []  # List of (base_name, internal_path, hash, source_zip_path)

    for zip_path, lang_code in zip_tasks:
        lang_file_paths, english_files_metadata = process_zip_file_pass1(zip_path, lang_code)

        # Write language-specific files immediately (streaming from source zip)
        if lang_file_paths:
            lang_output_zip = combined_dir / f"{lang_code}-deduped-docs.zip"
            write_lang_files_from_zip(zip_path, lang_file_paths, lang_output_zip, lang_code)

        # Collect English metadata (no file content - just paths and hashes)
        print("Collecting English metadata")
        for base, internal_path, hash_val in tqdm(english_files_metadata, ncols=80):
            all_english_metadata.append((base, internal_path, hash_val, str(zip_path)))

    # ========== Deduplicate using metadata only ==========
    print("Deduplicating English files by hash...")

    # Group by base filename
    english_by_base = defaultdict(list)
    for base, internal_path, hash_val, source_zip in all_english_metadata:
        english_by_base[base].append((internal_path, hash_val, source_zip))

    # Deduplicate and determine which files to keep
    # Store: (source_zip, internal_path, output_name)
    files_to_copy = []

    for base, entries in tqdm(english_by_base.items(), desc="Deduplicating by content", ncols=80):
        # Deduplicate by hash (keep one per unique content)
        unique_by_hash = {}
        for internal_path, hash_val, source_zip in entries:
            if hash_val not in unique_by_hash:
                unique_by_hash[hash_val] = (internal_path, source_zip)

        unique_list = list(unique_by_hash.values())
        unique_list.sort(key=lambda x: x[0])  # Sort by internal path

        # Assign names
        if len(unique_list) == 1:
            new_name = f"{base}.xml"
            files_to_copy.append((unique_list[0][1], unique_list[0][0], new_name))
        else:
            for idx, (internal_path, source_zip) in enumerate(unique_list, start=1):
                new_name = f"{base}_{idx}.xml"
                files_to_copy.append((source_zip, internal_path, new_name))

    # Resolve naming conflicts
    print("Resolving naming conflicts...")
    used_names = set()
    final_files_to_copy = []  # (source_zip, internal_path, final_name)

    for source_zip, internal_path, new_name in tqdm(files_to_copy, desc="Resolving conflicts", ncols=80):
        base_name = new_name.rsplit('.xml', 1)[0]
        counter = 1

        final_name = f"{base_name}_{counter}.xml"
        while final_name in used_names:
            counter += 1
            final_name = f"{base_name}_{counter}.xml"

        used_names.add(final_name)
        final_files_to_copy.append((source_zip, internal_path, final_name))

    # Free memory before pass 2
    del all_english_metadata
    del english_by_base
    del files_to_copy

    # ========== PASS 2: Re-read and write English files ==========
    print("Pass 2: Writing English files...")

    # Group files by source zip to minimize zip open/close operations
    files_by_source = defaultdict(list)
    for source_zip, internal_path, final_name in final_files_to_copy:
        files_by_source[source_zip].append((internal_path, final_name))

    en_output_zip = combined_dir / "en-deduped-docs.zip"

    with zipfile.ZipFile(en_output_zip, 'w', compression=zipfile.ZIP_DEFLATED) as en_zip:
        for source_zip, file_list in files_by_source.items():
            with zipfile.ZipFile(source_zip, 'r') as src_zip:
                for internal_path, final_name in tqdm(file_list, ncols=80):
                    file_data = src_zip.read(internal_path)
                    en_zip.writestr(final_name, file_data)

    print(f"Created deduped zip files in {combined_dir}")
    print(f"English files saved to: {en_output_zip}")
    print(f"Language-specific files saved to individual zip files in: {combined_dir}")


if __name__ == "__main__":
    main()
