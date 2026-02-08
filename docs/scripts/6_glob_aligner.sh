#!/bin/bash -l
#SBATCH --job-name=glob_align_job           # Job name
#SBATCH --output=logs/glob_align_%j.out    # Stdout 
#SBATCH --error=logs/glob_align_%j.err     # Stderr 
#SBATCH --partition=small             # Partition name
#SBATCH --ntasks=1                     # One task
#SBATCH --cpus-per-task=32          # Number of cores
#SBATCH --time=72:00:00              # Run time
#SBATCH --account=project_465001864    # Billing project
#SBATCH --mem=480G

module --force purge
module load LUMI


clean_folder=/scratch/project_465001864/bitexting_v3/sharded_data/clean/
output_folder=/flash/project_465001864/bitexting_v3/sharded_data/clean/


singularity exec \
  -B /scratch/project_465001864,/flash/project_465001864 \
  python.sif \
  python -u 6_alignment_combined.py \
    --folder ${clean_folder}/"$1" \
    --output ${output_folder}/combined \
    --num_cpu 32 \
    --batch_size 10000

#  --env TMPDIR=/flash/project_465001864 \
