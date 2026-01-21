import argparse
import gzip
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile
from tqdm import tqdm
import logging
from collections import defaultdict
import multiprocessing as mp
from functools import partial
from itertools import islice
from typing import Dict, List, Tuple, Optional, NamedTuple, Set
import math
import atexit
import os
import sqlite3
import tempfile

# --- Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Register the XML namespace to correctly parse 'xml:lang' attributes
ET.register_namespace('xml', 'http://www.w3.org/XML/1998/namespace')
XML_NS = {'xml': 'http://www.w3.org/XML/1998/namespace'}

# Bitextor segment separator constant
BITEXTOR_SEPARATOR = "~~~BITEXTOR_SEGMENT_SEPARATOR~~~"

# --- Data Structures ---
class AlignmentScores(NamedTuple):
    aligner: Optional[str] = None
    bicleaner: Optional[str] = None
    bifixer: Optional[str] = None

class DocumentInfo(NamedTuple):
    document: str
    paragraph_id: str
    scores: AlignmentScores

class SentenceAlignment(NamedTuple):
    src_sent: str
    tgt_sent: str
    src_para_id: str
    tgt_para_id: str
    scores: AlignmentScores

# --- Shared Globals (paths to SQLite databases) ---
shared_src_db_path: Optional[Path] = None
shared_tgt_db_path: Optional[Path] = None

# --- Worker-Specific Globals ---
worker_src_zip_path: Optional[Path] = None
worker_tgt_zip_path: Optional[Path] = None
worker_src_document_cache: Optional[Dict[str, bytes]] = None
worker_tgt_document_cache: Optional[Dict[str, bytes]] = None
worker_src_db_conn: Optional[sqlite3.Connection] = None
worker_tgt_db_conn: Optional[sqlite3.Connection] = None

# --- Cache Statistics ---
worker_cache_stats: Optional[Dict[str, int]] = None

def worker_initializer(src_zip_path: Path, tgt_zip_path: Path):
    """
    Initialize each worker. Stores ZIP paths and connects to SQLite databases.
    Documents are extracted on-demand using batch pre-fetch.
    """
    global worker_src_zip_path, worker_tgt_zip_path
    global worker_src_document_cache, worker_tgt_document_cache
    global worker_src_db_conn, worker_tgt_db_conn
    global worker_cache_stats

    try:
        # Store paths - documents will be batch extracted per batch
        worker_src_zip_path = src_zip_path
        worker_tgt_zip_path = tgt_zip_path

        worker_src_document_cache = {}
        worker_tgt_document_cache = {}

        # Connect to SQLite databases (read-only, shared cache for efficiency)
        worker_src_db_conn = sqlite3.connect(f"file:{shared_src_db_path}?mode=ro", uri=True)
        worker_tgt_db_conn = sqlite3.connect(f"file:{shared_tgt_db_path}?mode=ro", uri=True)

        # Initialize cache statistics
        worker_cache_stats = {
            'src_hits': 0,
            'src_misses': 0,
            'tgt_hits': 0,
            'tgt_misses': 0,
            'src_fast_path': 0,  # single-version, no read needed
            'tgt_fast_path': 0,
        }

        logger.info(f"Worker {mp.current_process().pid} initialized successfully.")

        atexit.register(worker_cleanup)

    except Exception as e:
        logger.error(f"Failed to initialize worker {mp.current_process().pid}: {e}")
        raise

def worker_cleanup():
    """Clean up worker resources when a process terminates."""
    global worker_cache_stats, worker_src_db_conn, worker_tgt_db_conn
    try:
        # Log cache statistics before cleanup
        if worker_cache_stats:
            pid = mp.current_process().pid
            src_total = worker_cache_stats['src_hits'] + worker_cache_stats['src_misses']
            tgt_total = worker_cache_stats['tgt_hits'] + worker_cache_stats['tgt_misses']
            src_hit_rate = (worker_cache_stats['src_hits'] / src_total * 100) if src_total > 0 else 0
            tgt_hit_rate = (worker_cache_stats['tgt_hits'] / tgt_total * 100) if tgt_total > 0 else 0

            # Calculate cache sizes
            src_cache_size = len(worker_src_document_cache) if worker_src_document_cache else 0
            tgt_cache_size = len(worker_tgt_document_cache) if worker_tgt_document_cache else 0
            src_cache_bytes = sum(len(v) for v in worker_src_document_cache.values() if v) if worker_src_document_cache else 0
            tgt_cache_bytes = sum(len(v) for v in worker_tgt_document_cache.values() if v) if worker_tgt_document_cache else 0

            logger.info(
                f"Worker {pid} cache stats: "
                f"SRC[hits={worker_cache_stats['src_hits']}, misses={worker_cache_stats['src_misses']}, "
                f"hit_rate={src_hit_rate:.1f}%, fast_path={worker_cache_stats['src_fast_path']}, "
                f"cached_docs={src_cache_size}, cache_MB={src_cache_bytes/1024/1024:.1f}] "
                f"TGT[hits={worker_cache_stats['tgt_hits']}, misses={worker_cache_stats['tgt_misses']}, "
                f"hit_rate={tgt_hit_rate:.1f}%, fast_path={worker_cache_stats['tgt_fast_path']}, "
                f"cached_docs={tgt_cache_size}, cache_MB={tgt_cache_bytes/1024/1024:.1f}]"
            )

        # Close SQLite connections
        if worker_src_db_conn:
            worker_src_db_conn.close()
        if worker_tgt_db_conn:
            worker_tgt_db_conn.close()

        logger.debug(f"Worker {mp.current_process().pid} cleanup complete.")
    except Exception as e:
        logger.debug(f"Error during worker cleanup in {mp.current_process().pid}: {e}")

def batch_extract_to_cache(files: Set[str], zip_path: Path, cache: Dict[str, bytes]):
    """
    Extract multiple files from a ZIP using unzip command and populate cache.
    Uses temp directory and processes in chunks to handle command line limits.
    Each unzip call scans the central directory once and extracts all requested files.
    """
    import subprocess

    if not files:
        return

    # Filter out files already in cache
    files_to_extract = [f for f in files if f not in cache]
    if not files_to_extract:
        return

    # Chunk size based on command line limits (~128KB safe, avg filename ~60 chars)
    chunk_size = 1500

    for i in range(0, len(files_to_extract), chunk_size):
        chunk = files_to_extract[i:i+chunk_size]

        with tempfile.TemporaryDirectory() as tmpdir:
            # -j: junk paths (extract flat)
            # -o: overwrite without prompting
            # -q: quiet
            cmd = ['unzip', '-j', '-o', '-q', '-d', tmpdir, str(zip_path)] + chunk
            try:
                subprocess.run(cmd, capture_output=True, timeout=600, check=False)
            except subprocess.TimeoutExpired:
                logger.warning(f"Timeout during batch extraction of {len(chunk)} files from {zip_path.name}")

            # Read extracted files into cache
            for filename in chunk:
                # Handle both flat filenames and paths
                basename = os.path.basename(filename) if '/' in filename else filename
                filepath = os.path.join(tmpdir, basename)
                try:
                    with open(filepath, 'rb') as f:
                        cache[filename] = f.read()
                except FileNotFoundError:
                    cache[filename] = None

def collect_batch_file_requirements(
    batch: List[ET.Element],
    src_lang: str,
    tgt_lang: str
) -> Tuple[Set[str], Set[str]]:
    """
    Pre-scan a batch to collect all document files that need to be extracted.
    Only includes files for documents with multiple versions (single-version uses fast path).
    Returns (src_files_to_extract, tgt_files_to_extract).
    """
    src_files: Set[str] = set()
    tgt_files: Set[str] = set()

    for tu in batch:
        src_tuv = tu.find(f".//tuv[@{{{XML_NS['xml']}}}lang='{src_lang}']")
        tgt_tuv = tu.find(f".//tuv[@{{{XML_NS['xml']}}}lang='{tgt_lang}']")

        if src_tuv is None or tgt_tuv is None:
            continue

        src_doc_info_list = extract_document_info_from_tuv(src_tuv)
        tgt_doc_info_list = extract_document_info_from_tuv(tgt_tuv)

        for src_doc_url, _, _ in src_doc_info_list:
            base_path = sanitize_url_to_filename(src_doc_url)
            versions = get_document_versions_from_db(base_path, 'src')
            if len(versions) > 1:  # Only need to extract if multiple versions
                src_files.update(versions)

        for tgt_doc_url, _, _ in tgt_doc_info_list:
            base_path = sanitize_url_to_filename(tgt_doc_url)
            versions = get_document_versions_from_db(base_path, 'tgt')
            if len(versions) > 1:  # Only need to extract if multiple versions
                tgt_files.update(versions)

    return src_files, tgt_files

def sanitize_url_to_filename(url: str) -> str:
    """
    Convert a URL into a safe filename format that matches the files inside the ZIP archives.
    This function removes the version suffix like '_1' from the end.
    """
    base = re.sub(r'[^A-Za-z0-9_.-]', '_', url)
    return f"{base}.xml"

def parse_paragraph_id(para_id_str: str) -> Optional[List[Dict]]:
    """Parse a potentially multi-part paragraph ID string into a list of structured dicts."""
    if not para_id_str:
        return None
    try:
        if '+' in para_id_str:
            parts = para_id_str.split('+')
            return [p for p in (parse_single_paragraph_id(p_str) for p_str in parts) if p]
        return [parse_single_paragraph_id(para_id_str)]
    except Exception as e:
        logger.warning(f"Failed to parse paragraph ID '{para_id_str}': {e}")
        return None

def parse_single_paragraph_id(para_id_str: str) -> Optional[Dict]:
    """Parse a single paragraph ID of the form 'p1:2s3/4' into its components."""
    if not para_id_str: return None
    match = re.match(r'p(\d+)(?::(\d+))?s(\d+)(?:/(\d+))?', para_id_str)
    if match:
        return {
            'paragraph_num': int(match.group(1)),
            'total_paragraphs': int(match.group(2)) if match.group(2) else None,
            'sentence_num': int(match.group(3)),
            'total_sentences': int(match.group(4)) if match.group(4) else None
        }
    logger.warning(f"Could not parse single paragraph ID format: '{para_id_str}'")
    return None

def parse_multi_document_prop(prop_text: Optional[str]) -> List[Optional[str]]:
    """Parse a property text that may contain '---' separating values for multiple documents."""
    if not prop_text:
        return [None]
    return prop_text.split('---') if '---' in prop_text else [prop_text]

def extract_document_info_from_tuv(tuv: ET.Element) -> List[Tuple[str, str, str]]:
    """
    Extracts document information from a TUV element.
    This handles parallel documents, paragraph IDs, and text segments.
    """
    source_docs = [prop.text for prop in tuv.findall(".//prop[@type='source-document']")]
    para_id_elem = tuv.find(".//prop[@type='paragraph-id']")
    para_ids = parse_multi_document_prop(para_id_elem.text if para_id_elem is not None else None)
    seg_elem = tuv.find(".//seg")
    segment_text = seg_elem.text if seg_elem is not None else ""
    segments = segment_text.split(BITEXTOR_SEPARATOR) if BITEXTOR_SEPARATOR in segment_text else [segment_text]

    doc_info_list = []
    if len(segments) > 1:
        min_len = min(len(source_docs), len(para_ids), len(segments))
        if len(source_docs) != min_len or len(para_ids) != min_len or len(segments) != min_len:
              logger.warning(f"Mismatch in parallel lists: docs({len(source_docs)}), paras({len(para_ids)}), segs({len(segments)})")
        for i in range(min_len):
            doc_info_list.append((source_docs[i], para_ids[i], segments[i]))
    else:
        segment = segments[0]
        min_len = min(len(source_docs), len(para_ids))
        if len(source_docs) != len(para_ids):
            logger.warning(f"Mismatch in parallel lists: docs({len(source_docs)}), paras({len(para_ids)}) for single segment")
        for i in range(min_len):
            doc_info_list.append((source_docs[i], para_ids[i], segment))

    return doc_info_list

def extract_scores_from_tu(tu: ET.Element) -> List[AlignmentScores]:
    """Extracts alignment scores from a TU element, handling multiple document scenarios."""
    aligner_elem = tu.find(".//prop[@type='score-aligner']")
    bicleaner_elem = tu.find(".//prop[@type='score-bicleaner']")
    bifixer_elem = tu.find(".//prop[@type='score-bifixer']")
    
    aligner_scores = parse_multi_document_prop(aligner_elem.text if aligner_elem is not None else None)
    bicleaner_scores = parse_multi_document_prop(bicleaner_elem.text if bicleaner_elem is not None else None)
    bifixer_scores = parse_multi_document_prop(bifixer_elem.text if bifixer_elem is not None else None)

    max_docs = max(len(aligner_scores), len(bicleaner_scores), len(bifixer_scores))
    
    aligner_scores.extend([None] * (max_docs - len(aligner_scores)))
    bicleaner_scores.extend([None] * (max_docs - len(bicleaner_scores)))
    bifixer_scores.extend([None] * (max_docs - len(bifixer_scores)))
    
    return [
        AlignmentScores(aligner=a, bicleaner=b, bifixer=f)
        for a, b, f in zip(aligner_scores, bicleaner_scores, bifixer_scores)
    ]

def scan_zip_to_sqlite_via_unzip(zip_path: Path, db_path: Path) -> int:
    """
    Scans a zip file using 'unzip -l' command (avoids loading 88M entries into Python memory)
    and creates a SQLite database mapping base filenames to versioned paths.
    Returns the number of unique base documents.
    """
    import subprocess

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create table
    cursor.execute('''
        CREATE TABLE doc_versions (
            base_name TEXT NOT NULL,
            version_path TEXT NOT NULL
        )
    ''')

    # Use unzip -l to list files (streams output, doesn't load all into memory)
    logger.info(f"Listing files in {zip_path.name} via unzip command...")
    proc = subprocess.Popen(
        ['unzip', '-l', str(zip_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    batch = []
    batch_size = 100000
    line_count = 0
    xml_count = 0

    # Parse unzip -l output: "  length  date  time  name"
    # Skip header lines, extract filename from each line
    for line in proc.stdout:
        line_count += 1
        # Skip header/footer lines (first 3 and last 2 typically)
        parts = line.strip().split()
        if len(parts) >= 4:
            filename = parts[-1]  # Last column is the filename
            if filename.endswith('.xml'):
                xml_count += 1
                base = re.sub(r'_(\d+)\.xml$', '.xml', filename)
                batch.append((base, filename))

                if len(batch) >= batch_size:
                    cursor.executemany('INSERT INTO doc_versions VALUES (?, ?)', batch)
                    batch = []

        # Progress indicator every 1M lines
        if line_count % 1000000 == 0:
            logger.info(f"  Processed {line_count:,} lines, {xml_count:,} XML files...")

    proc.wait()

    # Insert remaining
    if batch:
        cursor.executemany('INSERT INTO doc_versions VALUES (?, ?)', batch)

    logger.info(f"Total: {xml_count:,} XML files from {line_count:,} entries")

    # Create index after bulk insert (faster)
    logger.info("Creating database index...")
    cursor.execute('CREATE INDEX idx_base_name ON doc_versions(base_name)')

    conn.commit()

    # Count unique base names
    cursor.execute('SELECT COUNT(DISTINCT base_name) FROM doc_versions')
    unique_count = cursor.fetchone()[0]

    conn.close()
    return unique_count

def get_cached_document_content(xml_path: str, lang: str) -> Optional[bytes]:
    """
    Gets document content from the worker-local cache.
    Cache should be pre-populated by batch_extract_to_cache before calling this.
    """
    cache = worker_src_document_cache if lang == 'src' else worker_tgt_document_cache
    hit_key = f'{lang}_hits'
    miss_key = f'{lang}_misses'

    if xml_path in cache:
        worker_cache_stats[hit_key] += 1
        return cache[xml_path]

    # Cache miss - file wasn't pre-fetched (shouldn't happen often)
    worker_cache_stats[miss_key] += 1
    return None

def parse_xml_from_content(content: Optional[bytes]) -> Optional[ET.ElementTree]:
    """Safely parses XML content from bytes into an ElementTree."""
    if content is None:
        return None
    try:
        import io
        return ET.parse(io.BytesIO(content))
    except ET.ParseError as e:
        logger.warning(f"Skipping malformed XML file: {e}")
        return None
    except Exception as e:
        logger.error(f"Error parsing XML content: {e}")
        return None

def get_document_sentences(doc_tree: ET.ElementTree, para_info: List[Dict]) -> List[str]:
    """Extracts all specified sentence IDs from a parsed XML document tree."""
    if not para_info or not doc_tree:
        return []
    
    matches = []
    for para_data in para_info:
        if not para_data:
            continue
            
        paragraph = doc_tree.find(f".//P[@id='{para_data['paragraph_num']}']")
        if paragraph is not None:
            sentence = paragraph.find(f"./s[@id='{para_data['paragraph_num']}.{para_data['sentence_num']}']")
            if sentence is not None:
                matches.append(sentence.get('id'))
    
    return matches

def get_document_versions_from_db(base_path: str, lang: str) -> List[str]:
    """Query SQLite database for document versions."""
    db_conn = worker_src_db_conn if lang == 'src' else worker_tgt_db_conn
    cursor = db_conn.cursor()
    cursor.execute('SELECT version_path FROM doc_versions WHERE base_name = ?', (base_path,))
    return [row[0] for row in cursor.fetchall()]

def get_document_matches_optimized(
    base_path: str,
    lang: str,
    para_info: List[Dict]
) -> List[Tuple[str, List[str]]]:
    """
    CRITICAL OPTIMIZATION: Get matches for a document, avoiding parsing when possible.
    """
    fast_path_key = f'{lang}_fast_path'

    all_matches = []
    versions = get_document_versions_from_db(base_path, lang)

    if not versions:
        return all_matches

    if len(versions) == 1:
        # Fast path: single version, no need to read/parse XML
        worker_cache_stats[fast_path_key] += 1
        matches = [
            f"{p['paragraph_num']}.{p['sentence_num']}"
            for p in para_info if p
        ]
        if matches:
            all_matches.append((versions[0], matches))
        return all_matches

    # Slow path: multiple versions, must read and parse to find correct one
    for version_path in versions:
        content = get_cached_document_content(version_path, lang)
        if content:
            doc_tree = parse_xml_from_content(content)
            if doc_tree:
                matches = get_document_sentences(doc_tree, para_info)
                if matches:
                    all_matches.append((version_path, matches))

    return all_matches

def process_alignment_batch(
    batch: List[ET.Element],
    src_lang: str,
    tgt_lang: str
) -> Tuple[Dict[Tuple[str, str], List[Tuple[List[str], List[str], AlignmentScores]]], Dict[str, int]]:
    """
    Processes a batch of <tu> elements using worker-local resources.
    Uses batch pre-fetch to extract all needed files upfront.
    """
    # Snapshot stats at start to compute delta for this batch only
    stats_before = dict(worker_cache_stats) if worker_cache_stats else {}

    # Phase 1: Collect all files needed for this batch and extract them
    src_files_needed, tgt_files_needed = collect_batch_file_requirements(batch, src_lang, tgt_lang)

    # Batch extract to cache (one unzip call per ~1500 files)
    if src_files_needed:
        batch_extract_to_cache(src_files_needed, worker_src_zip_path, worker_src_document_cache)
    if tgt_files_needed:
        batch_extract_to_cache(tgt_files_needed, worker_tgt_zip_path, worker_tgt_document_cache)

    # Phase 2: Process the batch using cached content
    local_doc_alignments = defaultdict(list)
    seen_alignments = defaultdict(set)

    for tu in batch:
        src_tuv = tu.find(f".//tuv[@{{{XML_NS['xml']}}}lang='{src_lang}']")
        tgt_tuv = tu.find(f".//tuv[@{{{XML_NS['xml']}}}lang='{tgt_lang}']")
        
        if src_tuv is None or tgt_tuv is None:
            continue
        
        src_doc_info_list = extract_document_info_from_tuv(src_tuv)
        tgt_doc_info_list = extract_document_info_from_tuv(tgt_tuv)
        scores_list = extract_scores_from_tu(tu)
        
        min_len = min(len(src_doc_info_list), len(tgt_doc_info_list), len(scores_list))
        
        for i in range(min_len):
            src_doc_url, src_para_id, _ = src_doc_info_list[i]
            tgt_doc_url, tgt_para_id, _ = tgt_doc_info_list[i]
            scores = scores_list[i]
            
            src_base_path = sanitize_url_to_filename(src_doc_url)
            tgt_base_path = sanitize_url_to_filename(tgt_doc_url)
            
            src_para_info = parse_paragraph_id(src_para_id)
            tgt_para_info = parse_paragraph_id(tgt_para_id)
            
            if not src_para_info or not tgt_para_info:
                continue

            src_matches = get_document_matches_optimized(src_base_path, 'src', src_para_info)
            tgt_matches = get_document_matches_optimized(tgt_base_path, 'tgt', tgt_para_info)
            
            for src_file, src_sents in src_matches:
                for tgt_file, tgt_sents in tgt_matches:
                    doc_pair = (src_file, tgt_file)
                    
                    alignment_key = (tuple(sorted(src_sents)), tuple(sorted(tgt_sents)))
                    if alignment_key in seen_alignments[doc_pair]:
                        continue
                    
                    seen_alignments[doc_pair].add(alignment_key)
                    
                    src_sents.sort(key=lambda x: tuple(map(int, x.split('.'))))
                    tgt_sents.sort(key=lambda x: tuple(map(int, x.split('.'))))
                    
                    local_doc_alignments[doc_pair].append((src_sents, tgt_sents, scores))
        
        tu.clear()

    for alignments in local_doc_alignments.values():
        alignments.sort(key=lambda x: tuple(map(int, x[0][0].split('.'))))

    # Clear caches to free memory before next batch
    worker_src_document_cache.clear()
    worker_tgt_document_cache.clear()

    # Return alignments and delta stats for this batch only
    stats_after = dict(worker_cache_stats) if worker_cache_stats else {}
    stats_delta = {
        key: stats_after.get(key, 0) - stats_before.get(key, 0)
        for key in ['src_hits', 'src_misses', 'src_fast_path', 'tgt_hits', 'tgt_misses', 'tgt_fast_path']
    }
    return local_doc_alignments, stats_delta

def create_alignments_database(db_path: Path) -> sqlite3.Connection:
    """Create a SQLite database for storing alignments with deduplication."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE alignments (
            id INTEGER PRIMARY KEY,
            src_doc TEXT NOT NULL,
            tgt_doc TEXT NOT NULL,
            src_sents TEXT NOT NULL,
            tgt_sents TEXT NOT NULL,
            aligner_score TEXT,
            bicleaner_score TEXT,
            bifixer_score TEXT,
            sort_key TEXT NOT NULL
        )
    ''')

    # Unique constraint for deduplication (using sorted sentence IDs)
    cursor.execute('''
        CREATE UNIQUE INDEX idx_unique_alignment
        ON alignments(src_doc, tgt_doc, src_sents, tgt_sents)
    ''')

    conn.commit()
    return conn


def insert_batch_results_to_db(conn: sqlite3.Connection, batch_results: Dict[Tuple[str, str], list]):
    """Insert a batch of alignment results into the database, ignoring duplicates."""
    cursor = conn.cursor()

    rows = []
    for (src_doc, tgt_doc), alignments in batch_results.items():
        for src_sents, tgt_sents, scores in alignments:
            # Create sort key from first sentence ID for ordering
            sort_key = '.'.join(f'{int(x):010d}' for x in src_sents[0].split('.'))
            # Store sentence lists as sorted, joined strings for deduplication
            src_sents_key = ' '.join(sorted(src_sents))
            tgt_sents_key = ' '.join(sorted(tgt_sents))

            rows.append((
                src_doc, tgt_doc,
                src_sents_key, tgt_sents_key,
                scores.aligner, scores.bicleaner, scores.bifixer,
                sort_key
            ))

    cursor.executemany('''
        INSERT OR IGNORE INTO alignments
        (src_doc, tgt_doc, src_sents, tgt_sents, aligner_score, bicleaner_score, bifixer_score, sort_key)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', rows)

    conn.commit()


def finalize_alignments_db(conn: sqlite3.Connection):
    """Create index for efficient querying after all inserts are done."""
    logger.info("Creating database indexes for final output...")
    cursor = conn.cursor()
    cursor.execute('CREATE INDEX idx_doc_pair_sort ON alignments(src_doc, tgt_doc, sort_key)')
    conn.commit()


def get_alignment_stats(conn: sqlite3.Connection) -> Tuple[int, int]:
    """Get count of unique document pairs and total alignments."""
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(DISTINCT src_doc || tgt_doc) FROM alignments')
    doc_pairs = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM alignments')
    total_alignments = cursor.fetchone()[0]
    return doc_pairs, total_alignments


def stream_alignments_from_db(conn: sqlite3.Connection):
    """Generator that yields (doc_pair, alignments) from the database, sorted."""
    cursor = conn.cursor()

    # Get all unique document pairs, sorted
    cursor.execute('''
        SELECT DISTINCT src_doc, tgt_doc
        FROM alignments
        ORDER BY src_doc, tgt_doc
    ''')
    doc_pairs = cursor.fetchall()

    for src_doc, tgt_doc in doc_pairs:
        # Get all alignments for this document pair, sorted by sort_key
        cursor.execute('''
            SELECT src_sents, tgt_sents, aligner_score, bicleaner_score, bifixer_score
            FROM alignments
            WHERE src_doc = ? AND tgt_doc = ?
            ORDER BY sort_key
        ''', (src_doc, tgt_doc))

        alignments = []
        for row in cursor.fetchall():
            src_sents = row[0].split()
            tgt_sents = row[1].split()
            # Re-sort by numeric value for output (stored sorted alphabetically for dedup)
            src_sents.sort(key=lambda x: tuple(map(int, x.split('.'))))
            tgt_sents.sort(key=lambda x: tuple(map(int, x.split('.'))))
            scores = AlignmentScores(aligner=row[2], bicleaner=row[3], bifixer=row[4])
            alignments.append((src_sents, tgt_sents, scores))

        yield ((src_doc, tgt_doc), alignments)

def format_score_attributes(scores: AlignmentScores) -> str:
    """Formats alignment scores as a string of XML attributes."""
    attrs = []
    if scores.aligner is not None:
        attrs.append(f'aligner-score="{scores.aligner}"')
    if scores.bicleaner is not None:
        attrs.append(f'bicleaner-score="{scores.bicleaner}"')
    if scores.bifixer is not None:
        attrs.append(f'bifixer-score="{scores.bifixer}"')
    return ' '.join(attrs)

# --- CHANGE START: Logic to sort docs and sentences by language is now inside this function ---
def write_streaming_xml(
    output_file: Path, 
    alignment_generator, 
    total_groups: int, 
    src_lang: str, 
    tgt_lang: str
):
    """
    Writes the final cesAlign XML file, ensuring fromDoc/toDoc are alphabetically sorted by language.
    """
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Determine the correct alphabetical order for the output attributes.
    # lang1 will be the language that comes first alphabetically (e.g., 'en').
    # lang2 will be the language that comes second (e.g., 'xh').
    lang1, lang2 = sorted([src_lang, tgt_lang])
    
    # This flag tells us if the original src_lang (from the folder name) needs
    # to be swapped with the target language to maintain alphabetical order in the output.
    swap_langs = (src_lang != lang1)

    with gzip.open(output_file, 'wt', encoding='utf-8') as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n')
        f.write('<!DOCTYPE cesAlign PUBLIC "-//CES//DTD XML cesAlign//EN" "">\n')
        f.write('<cesAlign version="1.0">\n')
        
        pbar = tqdm(alignment_generator, desc="Writing XML link groups", total=total_groups, unit="group")
        # The generator yields docs and sentences based on the *original* src/tgt order.
        for (original_src_doc, original_tgt_doc), alignments in pbar:
            
            # If swap_langs is True, we write the target doc as fromDoc and source doc as toDoc.
            from_doc = original_tgt_doc if swap_langs else original_src_doc
            to_doc = original_src_doc if swap_langs else original_tgt_doc
            
            from_doc_attr = f"{lang1}/{from_doc}"
            to_doc_attr = f"{lang2}/{to_doc}"
            
            f.write(f'  <linkGrp targType="s" fromDoc="{from_doc_attr}" toDoc="{to_doc_attr}">\n')
            
            for original_src_sents, original_tgt_sents, scores in alignments:
                src_ids = ' '.join(original_src_sents)
                tgt_ids = ' '.join(original_tgt_sents)
                score_attrs = format_score_attributes(scores)
                
                # The sentence IDs in xtargets must also be swapped if the languages are swapped.
                xtargets_attr = f'xtargets="{tgt_ids};{src_ids}"' if swap_langs else f'xtargets="{src_ids};{tgt_ids}"'

                if score_attrs:
                    f.write(f'    <link {xtargets_attr} {score_attrs} />\n')
                else:
                    f.write(f'    <link {xtargets_attr} />\n')
            
            f.write('  </linkGrp>\n')
        
        f.write('</cesAlign>\n')
# --- CHANGE END ---

def count_total_tus(tmx_file_path: Path) -> int:
    """Efficiently counts the total number of <tu> elements for the progress bar."""
    logger.info(f"First pass: Counting total translation units in {tmx_file_path.name}...")
    count = 0
    try:
        with gzip.open(tmx_file_path, 'rt', encoding='utf-8', errors='ignore') as f:
            for event, elem in tqdm(ET.iterparse(f, events=('end',))):
                if elem.tag == 'tu':
                    count += 1
                elem.clear()
        logger.info(f"Found {count:,} total translation units.")
        return count
    except Exception as e:
        logger.error(f"Could not count TUs due to an error: {e}. Progress bar total will be approximate.")
        return 0

def chunk_iterator(iterator, chunk_size):
    """Splits an iterator into chunks of a specified size."""
    iterator = iter(iterator)
    return iter(lambda: list(islice(iterator, chunk_size)), [])

def verify_alignments(folder: str, output: str, num_cpus: int, batch_size: int):
    """Main function to drive the alignment verification process."""
    folder_path = Path(folder)
    output_path = Path(output)
    
    lang_pair_original = folder_path.name
    try:
        # These are the original languages based on the input folder name
        src_lang, tgt_lang = lang_pair_original.split('-')
    except ValueError:
        logger.error(f"Folder name '{lang_pair_original}' is not in the expected 'src-tgt' format. Exiting.")
        return

    # Sort languages alphabetically for the output filename
    sorted_langs = sorted([src_lang, tgt_lang])
    lang_pair_sorted = f"{sorted_langs[0]}-{sorted_langs[1]}"

    logger.info(f"Processing language pair: {src_lang}-{tgt_lang}")

    tmx_files = list(folder_path.glob('*all_collections.tmx_ids.gz'))
    if not tmx_files:
        logger.error(f"No '*.tmx.gz' file found in {folder_path}. Exiting.")
        return
    tmx_file = tmx_files[0]
    if len(tmx_files) > 1:
        logger.warning(f"Multiple TMX files found; using the first one: {tmx_file.name}")

    docs_dir = output_path / 'docs'
    src_docs_zip = docs_dir / f"{src_lang}-deduped-docs.zip"
    tgt_docs_zip = docs_dir / f"{tgt_lang}-deduped-docs.zip"
    
    alignments_dir = output_path / 'alignments'
    # Use the sorted name for the alignment file
    alignment_file = alignments_dir / f"{lang_pair_sorted}.alignments.gz"

    for f in [tmx_file, src_docs_zip, tgt_docs_zip]:
        if not f.exists():
            logger.error(f"Required file not found: {f}. Exiting.")
            return
            
    logger.info(f"Input TMX: {tmx_file}")
    logger.info(f"Source Docs: {src_docs_zip}")
    logger.info(f"Target Docs: {tgt_docs_zip}")
    logger.info(f"Output Alignments: {alignment_file}")

    total_tus = count_total_tus(tmx_file)
    total_batches = math.ceil(total_tus / batch_size) if total_tus > 0 else None

    # Create SQLite databases for doc versions (avoids 100GB+ in-memory dicts)
    global shared_src_db_path, shared_tgt_db_path

    # Create temp directory for databases
    temp_dir = Path(tempfile.mkdtemp(prefix='alignment_db_'))
    shared_src_db_path = temp_dir / 'src_doc_versions.db'
    shared_tgt_db_path = temp_dir / 'tgt_doc_versions.db'

    logger.info(f"Creating SQLite databases in {temp_dir}")

    # Scan ZIPs using unzip command (avoids loading 88M entries into Python memory)
    logger.info("Building SQLite index for source documents...")
    src_unique_count = scan_zip_to_sqlite_via_unzip(src_docs_zip, shared_src_db_path)
    logger.info(f"Found {src_unique_count:,} unique source documents")

    logger.info("Building SQLite index for target documents...")
    tgt_unique_count = scan_zip_to_sqlite_via_unzip(tgt_docs_zip, shared_tgt_db_path)
    logger.info(f"Found {tgt_unique_count:,} unique target documents")

    logger.info(f"Initializing worker pool with {num_cpus} CPUs...")
    pool = mp.Pool(num_cpus, initializer=worker_initializer,
                   initargs=(src_docs_zip, tgt_docs_zip))
    
    # Create SQLite database for alignments (avoids keeping all results in memory)
    alignments_db_path = temp_dir / 'alignments.db'
    alignments_conn = create_alignments_database(alignments_db_path)
    logger.info(f"Created alignments database: {alignments_db_path}")

    try:
        # process_func uses the original src_lang and tgt_lang to find the right data in the TMX
        process_func = partial(process_alignment_batch,
                               src_lang=src_lang,
                               tgt_lang=tgt_lang)

        aggregated_stats = {
            'src_hits': 0, 'src_misses': 0, 'src_fast_path': 0,
            'tgt_hits': 0, 'tgt_misses': 0, 'tgt_fast_path': 0,
        }

        with gzip.open(tmx_file, 'rt', encoding='utf-8', errors='ignore') as f:
            context = ET.iterparse(f, events=('end',))
            tu_iterator = (elem for _, elem in context if elem.tag == 'tu')
            batches = chunk_iterator(tu_iterator, batch_size)

            logger.info(f"Processing {total_tus:,} TUs in {total_batches or 'unknown'} batches of size {batch_size}...")

            results_iterator = pool.imap_unordered(process_func, batches)

            pbar = tqdm(results_iterator, desc="Processing TMX batches", total=total_batches, unit="batch")
            for result in pbar:
                if result:
                    alignments, stats = result
                    # Insert batch results directly to database instead of keeping in memory
                    insert_batch_results_to_db(alignments_conn, alignments)
                    # Aggregate stats
                    for key in aggregated_stats:
                        aggregated_stats[key] += stats.get(key, 0)

        # Log aggregated cache statistics
        src_total = aggregated_stats['src_hits'] + aggregated_stats['src_misses']
        tgt_total = aggregated_stats['tgt_hits'] + aggregated_stats['tgt_misses']
        src_hit_rate = (aggregated_stats['src_hits'] / src_total * 100) if src_total > 0 else 0
        tgt_hit_rate = (aggregated_stats['tgt_hits'] / tgt_total * 100) if tgt_total > 0 else 0
        logger.info(
            f"Cache stats (aggregated): "
            f"SRC[hits={aggregated_stats['src_hits']:,}, misses={aggregated_stats['src_misses']:,}, "
            f"hit_rate={src_hit_rate:.1f}%, fast_path={aggregated_stats['src_fast_path']:,}] "
            f"TGT[hits={aggregated_stats['tgt_hits']:,}, misses={aggregated_stats['tgt_misses']:,}, "
            f"hit_rate={tgt_hit_rate:.1f}%, fast_path={aggregated_stats['tgt_fast_path']:,}]"
        )

        # Finalize database with indexes for efficient querying
        finalize_alignments_db(alignments_conn)

        # Get statistics from database
        total_link_groups, total_alignments = get_alignment_stats(alignments_conn)
        logger.info(f"Found {total_link_groups:,} unique document pairs with {total_alignments:,} alignments.")

        logger.info(f"Writing final alignment file to {alignment_file}...")
        # Stream alignments from database - no need to load all into memory
        alignment_generator = stream_alignments_from_db(alignments_conn)
        # Pass the original src_lang and tgt_lang to the writer function.
        # The writer will handle the logic for sorting them alphabetically.
        write_streaming_xml(
            alignment_file,
            alignment_generator,
            total_link_groups,
            src_lang,
            tgt_lang
        )
        logger.info("Alignment file created successfully.")
        
    finally:
        logger.info("Shutting down worker pool...")
        pool.close()
        pool.join()
        logger.info("Worker pool shutdown complete.")

        # Close alignments database connection
        if alignments_conn:
            alignments_conn.close()

        # Cleanup temp SQLite databases
        import shutil
        if temp_dir.exists():
            logger.info(f"Cleaning up temp directory: {temp_dir}")
            shutil.rmtree(temp_dir, ignore_errors=True)

    logger.info("Script completed successfully.")
    os._exit(0)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify document alignments with new requirements")
    parser.add_argument("--folder", required=True, help="Path to the folder containing the TMX file (folder name gives the language pair, e.g., 'xh-en')")
    parser.add_argument("--output", required=True, help="Output folder; docs zip files are in <output>/docs and alignment file is written to <output>/alignments/")
    parser.add_argument("--num_cpu", type=int, default=max(1, os.cpu_count() - 2), help="Number of CPU cores to use for processing (defaults to almost all available)")
    parser.add_argument("--batch_size", type=int, default=50000, help="Number of alignment units per batch")
    args = parser.parse_args()
    
    verify_alignments(args.folder, args.output, args.num_cpu, args.batch_size)
