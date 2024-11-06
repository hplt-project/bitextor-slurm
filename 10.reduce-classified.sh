#!/bin/bash
set -euo pipefail

. ./env/init.sh
. ./config.sh
. ./functions.sh

lang=$1
shift

collections=$@
collection_hash=$(printf "%s\n" $collections | sort | join_by -)

declare -a batch_lists
batch_count=0

for collection in $collections; do
	batch_list=$(make_batch_list_all 10 $collection $lang)
	batch_count=$(( $batch_count + $(cat $batch_list | wc -l) ))
	batch_lists+=( $batch_list )
done

mkdir -p $DATA_CLEANING
output_file="${DATA_CLEANING}/${TARGET_LANG}-${lang}/${TARGET_LANG%~*}-${lang%~*}.${collection_hash}.classified.gz"
output_file_docs="${output_file/classified.gz/docs.gz}"

if [ ! -f $output_file ] || ! $RETRY; then
	prompt "Scheduling 1-1 for combining $batch_count batches across ${#batch_lists[@]} collections\n"
	if confirm; then
		schedule \
			-J reduce-classified-${lang%~*} \
			--time 24:00:00 \
			--cpus-per-task 1 \
			-o ${SLURM_LOGS}/10.reduce-classified-%A.log \
			${SCRIPTS}/10.reduce-classified ${lang%~*} ${output_file} ${batch_lists[@]}
		schedule \
			-J reduce-docs-${lang%~*} \
			--time 24:00:00 \
			--cpus-per-task 1 \
			-o ${SLURM_LOGS}/10.reduce-docs-%A.log \
			${SCRIPTS}/10.reduce-docs ${lang%~*} ${output_file_docs} ${batch_lists[@]}
	fi
fi
