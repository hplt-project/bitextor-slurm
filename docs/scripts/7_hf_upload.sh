#!/bin/bash

# ====================
# Options for sbatch
# ====================
#SBATCH --job-name="upload_HF"
#SBATCH --output=logs/upload_%j.out    # Stdout 
#SBATCH --error=logs/upload_%j.err     # Stderr 
#SBATCH --account=project_465001864
#SBATCH --partition=small
#SBATCH --cpus-per-task=8
#SBATCH --ntasks=1
#SBATCH --mem=150G
#SBATCH --time=24:00:00

module --force purge
module load LUMI
clean_folder=/scratch/project_465001864/bitexting_v3/sharded_data/clean/

export HF_DATASETS_CACHE="/scratch/project_465001864/hf-cache"
export HF_HOME="/scratch/project_465001864/hf-home"
#TODO make sure HF_TOKEN is set


singularity exec \
  --env HF_TOKEN=$HF_TOKEN \
  -B /scratch/project_465001864 \
  python.sif \
  python -u 7_hf_upload.py \
    --config="${1}" 


#python /scratch/project_465001864/malikbha/hplt-doc/doc_extraction.py --config="${1}" 
