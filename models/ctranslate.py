from timeit import default_timer
import sentencepiece as sp
import ctranslate2
import argparse
import logging
import yaml
import sys

parser = argparse.ArgumentParser()
parser.add_argument("model_dir", nargs='?', type=str)
parser.add_argument("-b","--beam_size", required=False, default=4, type=int)
parser.add_argument("-m","--mini-batch", required=False, default=1000, type=int)
parser.add_argument("-M","--maxi-batch", required=False, default=10000, type=int)
parser.add_argument("-l","--max-length", required=False, default=300, type=int)
parser.add_argument("-t","--threads", required=False, default=6, type=int)
args = parser.parse_args()

logger = logging.getLogger()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
spm_source = sp.SentencePieceProcessor(f"{args.model_dir}/source.spm")
spm_target = sp.SentencePieceProcessor(f"{args.model_dir}/target.spm")
translator = ctranslate2.Translator(
        args.model_dir,
        device="cpu",
        intra_threads=args.threads,
        inter_threads=1,
        )
total_toks = 0
total_sents = 0
start_time = default_timer()

def translate(batch):
    batch = spm_source.encode_as_pieces(batch)
    global total_toks
    total_toks += sum(map(lambda x: len(x), batch))
    pred = translator.translate_batch(
            source=batch,
            max_batch_size=args.mini_batch,
            batch_type="tokens",
            beam_size=args.beam_size,
            max_input_length=args.max_length,
            max_decoding_length=args.max_length,
            )
    for i in pred:
        print(spm_target.decode([x for x in i.hypotheses[0] if x!= '<bt>']))


batch = []
for i, line in enumerate(sys.stdin):
    batch.append(line.strip())

    if len(batch) >= args.maxi_batch:
        translate(batch)
        batch = []

if batch:
    translate(batch)

elapsed = default_timer() - start_time
toks_s = total_toks / elapsed
sent_s = (i + 1) / elapsed
logger.info(f"Total read: {total_toks} tokens - {i + 1} lines")
logger.info(f"Throughput: {toks_s:.1f} tok/s - {sent_s:.1f} line/s")
logger.info(f"Elapsed time: {elapsed:.1f}")

