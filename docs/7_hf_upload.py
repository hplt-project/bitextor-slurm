import zipfile
import io
import gzip
import os
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from io import BytesIO


# get docs name - intersection
# maintain dict to add info 
'''

eng_docs = dict()
lang_docs = dict()
doc_list = []

cy_docs_path = '/scratch/project_462000764/dayyan/combined/docs/cy-deduped-docs.zip'
en_docs_path = '/scratch/project_462000764/dayyan/combined/docs/en-deduped-docs.zip'

with gzip.open('/scratch/project_462000764/dayyan/combined/alignments/cy-en.alignments.gz', 'rt', encoding='utf-8') as f:
    tree = ET.parse(f)
    alignment_root = tree.getroot()

for num, linkGrp in enumerate(alignment_root.findall('linkGrp')):
    if num < 15:
        print(linkGrp)
        from_doc = linkGrp.get('fromDoc').split("en/")[-1]
        to_doc = linkGrp.get('toDoc').split("xh/")[-1]

        doc_list.append((from_doc, to_doc))
        
        eng_docs[from_doc] = {}
        lang_docs[to_doc] = {}

        for link in linkGrp.findall('link'):
            xtargets = link.get('xtargets')
            print(f"  xtargets: {xtargets}")
            print(f"  aligner-score: {link.get('aligner-score')}")
            print(f"  bicleaner-score: {link.get('bicleaner-score')}")
            print(f"  bifixer-score: {link.get('bifixer-score')}")

        # for link in linkGrp.findall('link'):
        #     print('Link attributes:')
        #     for attr_name, attr_value in link.attrib.items():
        #         print(f'  {attr_name}: {attr_value}')



# # technically all 3 should be same
# src_zip_file = ZipFile(en_docs_path, 'r')
tgt_zip_file = ZipFile(cy_docs_path, 'r')


def _get_doc_content_from_zip(zip_file, doc):
    ids = []
    sents = []

    with zip_file.open(doc) as f:
        content = f.read()

    xml_string = content.decode('utf-8')
    
    with io.StringIO(xml_string) as f:
        for event, elem in ET.iterparse(f, events=('end',)):
            if elem.tag == 's':
                ids.append(elem.attrib['id'])
                sents.append(elem.text.strip())
                elem.clear()
    
    return ids, sents


for num, linkGrp in enumerate(alignment_root.findall('linkGrp')):
    # if num < 15:
    print("***")
    from_doc = linkGrp.get('fromDoc').split("cy/")[-1]
    to_doc = linkGrp.get('toDoc').split("en/")[-1]

    # eng_ids, eng_sents = _get_doc_content_from_zip(src_zip_file, from_doc)
    lang_ids, lang_sents = _get_doc_content_from_zip(tgt_zip_file, from_doc)
        # assert len(lang_ids) == len(lang_sents)



# # src_zip_file.close()
# tgt_zip_file.close()

'''
#################

from huggingface_hub import login
from datasets import load_dataset
import argparse

parser = argparse.ArgumentParser(description='Process arguments')

parser.add_argument('--config', type=str, help='config')

args = parser.parse_args()

print(f"this is for {args.config}")

#hf_token = os.getenv("HF_TOKEN")
#if hf_token is None:
#    raise ValueError("HF_TOKEN environment variable not set")
#login(hf_token)
d = load_dataset("/home/bhaddow/code/bitextor-slurm/docs/docmt_hplt.py", args.config, trust_remote_code=True)
d.push_to_hub("bhaddow/DocHPLTv2", max_shard_size="2GB", config_name=args.config)