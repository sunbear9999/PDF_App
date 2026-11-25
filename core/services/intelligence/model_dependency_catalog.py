"""Pinned manifests for optional local models and packages shown in AI Setup.

Each ONNX entry pins a repository revision with per-file SHA-256 and size so
installation is reproducible and verifiable. Task entries record hardware
guidance and the evaluation that justified offering them.
"""
from __future__ import annotations

from typing import Callable, List

from core.models.model_dependency_models import ModelDependencySpec


def builtin_model_specs(model_dir: Callable[[str], str]) -> List[ModelDependencySpec]:
    return [
        ModelDependencySpec(
            id="spacy_lg", label="spaCy en_core_web_lg (optional)",
            description="Optional large English NER model. Quality gains on your corpus are not yet measured; installation is never automatic.",
            kind="spacy", role="entities", size_hint="~560 MB", spacy_model="en_core_web_lg",
        ),
        ModelDependencySpec(
            id="spacy_md",
            label="spaCy en_core_web_md",
            description=(
                "Larger spaCy model for entity extraction and alias "
                "resolution. The small model remains a valid fallback."
            ),
            kind="spacy",
            role="entities",
            size_hint="~40 MB",
            spacy_model="en_core_web_md",
        ),
        ModelDependencySpec(
            id="cross_encoder",
            label="Cross-encoder reranker (ONNX)",
            description=(
                "Quantized MS-MARCO MiniLM cross-encoder for local "
                "retrieval reranking."
            ),
            kind="onnx_hf",
            role="reranker_model",
            size_hint="~24 MB",
            hf_repo="Xenova/ms-marco-MiniLM-L-6-v2",
            hf_revision="a09144355adeed5f58c8ed011d209bf8ee5a1fec",
            hf_files={
                "model.onnx": ["onnx/model_quantized.onnx"],
                "tokenizer.json": ["tokenizer.json"],
            },
            file_sha256={
                "model.onnx": "e9d8ebf845c413e981c175bfe49a3bfa9b3dcce2a3ba54875ee5df5a58639fbe",
                "tokenizer.json": "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
            },
            file_sizes={
                "model.onnx": 23143499,
                "tokenizer.json": 711396,
            },
            dest_dir=model_dir("cross_encoder"),
            license="apache-2.0",
            runtime_modules=["onnxruntime", "tokenizers"],
        ),
        ModelDependencySpec(
            id="nli_relation",
            label="NLI relation scorer (ONNX)",
            description=(
                "Quantized DeBERTa-v3-small NLI model for local "
                "entailment and contradiction scoring."
            ),
            kind="onnx_hf",
            role="relation_classifier",
            size_hint="~170 MB",
            hf_repo="Xenova/nli-deberta-v3-small",
            hf_revision="7b977009b2cbb563e24994efe38c57d75c38207c",
            hf_files={
                "model.onnx": ["onnx/model_int8.onnx"],
                "tokenizer.json": ["tokenizer.json"],
                "config.json": ["config.json"],
            },
            file_sha256={
                "model.onnx": "fda286f22872be2ac08f2d08d18465fb39a630aef496ca1c148da804a1f26f0e",
                "tokenizer.json": "a86f883318afa11c8c10466f1bf4efaeb6ded28a52cbe57217a8fa0d0a2a87df",
                "config.json": "95300a238aaea78dfa0b100a3b53bf8bc233da8e7f3da54dc077135340d76b4f",
            },
            file_sizes={
                "model.onnx": 160932259,
                "tokenizer.json": 8656551,
                "config.json": 1037,
            },
            dest_dir=model_dir("nli_relation"),
            license="apache-2.0",
            runtime_modules=["onnxruntime", "tokenizers"],
        ),
        ModelDependencySpec(
            id="sentence_role_enhancer",
            label="Sentence-role enhancer (zero-shot NLI, extra-small)",
            description=(
                "Proposes roles for uncertain eligible prose by testing "
                "editable role/background hypotheses with a small NLI model. "
                "It requires entailment and separation, may abstain, and never "
                "overrides a heuristic label. Proposals stay low-confidence. The "
                "built-in heuristic keeps working without it."
            ),
            kind="onnx_hf",
            role="sentence_classifier_enhancer",
            task="sentence_roles",
            size_hint="~95 MB",
            hf_repo="Xenova/nli-deberta-v3-xsmall",
            hf_revision="2a4f614a701367a02d51389039afc998faeda637",
            hf_files={
                "model.onnx": ["onnx/model_int8.onnx"],
                "tokenizer.json": ["tokenizer.json"],
                "config.json": ["config.json"],
            },
            file_sha256={
                "model.onnx": "e1878da6c3a689d7e7beb9703395880016c3585cd127f875be0acad02405737b",
                "tokenizer.json": "a86f883318afa11c8c10466f1bf4efaeb6ded28a52cbe57217a8fa0d0a2a87df",
                "config.json": "ec0bd14cc28640326474399cd61d38ccd52b64900228799d0f81debda8c4bc53",
            },
            file_sizes={
                "model.onnx": 90409269,
                "tokenizer.json": 8656551,
                "config.json": 1038,
            },
            dest_dir=model_dir("sentence_role_enhancer"),
            license="apache-2.0 (base model cross-encoder/nli-deberta-v3-xsmall)",
            runtime_modules=["onnxruntime", "tokenizers"],
            hardware=(
                "CPU only; ~300 MB RAM while running; ~95 MB disk. Measured "
                "runtime depends on batch size and prose coverage; only "
                "eligible heuristic-unknown sentences are scored."
            ),
            evaluation=(
                "2026-10-04: 72-sentence development regression accuracy "
                "94.4% with or without this enhancer; abstention is allowed. "
                "On 155 provisional local PDF excerpts it added one false "
                "argument in development and no positive-role accuracy gain. "
                "Small/base also added no gain. These are limited English "
                "samples, not general accuracy or calibration guarantees. "
                "Keep optional proposals subject to review."
            ),
            extra_files={"role_hypotheses.json": "sentence_role_hypotheses"},
            required_labels=["entailment"],
        ),
        ModelDependencySpec(
            id="gliner_multi",
            label="Multilingual entity recogniser (GLiNER, ONNX)",
            description=(
                "Finds and types names in any language, including technical terms "
                "(methods, datasets, conditions) the English spaCy model mislabels. "
                "It adds votes to the document-wide type decision; it never decides alone. "
                "Off until switched on in the Entities stage settings."
            ),
            kind="onnx_hf", role="entity_recognizer", task="entities", size_hint="~1.2 GB download, ~600 MB installed",
            hf_repo="onnx-community/gliner_multi-v2.1",
            hf_revision="6ddaeb9413b0e71ad8457da1aab378a165b24058",
            hf_files={
                "model.onnx": ["onnx/model.onnx"],
                "tokenizer.json": ["tokenizer.json"],
                "gliner_config.json": ["gliner_config.json"],
            },
            file_sha256={
                "model.onnx": "57af7f3d8d72a3ca4e5619c247344b4b5ee362a5ebcf307c0634d67b65e721c0",
                "tokenizer.json": "914bd3c8fb7b525af9e23b60d0ec7b1248ddb2b99014efd9c02ebeb022f8cab7",
                "gliner_config.json": "1ef59c57fe6816a155697a5670ca8d0faf0babe13f199a2c2b5b65113399ed72",
            },
            file_sizes={"model.onnx": 1157129714, "tokenizer.json": 16331948, "gliner_config.json": 731},
            dest_dir=model_dir("gliner_multi"),
            license="apache-2.0 (base model urchade/gliner_multi-v2.1; ONNX export onnx-community)",
            runtime_modules=["onnxruntime", "tokenizers"],
            install_modules=["onnx", "sympy"], transform="quantize_embeddings",
            hardware="CPU only; ~0.8 GB RAM while running (released when idle).",
            evaluation=(
                "2026-10-28: the repository's int8/uint8 exports score nothing (max 0.15 on 'Barack Obama "
                "visited Paris'), so the pinned fp32 graph is downloaded and only its embedding table is "
                "quantised on install (scores within 0.01 of fp32). Entity gold gate not yet run: off by default."
            ),
            extra_files={"entity_labels.json": "gliner_entity_labels"},
            validator="gliner", setting_key="use_gliner",
        ),
        ModelDependencySpec(
            id="sentence_encoder",
            label="Sentence encoder (bge-small, ONNX)",
            description=(
                "Turns sentences into vectors used as features for argument-role "
                "labelling and for comparing the contexts of entity mentions."
            ),
            kind="onnx_hf", role="sentence_encoder", task="sentence_roles", size_hint="~35 MB",
            hf_repo="Xenova/bge-small-en-v1.5",
            hf_revision="ea104dacec62c0de699686887e3f920caeb4f3e3",
            hf_files={
                "model.onnx": ["onnx/model_int8.onnx"],
                "tokenizer.json": ["tokenizer.json"],
                "config.json": ["config.json"],
            },
            file_sha256={
                "model.onnx": "bf64d05457cb391fa88d045faf5927a15ea36d96228ddf23ea970087afdc1197",
                "tokenizer.json": "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
                "config.json": "fa73f90bf92c8cace1fbcb709626306f2bdbc9ea3e5b5f94b440df9b6aa56350",
            },
            file_sizes={"model.onnx": 33760831, "tokenizer.json": 711396, "config.json": 683},
            dest_dir=model_dir("sentence_encoder"),
            license="mit (base model BAAI/bge-small-en-v1.5; ONNX export Xenova)",
            runtime_modules=["onnxruntime", "tokenizers"],
            hardware="CPU only; ~100 MB RAM while running.",
            evaluation="Unmeasured: used only once an argument-role head trained on adjudicated data passes its gate.",
            validator="encoder",
        ),
        ModelDependencySpec(
            id="language_id",
            label="Language identifier (GlotLID, ONNX)",
            description=(
                "Identifies the language of each passage (over 2,000 languages, "
                "including Hawaiian, Latin and Maya languages) so the English name "
                "model is not trusted on other languages. Without it a built-in "
                "function-word vote covers a few common languages."
            ),
            kind="onnx_hf", role="language_identifier", task="entities", size_hint="~430 MB",
            hf_repo="TigreGotico/glotlid-onnx",
            hf_revision="324ea6246b96c997c7eb63089ec5f272dc567b41",
            hf_files={
                "model.onnx": ["glotlid.int8.onnx"],
                "vocab.txt": ["vocab.txt"],
                "labels.json": ["labels.json"],
                "config.json": ["config.json"],
            },
            file_sha256={
                "model.onnx": "3b2c8c1fa4ffeb46f04280bf9f93d9949ff61320e9b1877d8d8f08bfeac2e84d",
                "vocab.txt": "59fc76ddaae19c21c205467bd45ae65f3ecd8bd80404066fbf946ea53f182039",
                "labels.json": "653f2e4c2a1b755b18889c7d821f8be648b2a974cd346279a772bf6633d6407f",
                "config.json": "d50dd161cd9d40e980fbd65c250a973e2667b17048736bc1bc4ed391ae3dfa45",
            },
            file_sizes={"model.onnx": 418935890, "vocab.txt": 5590445, "labels.json": 44142, "config.json": 105},
            dest_dir=model_dir("language_id"),
            license="apache-2.0 (GlotLID, cis-lmu; ONNX export TigreGotico)",
            runtime_modules=["onnxruntime"],
            hardware="CPU only; ~460 MB RAM while running; under 1 ms per sentence.",
            evaluation="Unmeasured: stays off by default until it passes the language gate.",
            validator="glotlid", setting_key="use_language_model",
        ),
        ModelDependencySpec(
            id="geonames_gazetteer",
            label="Place-name gazetteer (GeoNames)",
            description=(
                "A local list of about 240,000 populated places, regions and countries "
                "with their alternate names. It supports typing a name as a place only "
                "when the document also uses it like one (\"in Tekanto\"); it never "
                "decides alone. GeoNames data is CC BY 4.0 (geonames.org)."
            ),
            kind="dataset", role="gazetteer", task="entities", size_hint="~15 MB download, ~40 MB built",
            dataset_urls={
                "cities": "https://download.geonames.org/export/dump/cities500.zip",
                "admin1": "https://download.geonames.org/export/dump/admin1CodesASCII.txt",
                "countries": "https://download.geonames.org/export/dump/countryInfo.txt",
            },
            dataset_builder="geonames_places",
            dest_dir=model_dir("geonames_gazetteer"),
            license="cc-by-4.0 (GeoNames, geonames.org)",
            hardware="Disk lookups; under 20 MB RAM.",
            evaluation=(
                "GeoNames is republished daily, so files cannot be pinned in advance; installation "
                "checks the format and records the SHA-256 of what it downloaded. Unmeasured: off "
                "by default until it passes the entity gold gate."
            ),
            setting_key="use_gazetteer",
        ),
        ModelDependencySpec(
            id="sentence_classifier",
            label="Custom sentence-role model (manual import)",
            description=(
                "A task-trained ONNX classifier you supply (model.onnx, "
                "tokenizer.json, labels.json). When present it replaces the "
                "heuristic. No validated task-trained model is published for "
                "one-click install yet; the built-in heuristic is always active "
                "without one."
            ),
            kind="manual",
            role="sentence_classifier",
            task="sentence_roles",
            size_hint="varies",
            hf_files={"model.onnx": [], "tokenizer.json": [], "labels.json": []},
            dest_dir=model_dir("sentence_role_classifier"),
            runtime_modules=["onnxruntime", "tokenizers"],
        ),
        ModelDependencySpec(
            id="tesseract_ocr",
            label="OCR engine (Tesseract)",
            description=(
                "Reads text from scanned PDFs and images so intelligence, search "
                "and highlighting work on them. Installed through your system "
                "package manager; on Linux your desktop asks for the administrator "
                "password. Scanned PDFs are blocked from intelligence until OCR runs."
            ),
            kind="system",
            role="ocr",
            size_hint="~30 MB",
            system_binary="tesseract",
            license="apache-2.0",
            hardware="CPU only; OCR runs pages in parallel, roughly 1–3 s per page per core.",
        ),
        ModelDependencySpec(
            id="dateparser",
            label="dateparser",
            description=(
                "Stronger natural-language date normalization. The local "
                "regex/dateutil path remains available without it."
            ),
            kind="pip",
            role="dates",
            size_hint="small",
            pip_package="dateparser",
        ),
    ]
