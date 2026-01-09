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

# --- Worker-Specific Globals ---
worker_src_zip_file: Optional[ZipFile] = None
worker_tgt_zip_file: Optional[ZipFile] = None
worker_src_document_cache: Optional[Dict[str, bytes]] = None
worker_tgt_document_cache: Optional[Dict[str, bytes]] = None
worker_src_doc_versions: Optional[Dict[str, List[str]]] = None
worker_tgt_doc_versions: Optional[Dict[str, List[str]]] = None

def worker_initializer(src_zip_path: Path, tgt_zip_path: Path):
    """
    Initialize each worker. Each worker now performs its own ZIP scan.
    """
    global worker_src_zip_file, worker_tgt_zip_file
    global worker_src_document_cache, worker_tgt_document_cache
    global worker_src_doc_versions, worker_tgt_doc_versions 

    try:
        # Initialize file handles and caches
        worker_src_zip_file = ZipFile(src_zip_path, 'r')
        worker_tgt_zip_file = ZipFile(tgt_zip_path, 'r')
        worker_src_document_cache = {}
        worker_tgt_document_cache = {}

        logger.info(f"Worker {mp.current_process().pid}: Scanning source ZIP...")
        worker_src_doc_versions = scan_zip_for_documents(worker_src_zip_file)
        logger.info(f"Worker {mp.current_process().pid}: Scanning target ZIP...")
        worker_tgt_doc_versions = scan_zip_for_documents(worker_tgt_zip_file)
        logger.info(f"Worker {mp.current_process().pid} initialized successfully.")

        atexit.register(worker_cleanup)
      
    except Exception as e:
        logger.error(f"Failed to initialize worker {mp.current_process().pid}: {e}")
        raise

def worker_cleanup():
    """Clean up worker resources (file handles) when a process terminates."""
    global worker_src_zip_file, worker_tgt_zip_file
    try:
        if worker_src_zip_file:
            worker_src_zip_file.close()
        if worker_tgt_zip_file:
            worker_tgt_zip_file.close()
        logger.debug(f"Worker {mp.current_process().pid} cleaned up ZIP handles.")
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

def scan_zip_for_documents(zip_file: ZipFile) -> Dict[str, List[str]]:
    """
    Scans a zip file and creates a mapping of base filenames to their full versioned paths.
    """
    doc_versions = defaultdict(list)
    for filename in tqdm(zip_file.namelist(), desc=f"Scanning {os.path.basename(zip_file.filename)}", leave=False):
        if filename.endswith('.xml'):
            base = re.sub(r'_(\d+)\.xml$', '.xml', filename)
            doc_versions[base].append(filename)
    return doc_versions

def get_cached_document_content(xml_path: str, lang: str) -> Optional[bytes]:
    """
    Gets document content using the worker-local caching strategy for the specified language.
    """
    cache = worker_src_document_cache if lang == 'src' else worker_tgt_document_cache
    zip_file = worker_src_zip_file if lang == 'src' else worker_tgt_zip_file
    
    if xml_path in cache:
        return cache[xml_path]
    
    try:
        with zip_file.open(xml_path) as f:
            content = f.read()
            cache[xml_path] = content
            return content
    except KeyError:
        cache[xml_path] = None
        return None
    except Exception as e:
        logger.error(f"Worker {mp.current_process().pid} error reading {xml_path}: {e}")
        cache[xml_path] = None
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

def get_document_matches_optimized(
    base_path: str, 
    lang: str, 
    para_info: List[Dict]
) -> List[Tuple[str, List[str]]]:
    """
    CRITICAL OPTIMIZATION: Get matches for a document, avoiding parsing when possible.
    """
    doc_versions = worker_src_doc_versions if lang == 'src' else worker_tgt_doc_versions

    all_matches = []
    versions = doc_versions.get(base_path, [])
    
    if not versions:
        return all_matches
    
    if len(versions) == 1:
        matches = [
            f"{p['paragraph_num']}.{p['sentence_num']}"
            for p in para_info if p
        ]
        if matches:
            all_matches.append((versions[0], matches))
        return all_matches
    
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
) -> Dict[Tuple[str, str], List[Tuple[List[str], List[str], AlignmentScores]]]:
    """
    Processes a batch of <tu> elements using worker-local resources.
    """
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
    
    return local_doc_alignments

def merge_results(
    results: List[Dict[Tuple[str, str], list]], 
    all_doc_pairs: List[Tuple[str, str]]
) -> List[Tuple[Tuple[str, str], list]]:
    """Merges results from all worker batches into a final, sorted list."""
    logger.info("Merging results from all worker batches...")
    final_alignments = defaultdict(list)
    seen_alignments = defaultdict(set)

    for result_batch in tqdm(results, desc="Merging batches"):
        for doc_pair, alignments in result_batch.items():
            for alignment in alignments:
                src_key = tuple(sorted(alignment[0]))
                tgt_key = tuple(sorted(alignment[1]))
                align_key = (src_key, tgt_key)
                
                if align_key not in seen_alignments[doc_pair]:
                    final_alignments[doc_pair].append(alignment)
                    seen_alignments[doc_pair].add(align_key)

    for doc_pair in final_alignments:
        final_alignments[doc_pair].sort(key=lambda x: tuple(map(int, x[0][0].split('.'))))
    
    return [(pair, final_alignments[pair]) for pair in all_doc_pairs]

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
            for event, elem in ET.iterparse(f, events=('end',)):
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

    logger.info(f"Initializing Smart Worker pool with {num_cpus} CPUs...")
    pool = mp.Pool(num_cpus, initializer=worker_initializer, initargs=(src_docs_zip, tgt_docs_zip))
    
    try:
        # process_func uses the original src_lang and tgt_lang to find the right data in the TMX
        process_func = partial(process_alignment_batch,
                               src_lang=src_lang,
                               tgt_lang=tgt_lang)
        
        batch_results = []
        with gzip.open(tmx_file, 'rt', encoding='utf-8', errors='ignore') as f:
            context = ET.iterparse(f, events=('end',))
            tu_iterator = (elem for _, elem in context if elem.tag == 'tu')
            batches = chunk_iterator(tu_iterator, batch_size)
            
            logger.info(f"Processing {total_tus:,} TUs in {total_batches or 'unknown'} batches of size {batch_size}...")
            
            results_iterator = pool.imap_unordered(process_func, batches)
            
            pbar = tqdm(results_iterator, desc="Processing TMX batches", total=total_batches, unit="batch")
            for result in pbar:
                if result:
                    batch_results.append(result)

        logger.info("Aggregating results to find all unique document pairs...")
        all_doc_pairs = sorted(set(
            doc_pair
            for result in batch_results
            for doc_pair in result.keys()
        ))
        total_link_groups = len(all_doc_pairs)
        logger.info(f"Found {total_link_groups:,} unique document pairs to align.")

        final_sorted_alignments = merge_results(batch_results, all_doc_pairs)
        
        logger.info(f"Writing final alignment file to {alignment_file}...")
        # Pass the original src_lang and tgt_lang to the writer function.
        # The writer will handle the logic for sorting them alphabetically.
        write_streaming_xml(
            alignment_file, 
            final_sorted_alignments, 
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

    logger.info("Script completed successfully.")
    import os
    os._exit(0)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify document alignments with new requirements")
    parser.add_argument("--folder", required=True, help="Path to the folder containing the TMX file (folder name gives the language pair, e.g., 'xh-en')")
    parser.add_argument("--output", required=True, help="Output folder; docs zip files are in <output>/docs and alignment file is written to <output>/alignments/")
    parser.add_argument("--num_cpu", type=int, default=max(1, os.cpu_count() - 2), help="Number of CPU cores to use for processing (defaults to almost all available)")
    parser.add_argument("--batch_size", type=int, default=50000, help="Number of alignment units per batch")
    args = parser.parse_args()
    
    verify_alignments(args.folder, args.output, args.num_cpu, args.batch_size)
