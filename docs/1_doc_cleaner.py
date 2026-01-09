import argparse
import os
from xml.etree.ElementTree import iterparse
import gzip
import gc
from contextlib import contextmanager
from tqdm import tqdm
import pybloom_live

@contextmanager
def open_file(filename, mode='rt'):
    """Handle both gzipped and regular files."""
    if filename.endswith('.gz'):
        opener = gzip.open
    else:
        opener = open
    with opener(filename, mode, encoding='utf-8-sig') as f:
        yield f

def build_url_index(docs_file):
    """Build a simple flat dictionary for O(1) lookups."""
    print("Building URL index...")
    
    # Use a simple dict with tuple keys for direct lookup
    url_index = {}
    line_count = 0
    
    with gzip.open(docs_file, mode='rt', encoding='utf-8') as f_in:
        for line in tqdm(f_in, desc="Reading docs.gz"):
            line = line.strip()
            columns = line.split(maxsplit=2)
            if len(columns) >= 2:
                source_url, target_url = columns[0], columns[1]
                
                # Store both directions for lookup
                key1 = (source_url, target_url)
                key2 = (target_url, source_url)
                
                if key1 not in url_index:
                    url_index[key1] = []
                if key2 not in url_index:
                    url_index[key2] = []
                    
                url_index[key1].append(line)
                url_index[key2].append(line)
                
                line_count += 1
    
    print(f"URL index built with {line_count} lines")
    return url_index

def process_tmx_fast(tmx_file, url_index, output_file, srclang):
    """Fast TMX processing with progress bars."""
    processed_lines = 0
    matches_found = 0
    written_pairs = set()
    # written_pairs = pybloom_live.ScalableBloomFilter(
    #                 initial_capacity=5_000_000_000,
    #                 error_rate=0.00000001,
    #                 mode=pybloom_live.ScalableBloomFilter.LARGE_SET_GROWTH
    #         )

    print(f"Processing TMX file: {tmx_file}")
    
    # Open output file once
    with gzip.open(output_file, mode='wt', encoding='utf-8', compresslevel=1) as f_out:
        with open_file(tmx_file, 'rt') as f_tmx:
            for event, elem in tqdm(iterparse(f_tmx, events=('end',)), desc="Processing TMX"):
                if elem.tag == 'tu':
                    # Collect URLs more efficiently
                    en_urls = []
                    src_urls = []
                    
                    for tuv_elem in elem.findall('tuv'):
                        lang = tuv_elem.get('{http://www.w3.org/XML/1998/namespace}lang')
                        
                        if lang == 'en':
                            en_urls.extend(prop.text.strip() for prop in tuv_elem.findall('.//prop[@type="source-document"]') if prop.text)
                        elif lang == srclang:
                            src_urls.extend(prop.text.strip() for prop in tuv_elem.findall('.//prop[@type="source-document"]') if prop.text)
                    
                    # Fast lookup and write
                    for src_url in src_urls:
                        for tgt_url in en_urls:
                            lookup_key = (src_url, tgt_url)
                            lines = url_index.get(lookup_key, [])
                            
                            for line in lines:
                                columns = line.split()
                                if len(columns) > 3:
                                    # Use tuple for faster hashing
                                    pair_key = (src_url, tgt_url, columns[2], columns[3])
                                    if pair_key not in written_pairs:
                                        f_out.write(line)
                                        f_out.write('\n')
                                        written_pairs.add(pair_key)
                                        matches_found += 1
                    
                    elem.clear()
                    processed_lines += 1
                    
                    # Less frequent cleanup
                    if processed_lines % 50000 == 0:
                        gc.collect()
    
    print(f"Final: {processed_lines} TUs processed, {matches_found} matches found")
    print(f"Output: {output_file}")

def process_docs(docs_file, output_file, srclang):
    """Main processing function."""
    # Build index
    url_index = build_url_index(docs_file)
    
    # Process TMX
    tmx_file = docs_file.replace('.docs.gz', '.tmx_ids.gz')
    if not os.path.exists(tmx_file):
        print(f"Error: TMX file not found at {tmx_file}")
        return
    
    process_tmx_fast(tmx_file, url_index, output_file, srclang)
    
    # Clean up
    del url_index
    gc.collect()

def main():
    parser = argparse.ArgumentParser(description="Extract and match URLs between DOCS and TMX files.")
    parser.add_argument('--folder', type=str, required=True, help="Folder to process")
    args = parser.parse_args()
    
    folder_path = args.folder
    if not os.path.isdir(folder_path):
        print(f"Error: Folder not found at {folder_path}")
        return
    
    folder_name = os.path.basename(os.path.normpath(folder_path))
    if '-' in folder_name:
        parts = folder_name.split('-')
        srclang = parts[0] if parts[0] != 'en' else parts[1]
    else:
        srclang = folder_name
    
    print(f"Processing folder: {folder_path}")
    print(f"Source language: {srclang}")
    
    for filename in os.listdir(folder_path):
        if filename.endswith(".docs.gz"):
            print(f"\nProcessing: {filename}")
            docs_file = os.path.join(folder_path, filename)
            output_file = docs_file.replace(".docs.gz", ".docs_matched.gz")
            process_docs(docs_file, output_file, srclang)

if __name__ == "__main__":
    main()
