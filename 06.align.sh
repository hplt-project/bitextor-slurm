#!/bin/bash

## create and submit the batches on csd3 for alignment
set -euo pipefail

. ./env/init.sh
. ./config.sh
. ./functions.sh

function list_numeric_dirs {
	find "$@" -mindepth 1 -maxdepth 1 -type d -regex '.*/[0-9]*'
}

function make_batch_list_all {
	local collection="$1" lang="$2"
	if $FORCE_INDEX_BATCHES || ! test -e ${COLLECTIONS[$collection]}-batches/06.${lang}-${TARGET_LANG}; then
		for shard in $(list_numeric_dirs ${COLLECTIONS[$collection]}-shards/${lang}/); do
			join -t$'\t' -j2 -o 1.1,2.1 \
				<(list_numeric_dirs $shard) \
				<(list_numeric_dirs ${COLLECTIONS[$collection]}-shards/${TARGET_LANG}/$(basename $shard))
		done > ${COLLECTIONS[$collection]}-batches/06.${lang}-${TARGET_LANG}
	fi
	echo ${COLLECTIONS[$collection]}-batches/06.${lang}-${TARGET_LANG}
}

function make_batch_list_retry {
	batch_list=${COLLECTIONS[$collection]}-batches/06.${lang}-${TARGET_LANG}.$(date '+%Y%m%d%H%M%S')

	cat `make_batch_list_all "$@"` | while read SRC_BATCH REF_BATCH; do
		alignments=$SRC_BATCH/aligned.gz
		# either if the alignments doesn't exist, or the tokenised_en.gz file is newer than aligned-n.gz
		if [[ ! -e $alignments ]] || [[ $SRC_BATCH/tokenised_${TARGET_LANG%~*}.gz -nt $alignments ]]; then
			printf '%s\t%s\n' "$alignments" "$REF_BATCH" 1>&2
			printf '%s\t%s\n' "$SRC_BATCH" "$REF_BATCH"
		fi
	done | shuf > $batch_list

	echo $batch_list
}

function make_batch_list_reduce {
	local step="$1" collection="$2" lang="$3"
	local batch_list=${COLLECTIONS[$collection]}-batches/${step}.${lang}

	if [[ ! -d ${COLLECTIONS[$collection]}-batches ]]; then
		mkdir ${COLLECTIONS[$collection]}-batches
	fi

	if $FORCE_INDEX_BATCHES || [[ ! -f ${COLLECTIONS[$collection]}-batches/${lang} ]]; then
		find ${COLLECTIONS[$collection]}-shards/${lang} \
			-mindepth 2 \
			-maxdepth 2 \
			-type d \
			-regex '.*/[0-9]+/[0-9]+' \
			> ${COLLECTIONS[$collection]}-batches/${lang}
	fi

	rm -f ${batch_list}
	ln -s ${COLLECTIONS[$collection]}-batches/${lang} ${batch_list}

	echo ${batch_list}
}

declare -a OPTIONS=(
	--time 24:00:00
	--cpus-per-task 4
	-o ${SLURM_LOGS}/06.align-%A_%a.log
)

collection=$1
shift

for lang in $*; do
	batch_list=`make_batch_list $collection $lang`
	job_list=`make_job_list $batch_list`
	batch_list_reduce=`make_batch_list_reduce 06 $collection $lang`
	job_list_reduce=`make_job_list $batch_list_reduce`
	if [ ! -z $job_list ]; then
		prompt "Scheduling $job_list\n"
		if confirm; then
			align_job_id=$(schedule \
				-J align-${lang%~*}-${collection} \
				-a $job_list \
				--parsable \
				${OPTIONS[@]} \
				${SCRIPTS}/generic.slurm $batch_list \
				${SCRIPTS}/06.align ${lang%~*})

			schedule \
				-J reduce-align-${lang%~*}-${collection} \
				-a $job_list_reduce \
				--dependency afterok:$align_job_id \
				--time 24:00:00 --cpus-per-task 1 \
				-o ${SLURM_LOGS}/06.reduce-align-%A_%a.log \
				${SCRIPTS}/generic.slurm $batch_list_reduce \
				${SCRIPTS}/06.reduce-align ${lang%~*}
		fi
	fi
done
