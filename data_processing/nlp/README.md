# NLP CSV Export

This module extracts medical graph nodes and relationships from MongoDB
`cleaned_documents` and writes CSV files for Neo4j.

## Run

From `data_processing`:

```powershell
.\.venv\Scripts\python.exe .\nlp\nlp_to_csv.py
```

Optional:

```powershell
.\.venv\Scripts\python.exe .\nlp\nlp_to_csv.py --keyword 糖尿病
.\.venv\Scripts\python.exe .\nlp\nlp_to_csv.py --limit 5
.\.venv\Scripts\python.exe .\nlp\nlp_to_csv.py --output-dir .\nlp\output
```

## Output

```text
data_processing/nlp/output/nodes.csv
data_processing/nlp/output/relationships.csv
```

`nodes.csv` columns:

```text
node_id:ID,name,type:LABEL,source_doc_ids,description
```

`relationships.csv` columns:

```text
:START_ID,:END_ID,:TYPE,source_doc_id,evidence,confidence:float,section
```

## Node Types

```text
Disease
Symptom
Drug
Examination
Treatment
Department
Complication
Population
```

## Relationship Types

```text
HAS_SYMPTOM
TREATED_BY
TREATED_WITH_DRUG
REQUIRES_EXAM
HAS_COMPLICATION
BELONGS_TO_DEPARTMENT
CONTRAINDICATED_FOR
INTERACTS_WITH
```

The extractor is rule-based and keeps an `evidence` sentence for each
relationship, so wrong or weak edges can be traced back and adjusted.
