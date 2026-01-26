if [[ $(hostname -A) =~ "uan"[0-9][0-9] ]]; then
	PROJ_DIR=/project/project_465001864/zaragoza
	SCRATCH_DIR=/scratch/project_465001864/bitexting_v3/sharded_data_bh

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
		["ccmain201415"]="$COLLECTION_ROOT/CC-MAIN-2014-15"
		["ccmain201435"]="$COLLECTION_ROOT/CC-MAIN-2014-35"
		["ccmain201442"]="$COLLECTION_ROOT/CC-MAIN-2014-42"
		["ccmain201511"]="$COLLECTION_ROOT/CC-MAIN-2015-11"
		["ccmain201518"]="$COLLECTION_ROOT/CC-MAIN-2015-18"
		["ccmain201548"]="$COLLECTION_ROOT/CC-MAIN-2015-48"
		["ccmain201630"]="$COLLECTION_ROOT/CC-MAIN-2016-30"
		["ccmain201640"]="$COLLECTION_ROOT/CC-MAIN-2016-40"
		["ccmain201644"]="$COLLECTION_ROOT/CC-MAIN-2016-44"
		["ccmain201704"]="$COLLECTION_ROOT/CC-MAIN-2017-04"
		["ccmain201726"]="$COLLECTION_ROOT/CC-MAIN-2017-26"
		["ccmain201751"]="$COLLECTION_ROOT/CC-MAIN-2017-51"
		["ccmain201805"]="$COLLECTION_ROOT/CC-MAIN-2018-05"
		["ccmain201822"]="$COLLECTION_ROOT/CC-MAIN-2018-22"
		["ccmain201834"]="$COLLECTION_ROOT/CC-MAIN-2018-34"
		["ccmain201839"]="$COLLECTION_ROOT/CC-MAIN-2018-39"
		["ccmain201843"]="$COLLECTION_ROOT/CC-MAIN-2018-43"
		["ccmain201904"]="$COLLECTION_ROOT/CC-MAIN-2019-04"
		["ccmain202005"]="$COLLECTION_ROOT/CC-MAIN-2020-05"
		["ccmain202010"]="$COLLECTION_ROOT/CC-MAIN-2020-10"
		["ccmain202016"]="$COLLECTION_ROOT/CC-MAIN-2020-16"
		["ccmain202024"]="$COLLECTION_ROOT/CC-MAIN-2020-24"
		["ccmain202029"]="$COLLECTION_ROOT/CC-MAIN-2020-29"
		["ccmain202034"]="$COLLECTION_ROOT/CC-MAIN-2020-34"
		["ccmain202040"]="$COLLECTION_ROOT/CC-MAIN-2020-40"
		["ccmain202045"]="$COLLECTION_ROOT/CC-MAIN-2020-45"
		["ccmain202050"]="$COLLECTION_ROOT/CC-MAIN-2020-50"
		["ccmain202104"]="$COLLECTION_ROOT/CC-MAIN-2021-04"
		["ccmain202110"]="$COLLECTION_ROOT/CC-MAIN-2021-10"
		["ccmain202117"]="$COLLECTION_ROOT/CC-MAIN-2021-17"
		["ccmain202121"]="$COLLECTION_ROOT/CC-MAIN-2021-21"
		["ccmain202125"]="$COLLECTION_ROOT/CC-MAIN-2021-25"
		["ccmain202131"]="$COLLECTION_ROOT/CC-MAIN-2021-31"
		["ccmain202139"]="$COLLECTION_ROOT/CC-MAIN-2021-39"
		["ccmain202143"]="$COLLECTION_ROOT/CC-MAIN-2021-43"
		["ccmain202149"]="$COLLECTION_ROOT/CC-MAIN-2021-49"
		["ccmain202205"]="$COLLECTION_ROOT/CC-MAIN-2022-05"
		["ccmain202221"]="$COLLECTION_ROOT/CC-MAIN-2022-21"
		["ccmain202227"]="$COLLECTION_ROOT/CC-MAIN-2022-27"
		["ccmain202233"]="$COLLECTION_ROOT/CC-MAIN-2022-33"
		["ccmain202240"]="$COLLECTION_ROOT/CC-MAIN-2022-40"
		["ccmain202249"]="$COLLECTION_ROOT/CC-MAIN-2022-49"
		["ccmain202306"]="$COLLECTION_ROOT/CC-MAIN-2023-06"
		["ccmain202314"]="$COLLECTION_ROOT/CC-MAIN-2023-14"
		["ccmain202323"]="$COLLECTION_ROOT/CC-MAIN-2023-23"
		["ccmain202340"]="$COLLECTION_ROOT/CC-MAIN-2023-40"
		["ccmain202350"]="$COLLECTION_ROOT/CC-MAIN-2023-50"
		["ccmain202410"]="$COLLECTION_ROOT/CC-MAIN-2024-10"
		["ccmain202418"]="$COLLECTION_ROOT/CC-MAIN-2024-18"
		["ccmain202422"]="$COLLECTION_ROOT/CC-MAIN-2024-22"
		["ccmain202426"]="$COLLECTION_ROOT/CC-MAIN-2024-26"
		["ccmain202430"]="$COLLECTION_ROOT/CC-MAIN-2024-30"
		["ccmain202433"]="$COLLECTION_ROOT/CC-MAIN-2024-33"
		["ccmain202438"]="$COLLECTION_ROOT/CC-MAIN-2024-38"
		["ccmain202442"]="$COLLECTION_ROOT/CC-MAIN-2024-42"
		["ccmain202446"]="$COLLECTION_ROOT/CC-MAIN-2024-46"
		["ccmain202451"]="$COLLECTION_ROOT/CC-MAIN-2024-51"
		["survey3"]="$COLLECTION_ROOT/survey3"
		["wide2"]="$COLLECTION_ROOT/wide2"
		["wide5"]="$COLLECTION_ROOT/wide5"
		["wide6"]="$COLLECTION_ROOT/wide6"
		["wide10"]="$COLLECTION_ROOT/wide10"
		["wide11"]="$COLLECTION_ROOT/wide11"
		["wide12"]="$COLLECTION_ROOT/wide12"
		["wide15"]="$COLLECTION_ROOT/wide15"
		["wide16"]="$COLLECTION_ROOT/wide16"
		["wide17"]="$COLLECTION_ROOT/wide17"
	)


	# Where jobs should be executed. Values used in functions.sh/schedule.
	export SBATCH_ACCOUNT=project_465001864
	#TODO should investigate if this variable has to be set depending on the step
	# small partition is allocatable by resources
	# standard partition is allocatable by node
	export SBATCH_PARTITION=small
	export SBATCH_MEM_PER_CPU=1750 # Maximum recommended size for LUMI
	export SLURM_LOGS=/users/haddowba/bitexting/logs
	export TASKS_PER_BATCH=${TPB:-1}

	# How many resources should be allocated per slurm job. Defaults
	# to as many as necessary to process all tasks in parallel. Individual
	# .slurm job definitions define how many cpus should be allocated per
	# task.
	export SLURM_TASKS_PER_NODE=${TPN:-1}
fi
