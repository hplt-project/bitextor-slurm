if [[ $(hostname -A) =~ "uan"[0-9][0-9] ]]; then
	PROJ_DIR=/project/project_465001864/zaragoza
	SCRATCH_DIR=/scratch/project_465001864/bitexting_test/shards

	# Override binaries called by env variable
	# they should be available in PATH for lumi
	export DOCALIGN=docalign
	export DOCJOIN=docjoin
	export BLEUALIGN=bleualign_cpp
    export TOKENISER="/home/docker/bitextor/third_party/preprocess/moses/tokenizer/tokenizer.perl"

	function bicleaner_model {
		local lang=$1

		export BIFIXER_PARAMS="--aggressive_dedup -q"
		export BICLEANER=bicleaner-classify-lite
		export BICLEANER_THRESHOLD="0.5"
		export BICLEANER_PARAMS="-q" # --score_only is always supplied

		# Default path: here instead of in config.csd3 because path depends on $lang and the exceptions
		# above don't follow this pattern very well, which is why it's not in the 09.clean code itself.
		export BICLEANER_MODEL=$PROJ_DIR/bicleaner-models/${TARGET_LANG%~*}-${lang%~*}/${TARGET_LANG%~*}-${lang%~*}.yaml
	}

	export HF_HUB_CACHE="/projappl/project_465001864/.cache/huggingface/hub"
	function bicleaner_ai_model {
		export BIFIXER_PARAMS="--aggressive_dedup -q --ignore_segmentation"
		export BICLEANER=bicleaner-ai-classify
		export BICLEANER_THRESHOLD="0.5"
		export BICLEANER_PARAMS="-q --batch_size 64 --block_size 100000"
		export BICLEANER_MODEL=bitextor/bicleaner-ai-full-large-${TARGET_LANG%~*}-xx
	}

	export DATA_CLEANING=$SCRATCH_DIR/clean
	export COLLECTION_ROOT="$SCRATCH_DIR"
	declare -A COLLECTIONS=(
		["sample3"]="$COLLECTION_ROOT/CC-MAIN-2014-15"
	)

	# Where jobs should be executed. Values used in functions.sh/schedule.
	export SBATCH_ACCOUNT=project_465001864
	#TODO should investigate if this variable has to be set depending on the step
	# small partition is allocatable by resources
	# standard partition is allocatable by node
	export SBATCH_PARTITION=small
	export SBATCH_MEM_PER_CPU=1750 # Maximum recommended size for LUMI
	export SLURM_LOGS=$SCRATCH_DIR/logs
	export TASKS_PER_BATCH=${TPB:-1}

	# How many resources should be allocated per slurm job. Defaults
	# to as many as necessary to process all tasks in parallel. Individual
	# .slurm job definitions define how many cpus should be allocated per
	# task.
	export SLURM_TASKS_PER_NODE=${TPN:-1}
fi
