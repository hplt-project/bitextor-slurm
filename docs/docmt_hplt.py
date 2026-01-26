from zipfile import ZipFile
import io
import re
import gzip
import os
from tqdm import tqdm

from xml.etree import ElementTree as ET

import datasets

lang_pair_mapping = {
    "af": "afr_Latn",
    "ar": "arb_Arab",
    "az": "azj_Latn",
    "be": "bel_Cyrl",
    "bg": "bul_Cyrl",
    "bn": "ben_Beng",
    "bs": "bos_Latn",
    "ca": "cat_Latn",
    "cy": "cym_Latn",
    "eo": "epo_Latn",
    "et": "est_Latn",
    "eu": "eus_Latn",
    "en": "eng_Latn",
    "fa": "pes_Arab",
    "fi": "fin_Latn",
    "ga": "gle_Latn",
    "gl": "glg_Latn",
    "gu": "guj_Gujr",
    "he": "heb_Hebr",
    "hi": "hin_Deva",
    "hr": "hrv_Latn",
    "is": "isl_Latn",
    "ja": "jpn_Jpan",
    "kk": "kaz_Cyrl",
    "kn": "kan_Knda",
    "ko": "kor_Hang",
    "lt": "lit_Latn",
    "lv": "lvs_Latn",
    "mk": "mkd_Cyrl",
    "ml": "mal_Mlym",
    "mr": "mar_Deva",
    "ms": "zsm_Latn",
    "mt": "mlt_Latn",
    "nb": "nob_Latn",
    "ne": "npi_Deva",
    "no": "nno_Latn", # NB: should be nn
    "si": "sin_Sinh",
    "sk": "slk_Latn",
    "sl": "slv_Latn",
    "sq": "als_Latn",
    "sr": "srp_Cyrl",
    "sw": "swh_Latn",
    "ta": "tam_Taml",
    "te": "tel_Telu",
    "th": "tha_Thai",
    "tr": "tur_Latn",
    "uk": "ukr_Cyrl",
    "ur": "urd_Arab",
    "uz": "uzn_Latn",
    "vi": "vie_Latn",
    "xh": "xho_Latn",
}


_DESCRIPTION = """\
DocMT HPLT
"""

# TODO: Add the licence for the dataset here if you can find it
_LICENSE = ""
_HOMEPAGE = ""
_CITATION = ""



_DOC_URLS = {
    # "af": "/scratch/project_462000764/dayyan/combined/docs/af-deduped-docs.zip",
    # "ar": "/scratch/project_462000764/dayyan/combined/docs/ar-deduped-docs.zip",
    # "az": "/scratch/project_462000764/dayyan/combined/docs/az-deduped-docs.zip",
    # "be": "/scratch/project_462000764/dayyan/combined/docs/be-deduped-docs.zip",
    # "bg": "/scratch/project_462000764/dayyan/combined/docs/bg-deduped-docs.zip",
    # "bn": "/scratch/project_462000764/dayyan/combined/docs/bn-deduped-docs.zip",
    # "bs": "/scratch/project_462000764/dayyan/combined/docs/bs-deduped-docs.zip",
    # "ca": "/scratch/project_462000764/dayyan/combined/docs/ca-deduped-docs.zip",
    # "cy": "/scratch/project_462000764/dayyan/combined/docs/cy-deduped-docs.zip",
    "en": "/fs/bil0/bhaddow/dochplt/bitexting_v3-clean/combined/docs/en-deduped-docs.zip",
    # "eo": "/scratch/project_462000764/dayyan/combined/docs/eo-deduped-docs.zip",
    # "et": "/scratch/project_462000764/dayyan/combined/docs/et-deduped-docs.zip",
    # "eu": "/scratch/project_462000764/dayyan/combined/docs/eu-deduped-docs.zip",
    # "fa": "/scratch/project_462000764/dayyan/combined/docs/fa-deduped-docs.zip",
    # "fi": "/scratch/project_462000764/dayyan/combined/docs/fi-deduped-docs.zip",
    # "ga": "/scratch/project_462000764/dayyan/combined/docs/ga-deduped-docs.zip",
    # "gl": "/scratch/project_462000764/dayyan/combined/docs/gl-deduped-docs.zip",
    # "gu": "/scratch/project_462000764/dayyan/combined/docs/gu-deduped-docs.zip",
    # "he": "/scratch/project_462000764/dayyan/combined/docs/he-deduped-docs.zip",
    # "hi": "/scratch/project_462000764/dayyan/combined/docs/hi-deduped-docs.zip",
    # "hr": "/scratch/project_462000764/dayyan/combined/docs/hr-deduped-docs.zip",
    # "is": "/scratch/project_462000764/dayyan/combined/docs/is-deduped-docs.zip",
    # "ja": "/scratch/project_462000764/dayyan/combined/docs/ja-deduped-docs.zip",
    # "kk": "/scratch/project_462000764/dayyan/combined/docs/kk-deduped-docs.zip",
    # "kn": "/scratch/project_462000764/dayyan/combined/docs/kn-deduped-docs.zip",
    # "ko": "/scratch/project_462000764/dayyan/combined/docs/ko-deduped-docs.zip",
    # "lt": "/scratch/project_462000764/dayyan/combined/docs/lt-deduped-docs.zip",
    # "lv": "/scratch/project_462000764/dayyan/combined/docs/lv-deduped-docs.zip",
    # "mk": "/scratch/project_462000764/dayyan/combined/docs/mk-deduped-docs.zip",
    # "ml": "/scratch/project_462000764/dayyan/combined/docs/ml-deduped-docs.zip",
    # "mr": "/scratch/project_462000764/dayyan/combined/docs/mr-deduped-docs.zip",
    # "ms": "/scratch/project_462000764/dayyan/combined/docs/ms-deduped-docs.zip",
    # "mt": "/scratch/project_465001864/bitexting_v3/sharded_data/clean/combined/docs/mt-deduped-docs.zip",
    # "nb": "/scratch/project_462000764/dayyan/combined/docs/nb-deduped-docs.zip",
    # "ne": "/scratch/project_462000764/dayyan/combined/docs/ne-deduped-docs.zip",
    "no": "/fs/bil0/bhaddow/dochplt/bitexting_v3-clean/combined/docs/no-deduped-docs.zip",
    # "si": "/scratch/project_462000764/dayyan/combined/docs/si-deduped-docs.zip",
    # "sk": "/scratch/project_462000764/dayyan/combined/docs/sk-deduped-docs.zip",
    # "sl": "/scratch/project_462000764/dayyan/combined/docs/sl-deduped-docs.zip",
    # "sq": "/scratch/project_462000764/dayyan/combined/docs/sq-deduped-docs.zip",
    # "sr": "/scratch/project_462000764/dayyan/combined/docs/sr-deduped-docs.zip",
    # "sw": "/scratch/project_462000764/dayyan/combined/docs/sw-deduped-docs.zip",
    # "ta": "/scratch/project_462000764/dayyan/combined/docs/ta-deduped-docs.zip",
    # "te": "/scratch/project_462000764/dayyan/combined/docs/te-deduped-docs.zip",
    # "th": "/scratch/project_462000764/dayyan/combined/docs/th-deduped-docs.zip",
    # "tr": "/scratch/project_462000764/dayyan/combined/docs/tr-deduped-docs.zip",
    # "uk": "/scratch/project_462000764/dayyan/combined/docs/uk-deduped-docs.zip",
    # "ur": "/scratch/project_462000764/dayyan/combined/docs/ur-deduped-docs.zip",
    # "uz": "/scratch/project_462000764/dayyan/combined/docs/uz-deduped-docs.zip",
    # "vi": "/scratch/project_462000764/dayyan/combined/docs/vi-deduped-docs.zip",
    # "xh": "/scratch/project_462000764/dayyan/combined/docs/xh-deduped-docs.zip",
}

_ALIGNMENT_URLS = {
    # "af-en": "/scratch/project_462000764/dayyan/combined/alignments/af-en.alignments.gz",
    # "ar-en": "/scratch/project_462000764/dayyan/combined/alignments/ar-en.alignments.gz",
    # "az-en": "/scratch/project_462000764/dayyan/combined/alignments/az-en.alignments.gz",
    # "be-en": "/scratch/project_462000764/dayyan/combined/alignments/be-en.alignments.gz",
    # "bg-en": "/scratch/project_462000764/dayyan/combined/alignments/bg-en.alignments.gz",
    # "bn-en": "/scratch/project_462000764/dayyan/combined/alignments/bn-en.alignments.gz",
    # "bs-en": "/scratch/project_462000764/dayyan/combined/alignments/bs-en.alignments.gz",
    # "ca-en": "/scratch/project_462000764/dayyan/combined/alignments/ca-en.alignments.gz",
    # "cy-en": "/scratch/project_462000764/dayyan/combined/alignments/cy-en.alignments.gz",
    # "en-eo": "/scratch/project_462000764/dayyan/combined/alignments/en-eo.alignments.gz",
    # "en-et": "/scratch/project_462000764/dayyan/combined/alignments/en-et.alignments.gz",
    # "en-eu": "/scratch/project_462000764/dayyan/combined/alignments/en-eu.alignments.gz",
    # "en-fa": "/scratch/project_462000764/dayyan/combined/alignments/en-fa.alignments.gz",
    # "en-fi": "/scratch/project_462000764/dayyan/combined/alignments/en-fi.alignments.gz",
    # "en-ga": "/scratch/project_462000764/dayyan/combined/alignments/en-ga.alignments.gz",
    # "en-gl": "/scratch/project_462000764/dayyan/combined/alignments/en-gl.alignments.gz",
    # "en-gu": "/scratch/project_462000764/dayyan/combined/alignments/en-gu.alignments.gz",
    # "en-he": "/scratch/project_462000764/dayyan/combined/alignments/en-he.alignments.gz",
    # "en-hi": "/scratch/project_462000764/dayyan/combined/alignments/en-hi.alignments.gz",
    # "en-hr": "/scratch/project_462000764/dayyan/combined/alignments/en-hr.alignments.gz",
    # "en-is": "/scratch/project_462000764/dayyan/combined/alignments/en-is.alignments.gz",
    # "en-ja": "/scratch/project_462000764/dayyan/combined/alignments/en-ja.alignments.gz",
    # "en-kk": "/scratch/project_462000764/dayyan/combined/alignments/en-kk.alignments.gz",
    # "en-kn": "/scratch/project_462000764/dayyan/combined/alignments/en-kn.alignments.gz",
    # "en-ko": "/scratch/project_462000764/dayyan/combined/alignments/en-ko.alignments.gz",
    # "en-lt": "/scratch/project_462000764/dayyan/combined/alignments/en-lt.alignments.gz",
    # "en-lv": "/scratch/project_462000764/dayyan/combined/alignments/en-lv.alignments.gz",
    # "en-mk": "/scratch/project_462000764/dayyan/combined/alignments/en-mk.alignments.gz",
    # "en-ml": "/scratch/project_462000764/dayyan/combined/alignments/en-ml.alignments.gz",
    # "en-mr": "/scratch/project_462000764/dayyan/combined/alignments/en-mr.alignments.gz",
    # "en-ms": "/scratch/project_462000764/dayyan/combined/alignments/en-ms.alignments.gz",
    # "en-mt": "/scratch/project_465001864/bitexting_v3/sharded_data/clean/combined/alignments/en-mt.alignments.gz",
    # "en-nb": "/scratch/project_462000764/dayyan/combined/alignments/en-nb.alignments.gz",
    # "en-ne": "/scratch/project_462000764/dayyan/combined/alignments/en-ne.alignments.gz",
    "en-no": "/fs/bil0/bhaddow/dochplt/bitexting_v3-clean/combined/alignments/en-no.alignments.gz",
    # "en-si": "/scratch/project_462000764/dayyan/combined/alignments/en-si.alignments.gz",
    # "en-sk": "/scratch/project_462000764/dayyan/combined/alignments/en-sk.alignments.gz",
    # "en-sl": "/scratch/project_462000764/dayyan/combined/alignments/en-sl.alignments.gz",
    # "en-sq": "/scratch/project_462000764/dayyan/combined/alignments/en-sq.alignments.gz",
    # "en-sr": "/scratch/project_462000764/dayyan/combined/alignments/en-sr.alignments.gz",
    # "en-sw": "/scratch/project_462000764/dayyan/combined/alignments/en-sw.alignments.gz",
    # "en-ta": "/scratch/project_462000764/dayyan/combined/alignments/en-ta.alignments.gz",
    # "en-te": "/scratch/project_462000764/dayyan/combined/alignments/en-te.alignments.gz",
    # "en-th": "/scratch/project_462000764/dayyan/combined/alignments/en-th.alignments.gz",
    # "en-tr": "/scratch/project_462000764/dayyan/combined/alignments/en-tr.alignments.gz",
    # "en-uk": "/scratch/project_462000764/dayyan/combined/alignments/en-uk.alignments.gz",
    # "en-ur": "/scratch/project_462000764/dayyan/combined/alignments/en-ur.alignments.gz",
    # "en-uz": "/scratch/project_462000764/dayyan/combined/alignments/en-uz.alignments.gz",
    # "en-vi": "/scratch/project_462000764/dayyan/combined/alignments/en-vi.alignments.gz",
    # "en-xh": "/scratch/project_462000764/dayyan/combined/alignments/en-xh.alignments.gz",
}


class DocMTHPLTDataset(datasets.GeneratorBasedBuilder):

    VERSION = datasets.Version("1.1.0")

    # This is an example of a dataset with multiple configurations.
    # If you don't want/need to define several sub-sets in your dataset,
    # just remove the BUILDER_CONFIG_CLASS and the BUILDER_CONFIGS attributes.

    # If you need to make complex sub-parts in the datasets with configurable options
    # You can create your own builder configuration class to store attribute, inheriting from datasets.BuilderConfig
    # BUILDER_CONFIG_CLASS = MyBuilderConfig

    # You will be able to load one or the other configurations in the following list with
    # data = datasets.load_dataset('my_dataset', 'first_domain')
    # data = datasets.load_dataset('my_dataset', 'second_domain')

    # data_sources = {self.config.name: _ALIGNMENT_URLS[self.config.name]}
    # print(data_sources)

    BUILDER_CONFIGS = [
        datasets.BuilderConfig(
            name=lang,
            description=(f"This part of the dataset has {lang_pair_mapping[lang.split('-')[0]]} as source language and {lang_pair_mapping[lang.split('-')[1]]} as target language."),
        )
        for lang in _ALIGNMENT_URLS.keys()
    ]
    # BUILDER_CONFIGS = [
    #     datasets.BuilderConfig(name="en-xh", version=VERSION, description="This part of the dataset has eng_Latn as source language and xho_Latn as target language"),
    #     datasets.BuilderConfig(name="en-ja", version=VERSION, description="This part of the dataset has eng_Latn as source language and jpn_Jpan as target language"),
    #     # datasets.BuilderConfig(name="second_domain", version=VERSION, description="This part of my dataset covers a second domain"),
    # ]

    # DEFAULT_CONFIG_NAME = "en-xh"  # It's not mandatory to have a default configuration. Just use one if it make sense.

    def _info(self):
        # TODO: This method specifies the datasets.DatasetInfo object which contains informations and typings for the dataset
        features = datasets.Features(
            {
                "src_doc_id": datasets.Value("string"),
                "tgt_doc_id": datasets.Value("string"),
                "lang_pair": datasets.Value("string"),
                # "src_doc_ids": datasets.Sequence(datasets.Value("string")),
                # "src_doc_sentences": datasets.Sequence(datasets.Value("string")),
                # "tgt_doc_ids": datasets.Sequence(datasets.Value("string")),
                # "tgt_doc_sentences": datasets.Sequence(datasets.Value("string")),
                "src_doc": {
                    "ids": datasets.Sequence(datasets.Value("string")),
                    "sentences": datasets.Sequence(datasets.Value("string")),
                },
                "tgt_doc": {
                    "ids": datasets.Sequence(datasets.Value("string")),
                    "sentences": datasets.Sequence(datasets.Value("string")),
                },
                "alignment": [
                    {
                        "src": [datasets.Value("string")],
                        "tgt": [datasets.Value("string")],
                        "aligner-score": datasets.Value("float"),
                        "bicleaner-score": datasets.Value("float"),
                        "bifixer-score": datasets.Value("float"),
                    }
                ],
            }
        )
        return datasets.DatasetInfo(
            # This is the description that will appear on the datasets page.
            description=_DESCRIPTION,
            # This defines the different columns of the dataset and their types
            features=features,  # Here we define them above because they are different between the two configurations
            # If there's a common (input, target) tuple from the features, uncomment supervised_keys line below and
            # specify them. They'll be used if as_supervised=True in builder.as_dataset.
            # supervised_keys=("sentence", "label"),
            # Homepage of the dataset for documentation
            homepage=_HOMEPAGE,
            # License for the dataset if available
            license=_LICENSE,
            # Citation for the dataset
            citation=_CITATION,
        )

    def _split_generators(self, dl_manager):
        # TODO: This method is tasked with downloading/extracting the data and defining the splits depending on the configuration
        # If several configurations are possible (listed in BUILDER_CONFIGS), the configuration selected by the user is in self.config.name

        # dl_manager is a datasets.download.DownloadManager that can be used to download and extract URLS
        # It can accept any type or nested list/dict and will give back the same structure with the url replaced with path to local files.
        # By default the archives will be extracted and a path to a cached folder where they are extracted is returned instead of the archive
        data_sources = {self.config.name: _ALIGNMENT_URLS[self.config.name]}
        # eng_docs = dl_manager.download(_DOC_URLS["en"])
        eng_zip_file = ZipFile(_DOC_URLS["en"], 'r')

        return [
            datasets.SplitGenerator(
                name="train",
                gen_kwargs={
                    # "alignment_filepath": dl_manager.download(data_sources[lang]),
                    "lang_pair": lang,
                    "alignment_filepath": data_sources[lang],
                    "eng_zip_file": eng_zip_file,
                    "lang_docs_path": _DOC_URLS[lang.split('-')[1]] if lang.startswith("en") else _DOC_URLS[lang.split('-')[0]],
                }
            )
            for lang in data_sources
        ]

    def _get_doc_content_from_zip(self, zip_file, doc):
        ids = []
        sents = []

        try:
            with zip_file.open(doc) as f:
                content = f.read()

            xml_string = content.decode('utf-8')

            with io.StringIO(xml_string) as f:
                for event, elem in ET.iterparse(f, events=('end',)):
                    if elem.tag == 's':
                        if 'id' in elem.attrib and elem.text:
                            ids.append(elem.attrib['id'])
                            sents.append(elem.text.strip())
                        elem.clear()

        except ET.ParseError as e:
            print(f"[Warning] XML parse error in file {doc}: {e}")
            return [], []

        except UnicodeDecodeError as e:
            print(f"[Warning] Unicode decode error in file {doc}: {e}")
            return [], []

        return ids, sents

    # method parameters are unpacked from `gen_kwargs` as given in `_split_generators`
    def _generate_examples(self, lang_pair, alignment_filepath, eng_zip_file, lang_docs_path):
        # TODO: This method handles input defined in _split_generators to yield (key, example) tuples from the dataset.
        # The `key` is for legacy reasons (tfds) and is not important in itself, but must be unique for each example.        
        
        self.lang = lang_pair.split('-')[1] if lang_pair.startswith("en") else lang_pair.split('-')[0]
        self.key = 0
        
        print(f"reading {self.lang} docs file")
        tgt_zip_file = ZipFile(lang_docs_path, 'r')

        file_size = os.path.getsize(alignment_filepath)
        print(f"reading {self.lang} alignments file")

        with gzip.open(alignment_filepath, 'rb') as f:
            with tqdm.wrapattr(f, "read", total=file_size, desc="Parsing") as f_tqdm:
                context = ET.iterparse(f, events=('end',))
                
                for event, elem in context:
                    if elem.tag == 'linkGrp':
                        if lang_pair.startswith("en"):  # en-X
                            from_doc = elem.get('fromDoc').split("en/")[-1]
                            to_doc = elem.get('toDoc').split(f"{self.lang}/")[-1]

                            eng_ids, eng_sents = self._get_doc_content_from_zip(eng_zip_file, from_doc)
                            lang_ids, lang_sents = self._get_doc_content_from_zip(tgt_zip_file, to_doc)

                        else:  # X-en
                            from_doc = elem.get('fromDoc').split(f"{self.lang}/")[-1]
                            to_doc = elem.get('toDoc').split("en/")[-1]

                            eng_ids, eng_sents = self._get_doc_content_from_zip(eng_zip_file, to_doc)
                            lang_ids, lang_sents = self._get_doc_content_from_zip(tgt_zip_file, from_doc)
                        
                        # Skip if either doc failed to parse (empty lists)
                        if not eng_ids or not lang_ids:
                            print(f"[Warning] Skipping example due to parse failure: src_doc={from_doc}, tgt_doc={to_doc}")
                            elem.clear()
                            continue

                        alignment_list = []
                        for link in elem.findall('link'):
                            src, tgt = link.get('xtargets').split(";")
                            alignment_list.append({
                                "src": src.split(),
                                "tgt": tgt.split(),
                                "aligner-score": float(link.get('aligner-score')),
                                "bicleaner-score": float(link.get('bicleaner-score')),
                                "bifixer-score": float(link.get('bifixer-score'))/100
                            })

                        if lang_pair.startswith("en"):
                            yield self.key, {
                                "lang_pair": f"en-{self.lang}",
                                "src_doc_id": from_doc,
                                "tgt_doc_id": to_doc,
                                "src_doc": {
                                    "ids": eng_ids,
                                    "sentences": eng_sents,
                                },
                                "tgt_doc": {
                                    "ids": lang_ids,
                                    "sentences": lang_sents,
                                },
                                "alignment": alignment_list,
                            }
                        else:
                            yield self.key, {
                                "lang_pair": f"{self.lang}-en",
                                "src_doc_id": from_doc,
                                "tgt_doc_id": to_doc,
                                "src_doc": {
                                    "ids": lang_ids,
                                    "sentences": lang_sents,
                                },
                                "tgt_doc": {
                                    "ids": eng_ids,
                                    "sentences": eng_sents,
                                },
                                "alignment": alignment_list,
                            }
                        self.key += 1
                        elem.clear()

        tgt_zip_file.close()
        eng_zip_file.close()