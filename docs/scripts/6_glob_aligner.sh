#!/bin/bash -l
#SBATCH --job-name=glob_align_job           # Job name
#SBATCH --output=logs/glob_align_%j.out    # Stdout 
#SBATCH --error=logs/glob_align_%j.err     # Stderr 
#SBATCH --partition=small             # Partition name
#SBATCH --ntasks=1                     # One task
#SBATCH --cpus-per-task=8            # Number of cores
#SBATCH --time=3-00:00:00              # Run time
#SBATCH --account=project_465001864    # Billing project

module --force purge
module load LUMI


clean_folder=/scratch/project_465001864/bitexting_v3/sharded_data/clean/


singularity exec \
  --env TMPDIR=/flash/project_465001864 \
  -B /scratch/project_465001864,/flash/project_465001864 \
  python.sif \
  python -u 6_alignment_combined.py \
    --folder ${clean_folder}/"$1" \
    --output ${clean_folder}/combined \
    --num_cpu 8 \
    --batch_size 10000

