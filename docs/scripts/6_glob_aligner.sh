#!/bin/bash -l
#SBATCH --job-name=glob_align_job           # Job name
#SBATCH --output=logs/glob_align_job_%A_%a.out    # Stdout (%A = job ID, %a = array index)
#SBATCH --error=logs/glob_align_job_%A_%a.err     # Stderr (%A = job ID, %a = array index)
#SBATCH --partition=small             # Partition name
#SBATCH --ntasks=1                     # One task
#SBATCH --cpus-per-task=8            # Number of cores
#SBATCH --time=1-00:00:00              # Run time
#SBATCH --account=project_465001864    # Billing project

module --force purge
module load LUMI
module load cray-python


# python3 -u 6_alignment_combined.py --folder /fs/alvis0/bhaddow/dochplt/bitexting_v3-clean/da-en/ --output /fs/alvis0/bhaddow/dochplt/bitexting_v3-clean/combined --num_cpu 8 --batch_size 2000

clean_folder=/scratch/project_465001864/bitexting_v3/sharded_data/clean/

python -u ../6_alignment_combined.py \
    --folder ${clean_folder}/"$1" \
    --output ${clean_folder}/combined" \
    --num_cpu 8 \
    --batch_size 10000

