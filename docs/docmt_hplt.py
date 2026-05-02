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
    "cs": "ces_Latn",
    "da": "dan_Latn",
    "eo": "epo_Latn",
    "et": "est_Latn",
    "eu": "eus_Latn",
    "el": "ell_Latn",
    "en": "eng_Latn",
    "fa": "pes_Arab",
    "fi": "fin_Latn",
    "ga": "gle_Latn",
    "gl": "glg_Latn",
    "gu": "guj_Gujr",
    "he": "heb_Hebr",
    "hi": "hin_Deva",
    "hr": "hrv_Latn",
    "hu": "hun_Latn",
    "is": "isl_Latn",
    "ja": "jpn_Jpan",
    "ka": "kat_Geor",
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
    "nn": "nno_Latn", 
    "ro": "ron_Latn",
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


_COMBINED_PATH = "/fs/bil0/bhaddow/dochplt/bitexting_v3-clean/combined/"

_LANGS = ["sq", "bs", "bg", "ca", "cs", "da", "et", "el", "eu", "fi", "ga", "gl", "hr", "hu", "is" , "ka", "lt", "lv", "mk", "mt", "nn", "nb", "ro", "sk", "sl", "sr", "tr", "uk"  ]
_DOC_URLS = {
  lang: f"{_COMBINED_PATH}/docs_deduped/{lang}-deduped-docs.zip" for lang in _LANGS + ["en"]
}
_ENG_DOCS_DIR = f"{_COMBINED_PATH}/docs_deduped/en-extracted/"

_PAIRS = ["-".join(sorted(("en",lang))) for lang in _LANGS]

_ALIGNMENT_URLS = {pair: f"{_COMBINED_PATH}/alignments_deduped/{pair}.alignments.gz" for pair in _PAIRS}



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

        return [
            datasets.SplitGenerator(
                name="train",
                gen_kwargs={
                    "lang_pair": lang,
                    "alignment_filepath": data_sources[lang],
                    "eng_docs_dir": _ENG_DOCS_DIR,
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

    def _get_doc_content_from_dir(self, docs_dir, doc):
        ids = []
        sents = []
        filepath = os.path.join(docs_dir, doc)

        try:
            with open(filepath, 'rb') as f:
                content = f.read()

            xml_string = content.decode('utf-8')

            with io.StringIO(xml_string) as f:
                for event, elem in ET.iterparse(f, events=('end',)):
                    if elem.tag == 's':
                        if 'id' in elem.attrib and elem.text:
                            ids.append(elem.attrib['id'])
                            sents.append(elem.text.strip())
                        elem.clear()

        except FileNotFoundError as e:
            print(f"[Warning] File not found: {filepath}")
            return [], []

        except ET.ParseError as e:
            print(f"[Warning] XML parse error in file {doc}: {e}")
            return [], []

        except UnicodeDecodeError as e:
            print(f"[Warning] Unicode decode error in file {doc}: {e}")
            return [], []

        return ids, sents

    # method parameters are unpacked from `gen_kwargs` as given in `_split_generators`
    def _generate_examples(self, lang_pair, alignment_filepath, eng_docs_dir, lang_docs_path):
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

                            eng_ids, eng_sents = self._get_doc_content_from_dir(eng_docs_dir, from_doc)
                            lang_ids, lang_sents = self._get_doc_content_from_zip(tgt_zip_file, to_doc)

                        else:  # X-en
                            from_doc = elem.get('fromDoc').split(f"{self.lang}/")[-1]
                            to_doc = elem.get('toDoc').split("en/")[-1]

                            eng_ids, eng_sents = self._get_doc_content_from_dir(eng_docs_dir, to_doc)
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
