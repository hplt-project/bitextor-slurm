#!/bin/bash
set -euo pipefail

MODEL=$(dirname $(realpath -es ${BASH_SOURCE[0]}))/model
SLANG=$(basename $MODEL | cut -d- -f1)

python $MODEL/../ctranslate.py $MODEL -t $THREADS \
    -m 3000 -M 10000
