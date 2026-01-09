#!/bin/bash -l
#SBATCH --job-name=glob_debup_job           # Job name
#SBATCH --output=/scratch/project_465001864/malikbha/slurm_logs/glob_dedup_job_%A.out    # Stdout (%A = job ID, %a = array index)
#SBATCH --error=/scratch/project_465001864/malikbha/slurm_logs/glob_dedup_job_%A.err     # Stderr (%A = job ID, %a = array index)
#SBATCH --partition=largemem              # Partition name
#SBATCH --ntasks=1                     # One task
#SBATCH --cpus-per-task=32            # Number of cores
#SBATCH --time=1-00:00:00              # Run time
#SBATCH --account=project_465001864    # Billing project
#SBATCH --mem=5T

# Print diagnostic information
module --force purge
source /scratch/project_465001864/malikbha/bitextor-slurm/dochplt/bin/activate

python -u /scratch/project_465001864/malikbha/bitextor-slurm/docs/5_global_dedup.py
