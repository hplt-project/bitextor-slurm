
#!/usr/bin/env python3
import os
import sys
import re
import glob
import gzip
import base64
import argparse
import zipfile
import xml.etree.ElementTree as ET
import string
import logging
import gc
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED, TimeoutError
from threading import Lock, local
from loomchild.segmenter import LoomchildSegmenter
from tqdm import tqdm
from typing import Optional, Tuple

# Configure logging
logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')

# Pre-compile regular expressions and create constants
URL_SANITIZER = re.compile(r'[^A-Za-z0-9_.-]')
DIGITS_AND_PUNCTUATION = set(string.punctuation + string.digits)

def remove_invalid_xml_chars(s):
    """Remove characters not allowed in XML 1.0."""
    return ''.join(c for c in s if c in ("\t", "\n", "\r") or ord(c) >= 0x20)

def filter_trash(sentence: str) -> bool:
    """Returns False if sentence is too noisy."""
    if "\x00" in sentence:
        return False
    n = sum(1 for c in sentence if c in DIGITS_AND_PUNCTUATION)
    return n < len(sentence) // 2

def split_segments(text: str, splitter_unary_func, prune_type: str = "words", 
                  prune_threshold: int = 0, filter_bad_sentences: bool = True, 
                  return_list: bool = False) -> str:
    segments = splitter_unary_func(text)
    del text

    if segments:
        if prune_threshold:
            if prune_type == "words":
                segments = [s for s in segments if len(s.split()) <= prune_threshold]
            elif prune_type == "chars":
                segments = [s for s in segments if len(s) <= prune_threshold]

        if filter_bad_sentences:
            segments = [s for s in segments if filter_trash(s)]

        segments = [s.strip() for s in segments if s.strip()]
        
        if return_list:
            return segments
        result = "\n".join(segments) + "\n"
        del segments
        return result
    return [] if return_list else ""

def create_xml_from_doc(doc_text: str, segmenter, prune_type: str = "words", 
                       prune_threshold: int = 0, dont_filter: bool = False) -> bytes:
    """Create an XML document from the decoded document text - optimized version."""
    content = doc_text.strip(' \n')
    del doc_text
    
    paragraphs = content.split("\n")
    del content
    
    # Build XML directly as a string for better performance
    xml_parts = ['<?xml version="1.0" encoding="utf-8"?>\n<document>']
    
    for p_index, line in enumerate(paragraphs, start=1):
        paragraph_text = line.split("\t")[0].strip()
        
        if not paragraph_text:
            continue
            
        try:
            sentences = split_segments(
                paragraph_text,
                segmenter.get_document_segmentation,
                prune_type=prune_type,
                prune_threshold=prune_threshold,
                filter_bad_sentences=not dont_filter,
                return_list=True
            )
        except Exception as e:
            debug_message = f"""
======================================================================
CRITICAL: Segmentation failed in Job {job_id}, Task {task_id}
======================================================================
Timestamp:          {datetime.now().isoformat()}
Input File:         {input_file}

Error Details:
----------------------------------------------------------------------
Exception Type:     {type(e).__name__}
Exception Message:  {e}

Failing Data:
----------------------------------------------------------------------
Paragraph Index:    {p_index}
Paragraph Length:   {len(paragraph_text)}

Paragraph Content (raw):
--------------------------
{paragraph_text}
--------------------------

Paragraph Content (repr, to show hidden characters like \\r, \\t):
-----------------------------------------------------------------
{repr(paragraph_text)}
-----------------------------------------------------------------

Function Parameters at Time of Failure:
----------------------------------------------------------------------
prune_type:             {prune_type}
prune_threshold:        {prune_threshold}
filter_bad_sentences:   {not dont_filter}
======================================================================
"""
            # Log as a critical error instead of just a warning
            logging.error(debug_message)
            continue
        del paragraph_text
        
        if not sentences:
            continue
            
        xml_parts.append(f'  <P id="{p_index}">')
        for s_index, sentence in enumerate(sentences, start=1):
            clean_text = remove_invalid_xml_chars(sentence)
            # Escape XML special characters
            clean_text = clean_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")
            xml_parts.append(f'    <s id="{p_index}.{s_index}">{clean_text}</s>')
        xml_parts.append('  </P>')
        del sentences

    xml_parts.append('</document>')
    return '\n'.join(xml_parts).encode('utf-8')

def get_unique_filename(zf: zipfile.ZipFile, base_filename: str) -> str:
    """
    Use the ZIP file's internal NameToInfo dictionary to find a unique name.
    Always append a counter starting from 1.
    """
    if base_filename.endswith(".xml"):
        name_part = base_filename[:-4]
        ext = ".xml"
    else:
        name_part = base_filename
        ext = ""
    
    counter = 1
    candidate = f"{name_part}_{counter}{ext}"
    while candidate in zf.NameToInfo:
        counter += 1
        candidate = f"{name_part}_{counter}{ext}"
    
    return candidate

def sanitize_url(url: str) -> str:
    return URL_SANITIZER.sub('_', url)

# Thread-local storage for segmenters
thread_local = local()

def initialize_thread_segmenters(src_lang: str):
    """Pre-initialize segmenters for each thread"""
    if not hasattr(thread_local, 'segmenters'):
        thread_local.segmenters = {
            src_lang: LoomchildSegmenter(src_lang),
            'en': LoomchildSegmenter('en')
        }

def get_segmenter(lang: str) -> LoomchildSegmenter:
    return thread_local.segmenters[lang]

class SafeZipWriter:
    def __init__(self, zf: zipfile.ZipFile):
        self.zf = zf
        self.lock = Lock()
    
    def write_file(self, filename: str, content: bytes) -> None:
        with self.lock:
            final_name = get_unique_filename(self.zf, filename)
            self.zf.writestr(final_name, content)

def process_line(line: str, src_lang: str, zip_writer: SafeZipWriter) -> None:
    """Process a single line and write results to ZIP file."""
    try:
        cols = line.strip().split("\t")
        if len(cols) < 5:
            return
            
        url_a, url_b, doc_a_b64, doc_b_b64 = cols[:4]
        
        doc_a_text = base64.b64decode(doc_a_b64).decode('utf-8', errors='ignore')
        doc_b_text = base64.b64decode(doc_b_b64).decode('utf-8', errors='ignore')
        del doc_a_b64, doc_b_b64
        
        seg_src = get_segmenter(src_lang)
        seg_en = get_segmenter("en")
        
        xml_src = create_xml_from_doc(doc_a_text, seg_src)
        del doc_a_text
        
        xml_en = create_xml_from_doc(doc_b_text, seg_en)
        del doc_b_text
        
        safe_url_a = os.path.join(src_lang, sanitize_url(url_a.strip()) + ".xml")
        safe_url_b = os.path.join("en", sanitize_url(url_b.strip()) + ".xml")
        
        zip_writer.write_file(safe_url_a, xml_src)
        zip_writer.write_file(safe_url_b, xml_en)
        
    except Exception as e:
        logging.error(f"Error processing line: {e}")

# def process_chunk(chunk: list, src_lang: str, zip_writer: SafeZipWriter, pbar: tqdm) -> None:
#     """Process a chunk of lines."""
#     initialize_thread_segmenters(src_lang)
    
#     for line in chunk:
#         process_line(line, src_lang, zip_writer)
#         pbar.update(1)
    
#     gc.collect()

def process_chunk(chunk: list, src_lang: str, zip_writer: SafeZipWriter) -> None:
    """Process a chunk of lines."""
    initialize_thread_segmenters(src_lang)
    processed = 0
    
    for line in chunk:
        process_line(line, src_lang, zip_writer)
        processed += 1
    
    # gc.collect()
    return processed

def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Process docs_matched.gz to produce XML files in a ZIP archive."
    )
    parser.add_argument("--folder", required=True,
                      help="Folder path whose final part is in the format <source>-en (e.g., xh-en).")
    parser.add_argument("--chunk-size", type=int, default=5000,
                      help="Number of lines to process in each chunk (default: 5000)")
    parser.add_argument("--threads", type=int, default=16,
                      help="Number of processing threads (default: 16)")
    return parser.parse_args()

def process_docs(folder: str, chunk_size: int = 1000, num_threads: int = 4):
    # Extract source language from the folder name
    last_folder = os.path.basename(os.path.normpath(folder))
    parts = last_folder.split("-")
    if len(parts) != 2 or parts[1].lower() != "en":
        sys.exit("Error: Folder name must be in the format <source>-en (e.g., xh-en).")
    src_lang = parts[0].lower()

    pattern = "*.docs_matched.gz"
    matching_files = glob.glob(os.path.join(folder, pattern))
    if not matching_files:
        sys.exit(f"Error: No file matching pattern {pattern} found in {folder}.")
    docs_file = matching_files[0]
    print(f"Using file: {docs_file}")

    output_filename = (f"{src_lang}-en.all_collections.all_docs.zip")
    
    output_path = os.path.join(folder, output_filename)
    
    if os.path.exists(output_path):
        os.remove(output_path)
        print(f"Deleted old ZIP file: {output_path}")

    # Count total lines
    with gzip.open(docs_file, mode='rt', encoding='utf-8') as f_in:
        total_lines = sum(1 for _ in f_in)
    print(f"Total lines to process: {total_lines}")

    # TIMEOUT CONSTANT (10 Minutes)
    TIMEOUT_SECONDS = 600
    executor = ThreadPoolExecutor(max_workers=num_threads)

    try:
        with zipfile.ZipFile(output_path, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
            zip_writer = SafeZipWriter(zf)
            with gzip.open(docs_file, mode='rt', encoding='utf-8') as f_in:
                pbar = tqdm(total=total_lines, unit="lines", desc="Processing documents")
                    
                current_chunk = []
                futures = []
                
                for line in f_in:
                    current_chunk.append(line)
                    
                    if len(current_chunk) >= chunk_size:
                        # THROTTLING: If too many pending tasks, wait.
                        if len(futures) >= num_threads * 2:
                            # Wait up to 10 mins for FIRST completion
                            done, not_done = wait(futures, return_when=FIRST_COMPLETED, timeout=TIMEOUT_SECONDS)
                            
                            if not done:
                                logging.warning("System stalled (10m timeout). Moving on, but threads may be stuck.")
                                # If stalled, we update futures to just the not_done ones and continue filling
                                # effectively ignoring the stuck ones for now (they will run in background)
                                futures = list(not_done)
                            else:
                                for future in done:
                                    try:
                                        pbar.update(future.result(timeout=10)) # Should be instant
                                    except Exception as e:
                                        logging.error(f"Task error: {e}")
                                futures = list(not_done)

                        # Submit new chunk
                        futures.append(
                            executor.submit(process_chunk, current_chunk, src_lang, zip_writer)
                        )
                        current_chunk = []
                
                # Process remaining lines
                # Submit final partial chunk
                if current_chunk:
                    futures.append(
                        executor.submit(process_chunk, current_chunk, src_lang, zip_writer)
                    )
                
                # FINAL COLLECTION
                # Using as_completed with a timeout to prevent hanging at 100%
                try:
                    for future in as_completed(futures, timeout=TIMEOUT_SECONDS):
                        try:
                            processed = future.result(timeout=1) # Result is ready, fetch immediately
                            pbar.update(processed)
                        except Exception as e:
                            logging.error(f"Final collection error: {e}")
                except TimeoutError:
                    logging.error("Final processing timed out (waiting > 10m). Skipping remaining stuck tasks.")
                
                pbar.close()
    finally:
        # shutdown(wait=False) ensures we do not wait for stuck "zombie" threads
        print("Shutting down executor (forcing exit if threads are stuck)...")
        executor.shutdown(wait=False)
        gc.collect()

    print(f"Processing complete. Output written to {output_path}")
    gc.collect()

def main():
    args = parse_arguments()
    process_docs(args.folder, args.chunk_size, args.threads)

if __name__ == '__main__':
    main()
