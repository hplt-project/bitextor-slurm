import argparse
import zipfile
from pathlib import Path
from collections import defaultdict
import re
import hashlib
from tqdm import tqdm
import concurrent.futures
from threading import Lock


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


def process_zip_file(zip_path: Path, lang_code: str) -> tuple:
    """
    Process a single zip file and return categorized file data.
    Returns: (lang_files_data, english_files_data)
    """
    lang_files_data = []  # List of (internal_path, file_data)
    english_files_data = []  # List of (base_name, internal_path, file_data, hash)
    
    with zipfile.ZipFile(zip_path, 'r') as zf:
        # Get all XML files at once
        xml_files = [f for f in zf.namelist() if f.endswith('.xml')]
        
        for file in xml_files:
            parts = Path(file).parts
            if not parts:
                continue
            
            # Read file data once
            file_data = zf.read(file)
            
            if parts[0] == "en":
                # English file: store with hash for deduplication
                base = get_base_filename(Path(file).name)
                hash_val = compute_hash_from_data(file_data)
                english_files_data.append((base, file, file_data, hash_val))
            
            elif parts[0] == lang_code:
                # Language-specific file: store data
                lang_files_data.append((file, file_data))
    
    return lang_files_data, english_files_data


def write_lang_zip(lang_files_data: list, output_path: Path, lang_code: str):
    """Write language-specific files to zip."""
    if not lang_files_data:
        return
        
    with zipfile.ZipFile(output_path, 'w', compression=zipfile.ZIP_DEFLATED) as outzip:
        for internal_path, file_data in lang_files_data:
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

    # Process all zip files and collect data
    print("Processing zip files...")
    all_english_data = []  # List of (base_name, internal_path, file_data, hash, source_zip)
    
    # Process each zip file
    for zip_path, lang_code in tqdm(zip_tasks, desc="Processing zip files", ncols=80):
        lang_files_data, english_files_data = process_zip_file(zip_path, lang_code)
        
        # Write language-specific files immediately
        if lang_files_data:
            lang_output_zip = combined_dir / f"{lang_code}-deduped-docs.zip"
            write_lang_zip(lang_files_data, lang_output_zip, lang_code)
        
        # Collect English data for global processing
        for base, internal_path, file_data, hash_val in english_files_data:
            all_english_data.append((base, internal_path, file_data, hash_val, str(zip_path)))

    # Process English files for deduplication
    print("Deduplicating English files...")
    
    # Group by base filename
    english_by_base = defaultdict(list)
    for base, internal_path, file_data, hash_val, source_zip in all_english_data:
        english_by_base[base].append((internal_path, file_data, hash_val, source_zip))
    
    # Deduplicate and prepare final file list
    english_to_write = []  # List of (file_data, new_name)
    
    for base, entries in tqdm(english_by_base.items(), desc="Deduplicating by content", ncols=80):
        # Deduplicate by hash (keep one per unique content)
        unique_by_hash = {}
        for internal_path, file_data, hash_val, source_zip in entries:
            if hash_val not in unique_by_hash:
                unique_by_hash[hash_val] = (internal_path, file_data, source_zip)
        
        unique_list = list(unique_by_hash.values())
        unique_list.sort(key=lambda x: x[0])  # Sort by internal path
        
        # Assign names
        if len(unique_list) == 1:
            new_name = f"{base}.xml"
            english_to_write.append((unique_list[0][1], new_name))
        else:
            for idx, (_, file_data, _) in enumerate(unique_list, start=1):
                new_name = f"{base}_{idx}.xml"
                english_to_write.append((file_data, new_name))

    # Resolve naming conflicts
    print("Resolving naming conflicts...")
    used_names = set()
    final_english_to_write = []
    
    for file_data, new_name in tqdm(english_to_write, desc="Resolving conflicts", ncols=80):
        original_name = new_name
        base_name = new_name.rsplit('.xml', 1)[0]
        counter = 1
        
        new_name = f"{base_name}_{counter}.xml"
        while new_name in used_names:
            new_name = f"{base_name}_{counter}.xml"
            counter += 1
        
        used_names.add(new_name)
        final_english_to_write.append((file_data, new_name))

    # Write all English files to combined zip
    print("Writing English files...")
    en_output_zip = combined_dir / "en-deduped-docs.zip"
    
    with zipfile.ZipFile(en_output_zip, 'w', compression=zipfile.ZIP_DEFLATED) as en_zip:
        for file_data, new_name in tqdm(final_english_to_write, desc="Writing to zip", ncols=80):
            en_zip.writestr(new_name, file_data)

    print(f"Created deduped zip files in {combined_dir}")
    print(f"English files saved to: {en_output_zip}")
    print(f"Language-specific files saved to individual zip files in: {combined_dir}")


if __name__ == "__main__":
    main()
