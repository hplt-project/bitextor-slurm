import zipfile
import io
import gzip
import os
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from io import BytesIO

#################

from huggingface_hub import login
from datasets import load_dataset
import argparse

parser = argparse.ArgumentParser(description='Process arguments')

parser.add_argument('--config', type=str, help='config')

args = parser.parse_args()

print(f"this is for {args.config}")

d = load_dataset("/scratch/project_465001864/haddowba/bitexting/bitextor-slurm/docs/docmt_hplt.py", args.config, trust_remote_code=True)
d.push_to_hub("bhaddow/DocHPLTv2", max_shard_size="2GB", config_name=args.config)
