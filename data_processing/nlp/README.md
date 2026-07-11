# NLP CSV Export

This module reads MongoDB `cleaned_documents` and exports Neo4j-ready CSV
files. It has two stages:

1. `nlp_to_csv.py` extracts candidate graph nodes and relationships with jieba,
   spaCy, and a required local BERT model.
2. `postprocess_csv.py` performs strict filtering, normalization, deduplication,
   relationship direction checks, and review-report generation.

The shared CSV schema lives in `graph_schema.py` so both stages read and write
the same Neo4j-compatible columns without duplicating schema constants.

## Run Extraction

From `data_processing`:

```powershell
.\.venv\Scripts\python.exe .\nlp\nlp_to_csv.py
```

By default this reads `nlp/debug_params.json` for `keyword`, `limit`,
`bert_model`, and `raw_output_dir`. Command-line arguments override the JSON
values for one run.

Optional:

```powershell
.\.venv\Scripts\python.exe .\nlp\nlp_to_csv.py --keyword 糖尿病
.\.venv\Scripts\python.exe .\nlp\nlp_to_csv.py --limit 5
.\.venv\Scripts\python.exe .\nlp\nlp_to_csv.py --output-dir .\nlp\output
.\.venv\Scripts\python.exe .\nlp\nlp_to_csv.py --bert-model C:\models\bert-base-chinese
```

BERT is required. The extractor loads
`data_processing/nlp/models/bert-base-chinese` automatically when it exists. You
can override that path with `--bert-model` or `NLP_BERT_MODEL`. If no BERT model
is available, extraction exits with a clear error instead of silently falling
back to rules.

Raw extraction files are written to:

```text
data_processing/nlp/output/nodes.csv
data_processing/nlp/output/relationships.csv
```

## Post-process

Run this after `nlp_to_csv.py` to remove generic terms, sentence fragments,
low-confidence relationships, and label/direction mismatches:

```powershell
.\.venv\Scripts\python.exe .\nlp\postprocess_csv.py
```

By default this reads `nlp/debug_params.json` for `raw_output_dir`,
`cleaned_output_dir`, and `min_confidence`.

Cleaned files are written to:

```text
data_processing/nlp/output_cleaned/nodes.csv
data_processing/nlp/output_cleaned/relationships.csv
data_processing/nlp/output_cleaned/rejects.csv
data_processing/nlp/output_cleaned/summary.csv
```

The default post-processing threshold is `0.85`, which is intentionally strict.
You can make relationship filtering looser or stricter with:

```powershell
.\.venv\Scripts\python.exe .\nlp\postprocess_csv.py --min-confidence 0.82
.\.venv\Scripts\python.exe .\nlp\postprocess_csv.py --min-confidence 0.90
```

## CSV Schema

`nodes.csv`:

```text
node_id:ID,name,type:LABEL,source_doc_ids,description
```

`relationships.csv`:

```text
:START_ID,:END_ID,:TYPE,source_doc_id,evidence,confidence:float,section
```

## Node Types

```text
Disease      disease and complication disease nodes
Symptom      symptoms and clinical manifestations
Drug         drug nodes
Examination  examination and lab-test nodes
Treatment    non-drug treatment plans
```

## Relationship Types

```text
Disease -> Symptom      HAS_SYMPTOM
Disease -> Examination  REQUIRES_EXAM
Drug    -> Disease      TREATS_DISEASE
Disease -> Treatment    TREATED_BY
Disease -> Disease      HAS_COMPLICATION
```
