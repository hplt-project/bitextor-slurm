#!/bin/bash -l
#SBATCH --job-name=glob_align_job           # Job name
#SBATCH --output=/scratch/project_465001864/malikbha/slurm_logs/glob_align_job_%A_%a.out    # Stdout (%A = job ID, %a = array index)
#SBATCH --error=/scratch/project_465001864/malikbha/slurm_logs/glob_align_job_%A_%a.err     # Stderr (%A = job ID, %a = array index)
#SBATCH --partition=largemem             # Partition name
#SBATCH --ntasks=1                     # One task
#SBATCH --cpus-per-task=8            # Number of cores
#SBATCH --time=1-00:00:00              # Run time
#SBATCH --account=project_465001864    # Billing project
#SBATCH --mem=4T

# Run the Python script
# experiment_file="$1"

# # Check if SLURM_ARRAY_TASK_ID is set
# if [ -z "$SLURM_ARRAY_TASK_ID" ]; then
#     echo "Error: This script should be run as a SLURM array job"
#     exit 1
# fi

# # Get the folder from the experiment file using the array task ID
# input=$(sed -n "${SLURM_ARRAY_TASK_ID}p" "$experiment_file")
# read folder <<< "$input"

# # Print diagnostic information
# echo "Job ID: $SLURM_JOB_ID"
# echo "Array Task ID: $SLURM_ARRAY_TASK_ID"
# echo "Processing folder: $folder"

module --force purge
source /scratch/project_465001864/malikbha/bitextor-slurm/dochplt/bin/activate

python -u /scratch/project_465001864/malikbha/bitextor-slurm/docs/6_alignment_combined.py --folder "$1" --output "/scratch/project_465001864/bitexting_v3/sharded_data/clean/combined" --num_cpu 8
echo "Done in SLURM!"
# exit 0

