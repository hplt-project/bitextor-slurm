import argparse
import gzip
import re
try:
    from lxml import etree as ET
    USING_LXML = True
except ImportError:
    import xml.etree.ElementTree as ET
    USING_LXML = False
from pathlib import Path
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
import shutil
import sqlite3
import subprocess
import tempfile
import time

# --- Configuration ---
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
logger.info(f"Using {'lxml' if USING_LXML else 'ElementTree'} for XML parsing")

# Register the XML namespace to correctly parse 'xml:lang' attributes
if not USING_LXML:
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
worker_src_db_conn: Optional[sqlite3.Connection] = None
worker_tgt_db_conn: Optional[sqlite3.Connection] = None
worker_path_stats: Optional[Dict[str, int]] = None
worker_src_content_cache: Optional[Dict[str, Optional[bytes]]] = None  # Cache for source document extractions
worker_timing_stats: Optional[Dict[str, float]] = None  # Timing instrumentation
worker_batch_extract_dir: Optional[Path] = None  # Temp directory for batch extractions

def worker_initializer(src_zip_path: Path, tgt_zip_path: Path):
    """
    Initialize each worker. Stores ZIP paths and connects to SQLite databases.
    Documents are extracted on-demand using unzip -p.
    """
    global worker_src_zip_path, worker_tgt_zip_path
    global worker_src_db_conn, worker_tgt_db_conn
    global worker_path_stats, worker_src_content_cache, worker_timing_stats

    try:
        worker_src_zip_path = src_zip_path
        worker_tgt_zip_path = tgt_zip_path

        # Connect to SQLite databases (read-only, shared cache for efficiency)
        worker_src_db_conn = sqlite3.connect(f"file:{shared_src_db_path}?mode=ro", uri=True)
        worker_tgt_db_conn = sqlite3.connect(f"file:{shared_tgt_db_path}?mode=ro", uri=True)

        # Initialize path statistics
        worker_path_stats = {
            'src_fast_path': 0,
            'src_slow_path': 0,
            'tgt_fast_path': 0,
            'tgt_slow_path': 0,
        }

        # Initialize source content cache (source has multi-version docs, target doesn't)
        worker_src_content_cache = {}

        # Initialize timing stats
        worker_timing_stats = {
            'unzip_time': 0.0,
            'unzip_calls': 0,
            'xml_parse_time': 0.0,
            'xml_parse_calls': 0,
            'sqlite_query_time': 0.0,
            'sqlite_query_calls': 0,
        }

        logger.info(f"Worker {mp.current_process().pid} initialized successfully.")

        atexit.register(worker_cleanup)

    except Exception as e:
        logger.error(f"Failed to initialize worker {mp.current_process().pid}: {e}")
        raise

def worker_cleanup():
    """Clean up worker resources when a process terminates."""
    global worker_src_db_conn, worker_tgt_db_conn
    try:
        # Close SQLite connections
        if worker_src_db_conn:
            worker_src_db_conn.close()
        if worker_tgt_db_conn:
            worker_tgt_db_conn.close()

        logger.debug(f"Worker {mp.current_process().pid} cleanup complete.")
    except Exception as e:
        logger.debug(f"Error during worker cleanup in {mp.current_process().pid}: {e}")

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

def batch_extract_documents(xml_paths: List[str], lang: str, extract_dir: Path) -> int:
    """
    Extract multiple documents from ZIP in a single unzip call.
    Chunks into multiple calls if too many files (to avoid command line length limits).
    Returns the number of files successfully extracted.
    """
    if not xml_paths:
        return 0

    zip_path = worker_src_zip_path if lang == 'src' else worker_tgt_zip_path

    # Chunk to avoid hitting command line length limits (~2MB on Linux)
    # Conservative limit: ~5000 files per call (assuming ~200 char avg path)
    CHUNK_SIZE = 5000
    total_extracted = 0

    for i in range(0, len(xml_paths), CHUNK_SIZE):
        chunk = xml_paths[i:i + CHUNK_SIZE]
        try:
            t0 = time.perf_counter()
            # unzip can extract multiple files at once: unzip archive.zip file1 file2 ... -d outdir
            cmd = ['unzip', '-o', '-q', str(zip_path)] + chunk + ['-d', str(extract_dir)]
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=300  # Longer timeout for batch extraction
            )
            if worker_timing_stats is not None:
                worker_timing_stats['unzip_time'] += time.perf_counter() - t0
                worker_timing_stats['unzip_calls'] += 1

            # Count extracted files (some may not exist in archive)
            extracted = sum(1 for p in chunk if (extract_dir / p).exists())
            total_extracted += extracted
        except subprocess.TimeoutExpired:
            logger.warning(f"Timeout batch extracting {len(chunk)} files from {zip_path.name}")
        except Exception as e:
            logger.warning(f"Error batch extracting chunk: {e}")

    return total_extracted


def extract_document_content(xml_path: str, lang: str) -> Optional[bytes]:
    """
    Extract document content. First checks batch extraction directory,
    then falls back to individual unzip -p call.
    For source documents, uses a cache to avoid repeated extractions.
    """
    # Check cache for source documents (source has multi-version docs that get accessed repeatedly)
    if lang == 'src' and worker_src_content_cache is not None:
        if xml_path in worker_src_content_cache:
            return worker_src_content_cache[xml_path]

    # Check if file was batch-extracted
    if worker_batch_extract_dir is not None:
        extracted_path = worker_batch_extract_dir / xml_path
        if extracted_path.exists():
            try:
                content = extracted_path.read_bytes()
                # Cache source document content
                if lang == 'src' and worker_src_content_cache is not None:
                    worker_src_content_cache[xml_path] = content
                return content
            except Exception as e:
                logger.warning(f"Error reading extracted file {extracted_path}: {e}")

    # Fall back to individual extraction (should be rare after batch extraction)
    zip_path = worker_src_zip_path if lang == 'src' else worker_tgt_zip_path

    try:
        t0 = time.perf_counter()
        result = subprocess.run(
            ['unzip', '-p', str(zip_path), xml_path],
            capture_output=True,
            timeout=60
        )
        if worker_timing_stats is not None:
            worker_timing_stats['unzip_time'] += time.perf_counter() - t0
            worker_timing_stats['unzip_calls'] += 1
        content = result.stdout if result.returncode == 0 and result.stdout else None

        # Cache source document content
        if lang == 'src' and worker_src_content_cache is not None:
            worker_src_content_cache[xml_path] = content

        return content
    except subprocess.TimeoutExpired:
        logger.warning(f"Timeout extracting {xml_path} from {zip_path.name}")
        return None
    except Exception as e:
        logger.warning(f"Error extracting {xml_path}: {e}")
        return None

def parse_xml_from_content(content: Optional[bytes]) -> Optional[ET.ElementTree]:
    """Safely parses XML content from bytes into an ElementTree."""
    if content is None:
        return None
    try:
        import io
        t0 = time.perf_counter()
        tree = ET.parse(io.BytesIO(content))
        if worker_timing_stats is not None:
            worker_timing_stats['xml_parse_time'] += time.perf_counter() - t0
            worker_timing_stats['xml_parse_calls'] += 1
        return tree
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
    t0 = time.perf_counter()
    cursor.execute('SELECT version_path FROM doc_versions WHERE base_name = ?', (base_path,))
    result = [row[0] for row in cursor.fetchall()]
    if worker_timing_stats is not None:
        worker_timing_stats['sqlite_query_time'] += time.perf_counter() - t0
        worker_timing_stats['sqlite_query_calls'] += 1
    return result

def get_document_matches_optimized(
    base_path: str,
    lang: str,
    para_info: List[Dict]
) -> List[Tuple[str, List[str]]]:
    """
    Get matches for a document, avoiding parsing when possible.
    Fast path: single version documents don't need XML parsing.
    """
    all_matches = []
    versions = get_document_versions_from_db(base_path, lang)

    if not versions:
        return all_matches

    if len(versions) == 1:
        # Fast path: single version, no need to read/parse XML
        worker_path_stats[f'{lang}_fast_path'] += 1
        matches = [
            f"{p['paragraph_num']}.{p['sentence_num']}"
            for p in para_info if p
        ]
        if matches:
            all_matches.append((versions[0], matches))
        return all_matches

    # Slow path: multiple versions, must read and parse to find correct one
    worker_path_stats[f'{lang}_slow_path'] += 1
    for version_path in versions:
        content = extract_document_content(version_path, lang)
        if content:
            doc_tree = parse_xml_from_content(content)
            if doc_tree:
                matches = get_document_sentences(doc_tree, para_info)
                if matches:
                    all_matches.append((version_path, matches))

    return all_matches

def deserialize_tu_batch(batch: List[bytes]) -> List:
    """Deserialize a batch of TU elements from bytes (for lxml compatibility with multiprocessing)."""
    if USING_LXML:
        return [ET.fromstring(tu_bytes) for tu_bytes in batch]
    else:
        return batch  # ElementTree elements are already unpickled


def collect_slow_path_documents(
    batch: List,
    src_lang: str,
    tgt_lang: str
) -> Tuple[Set[str], Set[str], List[Tuple]]:
    """
    First pass: collect all documents needing slow-path extraction.
    Returns (src_paths_to_extract, tgt_paths_to_extract, parsed_tu_data).
    """
    # Deserialize if needed (lxml elements can't be pickled)
    tu_elements = deserialize_tu_batch(batch)

    src_paths_to_extract = set()
    tgt_paths_to_extract = set()
    parsed_tu_data = []

    for tu in tu_elements:
        src_tuv = tu.find(f".//tuv[@{{{XML_NS['xml']}}}lang='{src_lang}']")
        tgt_tuv = tu.find(f".//tuv[@{{{XML_NS['xml']}}}lang='{tgt_lang}']")

        if src_tuv is None or tgt_tuv is None:
            continue

        src_doc_info_list = extract_document_info_from_tuv(src_tuv)
        tgt_doc_info_list = extract_document_info_from_tuv(tgt_tuv)
        scores_list = extract_scores_from_tu(tu)

        min_len = min(len(src_doc_info_list), len(tgt_doc_info_list), len(scores_list))

        tu_entries = []
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

            # Check if slow path needed for src
            src_versions = get_document_versions_from_db(src_base_path, 'src')
            if len(src_versions) > 1:
                src_paths_to_extract.update(src_versions)

            # Check if slow path needed for tgt
            tgt_versions = get_document_versions_from_db(tgt_base_path, 'tgt')
            if len(tgt_versions) > 1:
                tgt_paths_to_extract.update(tgt_versions)

            tu_entries.append((
                src_base_path, tgt_base_path,
                src_para_info, tgt_para_info,
                src_versions, tgt_versions,
                scores
            ))

        if tu_entries:
            parsed_tu_data.append(tu_entries)

        tu.clear()

    return src_paths_to_extract, tgt_paths_to_extract, parsed_tu_data


def process_alignment_batch(
    batch: List[ET.Element],
    src_lang: str,
    tgt_lang: str
) -> Tuple[Dict[Tuple[str, str], List[Tuple[List[str], List[str], AlignmentScores]]], Dict[str, int]]:
    """
    Processes a batch of <tu> elements using worker-local resources.
    Uses two-pass approach: first collect needed documents, batch extract, then process.
    Returns (alignments, path_stats_delta, timing_stats).
    """
    global worker_batch_extract_dir

    # Snapshot stats at start to compute delta for this batch
    stats_before = dict(worker_path_stats)

    # === PASS 1: Collect all documents needing slow-path extraction ===
    src_paths_to_extract, tgt_paths_to_extract, parsed_tu_data = collect_slow_path_documents(
        batch, src_lang, tgt_lang
    )

    # === BATCH EXTRACTION ===
    # Create temp directory for this batch's extractions
    worker_batch_extract_dir = Path(tempfile.mkdtemp(prefix=f'batch_extract_{mp.current_process().pid}_'))

    try:
        # Batch extract all needed documents
        if src_paths_to_extract:
            src_extracted = batch_extract_documents(
                list(src_paths_to_extract), 'src', worker_batch_extract_dir
            )
            logger.debug(f"Batch extracted {src_extracted}/{len(src_paths_to_extract)} src documents")

        if tgt_paths_to_extract:
            tgt_extracted = batch_extract_documents(
                list(tgt_paths_to_extract), 'tgt', worker_batch_extract_dir
            )
            logger.debug(f"Batch extracted {tgt_extracted}/{len(tgt_paths_to_extract)} tgt documents")

        # === PASS 2: Process using pre-extracted documents ===
        local_doc_alignments = defaultdict(list)
        seen_alignments = defaultdict(set)

        for tu_entries in parsed_tu_data:
            for (src_base_path, tgt_base_path, src_para_info, tgt_para_info,
                 src_versions, tgt_versions, scores) in tu_entries:

                # Get matches (will use batch-extracted files)
                src_matches = get_document_matches_from_versions(
                    src_versions, 'src', src_para_info
                )
                tgt_matches = get_document_matches_from_versions(
                    tgt_versions, 'tgt', tgt_para_info
                )

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

        for alignments in local_doc_alignments.values():
            alignments.sort(key=lambda x: tuple(map(int, x[0][0].split('.'))))

    finally:
        # Clean up temp directory
        if worker_batch_extract_dir and worker_batch_extract_dir.exists():
            shutil.rmtree(worker_batch_extract_dir, ignore_errors=True)
        worker_batch_extract_dir = None

    # Compute stats delta for this batch
    stats_delta = {
        key: worker_path_stats[key] - stats_before[key]
        for key in worker_path_stats
    }

    # Include timing stats in result
    timing_snapshot = dict(worker_timing_stats) if worker_timing_stats else {}

    logger.debug(f"Batch loaded; worker_path_stats: {worker_path_stats}")
    logger.debug(f"worker_timing_stats: {worker_timing_stats}")
    return local_doc_alignments, stats_delta, timing_snapshot


def get_document_matches_from_versions(
    versions: List[str],
    lang: str,
    para_info: List[Dict]
) -> List[Tuple[str, List[str]]]:
    """
    Get matches for a document given its versions (already looked up).
    Fast path: single version documents don't need XML parsing.
    """
    all_matches = []

    if not versions:
        return all_matches

    if len(versions) == 1:
        # Fast path: single version, no need to read/parse XML
        worker_path_stats[f'{lang}_fast_path'] += 1
        matches = [
            f"{p['paragraph_num']}.{p['sentence_num']}"
            for p in para_info if p
        ]
        if matches:
            all_matches.append((versions[0], matches))
        return all_matches

    # Slow path: multiple versions, must read and parse to find correct one
    worker_path_stats[f'{lang}_slow_path'] += 1
    for version_path in versions:
        content = extract_document_content(version_path, lang)
        if content:
            doc_tree = parse_xml_from_content(content)
            if doc_tree:
                matches = get_document_sentences(doc_tree, para_info)
                if matches:
                    all_matches.append((version_path, matches))

    return all_matches

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
        with gzip.open(tmx_file_path, 'rb') as f:
            for event, elem in tqdm(ET.iterparse(f, events=('end',))):
                if elem.tag == 'tu':
                    count += 1
                elem.clear()
        logger.info(f"Found {count:,} total translation units.")
        return count
    except Exception as e:
        logger.error(f"Could not count TUs due to an error: {e}. Progress bar total will be approximate.")
        return 0

def serialize_element(elem) -> bytes:
    """Serialize an XML element to bytes for pickling (lxml compatibility)."""
    if USING_LXML:
        return ET.tostring(elem)
    else:
        return elem  # ElementTree elements can be pickled directly


def chunk_iterator(iterator, chunk_size, serialize=False):
    """Splits an iterator into chunks of a specified size, optionally serializing elements."""
    iterator = iter(iterator)
    if serialize:
        # Serialize elements for lxml multiprocessing compatibility
        def get_chunk():
            chunk = list(islice(iterator, chunk_size))
            if not chunk:
                return []
            serialized = []
            for elem in chunk:
                serialized.append(serialize_element(elem))
                elem.clear()  # Free memory after serializing
            return serialized
        return iter(get_chunk, [])
    else:
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

        # Aggregate path statistics
        aggregated_stats = {
            'src_fast_path': 0, 'src_slow_path': 0,
            'tgt_fast_path': 0, 'tgt_slow_path': 0,
        }
        # Aggregate timing statistics
        aggregated_timing = {
            'unzip_time': 0.0, 'unzip_calls': 0,
            'xml_parse_time': 0.0, 'xml_parse_calls': 0,
            'sqlite_query_time': 0.0, 'sqlite_query_calls': 0,
        }

        with gzip.open(tmx_file, 'rb') as f:
            context = ET.iterparse(f, events=('end',))
            tu_iterator = (elem for _, elem in context if elem.tag == 'tu')
            batches = chunk_iterator(tu_iterator, batch_size, serialize=USING_LXML)

            logger.info(f"Processing {total_tus:,} TUs in {total_batches or 'unknown'} batches of size {batch_size}...")

            results_iterator = pool.imap_unordered(process_func, batches)

            pbar = tqdm(results_iterator, desc="Processing TMX batches", total=total_batches, unit="batch")
            for result in pbar:
                if result:
                    alignments, stats, timing = result
                    # Insert batch results directly to database instead of keeping in memory
                    insert_batch_results_to_db(alignments_conn, alignments)
                    # Aggregate stats
                    for key in aggregated_stats:
                        aggregated_stats[key] += stats.get(key, 0)
                    # Aggregate timing
                    for key in aggregated_timing:
                        aggregated_timing[key] += timing.get(key, 0)

        # Log path statistics
        src_total = aggregated_stats['src_fast_path'] + aggregated_stats['src_slow_path']
        tgt_total = aggregated_stats['tgt_fast_path'] + aggregated_stats['tgt_slow_path']
        src_fast_pct = (aggregated_stats['src_fast_path'] / src_total * 100) if src_total > 0 else 0
        tgt_fast_pct = (aggregated_stats['tgt_fast_path'] / tgt_total * 100) if tgt_total > 0 else 0
        logger.info(
            f"Path stats: "
            f"SRC[fast={aggregated_stats['src_fast_path']:,}, slow={aggregated_stats['src_slow_path']:,}, fast%={src_fast_pct:.1f}%] "
            f"TGT[fast={aggregated_stats['tgt_fast_path']:,}, slow={aggregated_stats['tgt_slow_path']:,}, fast%={tgt_fast_pct:.1f}%]"
        )

        # Log timing breakdown
        unzip_avg = (aggregated_timing['unzip_time'] / aggregated_timing['unzip_calls'] * 1000) if aggregated_timing['unzip_calls'] > 0 else 0
        xml_avg = (aggregated_timing['xml_parse_time'] / aggregated_timing['xml_parse_calls'] * 1000) if aggregated_timing['xml_parse_calls'] > 0 else 0
        sqlite_avg = (aggregated_timing['sqlite_query_time'] / aggregated_timing['sqlite_query_calls'] * 1000) if aggregated_timing['sqlite_query_calls'] > 0 else 0
        logger.info(
            f"Timing stats (cumulative across all workers): "
            f"unzip[{aggregated_timing['unzip_time']:.1f}s total, {aggregated_timing['unzip_calls']:,} calls, {unzip_avg:.2f}ms avg] "
            f"xml_parse[{aggregated_timing['xml_parse_time']:.1f}s total, {aggregated_timing['xml_parse_calls']:,} calls, {xml_avg:.2f}ms avg] "
            f"sqlite[{aggregated_timing['sqlite_query_time']:.1f}s total, {aggregated_timing['sqlite_query_calls']:,} calls, {sqlite_avg:.3f}ms avg]"
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
