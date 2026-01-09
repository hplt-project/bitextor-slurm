#!/bin/bash -l
#SBATCH --job-name=merge_job           # Job name
#SBATCH --output=/scratch/project_465001864/malikbha/slurm_logs/merge_job_%A_%a.out    # Stdout output file
#SBATCH --error=/scratch/project_465001864/malikbha/slurm_logs/merge_job_%A_%a.err     # Stderr error file
#SBATCH --partition=small              # Partition name
#SBATCH --ntasks=1                     # One task
#SBATCH --cpus-per-task=1            # Number of cores
#SBATCH --time=3-00:00:00              # Run time
#SBATCH --account=project_465001864    # Billing project
#SBATCH --mem=400G

module --force purge
source /scratch/project_465001864/malikbha/bitextor-slurm/dochplt/bin/activate

# Run the Python script
# experiment_file="$1"
# current_experiment_number=$SLURM_ARRAY_TASK_ID
# input=$(sed -n "${current_experiment_number}p" "$experiment_file")
# read folder <<< "$input"

python -u /scratch/project_465001864/malikbha/bitextor-slurm/docs/1_doc_cleaner.py --folder "$1"
