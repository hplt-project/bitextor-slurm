#!/bin/bash

## create and submit the batches on csd3 for translation
set -euo pipefail

. config.csd3
. functions.sh
. translate.sh
export -f get_group_boundaries task

collection=$1
shift

#export NMOSI=${NMOSI:-4} #16} # 16 * 16 threads (see $MOSES_ARGS in config.csd3)

for lang in $*; do
	# Load in translation model config so we know MODEL_ARCH
	eval model_${lang}_${TARGET_LANG} || (echo "No model for ${lang} -> ${TARGET_LANG}" 1>&2 ; exit 255)
	# set -a # I want the variables from translate.sh to be available in my deep deep down bash
	# Copy model to scratch (only if we're running multiple mosis)
#	if [ "$MODEL_IMPL" = "translate_moses" ] && [ "$NMOSI" -gt 2 ]; then
#        	MDIR="`dirname $MODEL`"
#	        MNAME="`basename $MODEL`"
#        	MODEL_PATH="${MODEL}"
#       		MODEL="${SCRATCH}/${MNAME}"
#        echo "Copying translation model ${MODEL_PATH} to scratch space"
#        (cd "${MDIR}" && tar -cf - "${MNAME}") | (cd "${SCRATCH}" && tar -xvf -)
#	fi
	batch_list=$(make_batch_list 04 $collection $lang sentences_${TARGET_LANG}.gz)
	job_list=$(make_job_list $batch_list)
	if [ ! -z $job_list ]; then
		prompt "Scheduling $job_list on $MODEL_ARCH\n"
		if confirm; then
			schedule --nice=400 -J translate-${lang} -a $job_list 04.translate.${MODEL_ARCH}.slurm $lang $batch_list
		fi
	fi
done
