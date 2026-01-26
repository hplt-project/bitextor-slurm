#!/bin/bash

# ====================
# Options for sbatch
# ====================
#SBATCH --job-name="upload_HF"
#SBATCH --account=project_465001864
#SBATCH --partition=small
#SBATCH --cpus-per-task=8
#SBATCH --ntasks=1
#SBATCH --mem=150G
#SBATCH --time=24:00:00
#SBATCH --output=/scratch/project_465001864/malikbha/hplt-doc/logs/output_%j.log
#SBATCH --error=/scratch/project_465001864/malikbha/hplt-doc/logs/error_%j.log

module --force purge
module load cray-python
source /scratch/project_465001864/malikbha/bitextor-slurm/dochplt/bin/activate

export HF_DATASETS_CACHE="/scratch/project_465001864/malikbha/hplt-doc/cache"
export HF_HOME="/scratch/project_465001864/malikbha/hplt-doc/cache"
#TODO make sure HF_TOKEN is set


python /scratch/project_465001864/malikbha/hplt-doc/doc_extraction.py --config="${1}" 