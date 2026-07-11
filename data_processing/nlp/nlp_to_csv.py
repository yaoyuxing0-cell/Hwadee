"""
Extract medical graph CSV files from MongoDB cleaned_documents.

This script is the extraction stage. It reads cleaned MongoDB documents, uses
jieba/spaCy plus a required local BERT model to score candidate medical graph
terms, and writes recall-oriented CSV files. Strict phrase filtering and final
deduplication belong to postprocess_csv.py.

Nodes:
    Disease, Symptom, Drug, Examination, Treatment

Relationships:
    Disease -> Symptom      HAS_SYMPTOM
    Disease -> Examination  REQUIRES_EXAM
    Drug    -> Disease      TREATS_DISEASE
    Disease -> Treatment    TREATED_BY
    Disease -> Disease      HAS_COMPLICATION
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import jieba
import jieba.posseg as pseg
import spacy
from pymongo import MongoClient

from debug_config import (
    DEFAULT_DEBUG_PARAMS_PATH,
    config_int,
    config_keyword,
    config_path,
    load_debug_params,
)
from graph_schema import (
    NODE_DESC,
    NODE_DOCS,
    NODE_FIELDS,
    NODE_ID,
    NODE_LABEL,
    NODE_NAME,
    NODE_TYPES,
    REL_CONF,
    REL_DOC,
    REL_END,
    REL_EVIDENCE,
    REL_SECTION,
    REL_START,
    REL_TYPE,
    RELATION_FIELDS,
    RELATION_TYPES,
    stable_node_id,
)


CONFIG_PATH = Path(__file__).resolve().parents[1] / "crawler" / "crawler_config.json"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "output"
DEFAULT_BERT_MODEL_DIR = Path(__file__).resolve().parent / "models" / "bert-base-chinese"

REFERENCE_RE = re.compile(r"\[\d+(?:-\d+)?\]")
WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])")
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")

SYMPTOM_HEADINGS = ("症状", "临床表现", "常见症状", "典型症状", "早期症状", "伴随症状")
DRUG_HEADINGS = ("药物治疗", "用药", "药品", "药物")
EXAM_HEADINGS = ("检查", "检验", "诊断依据", "诊断流程", "体格检查", "实验室检查", "影像学检查", "辅助检查")
TREATMENT_HEADINGS = ("治疗", "一般治疗", "手术治疗", "中医治疗", "急性期治疗", "康复治疗", "前沿治疗")
COMPLICATION_HEADINGS = ("并发症", "合并症")

LIST_MARKERS = (
    "包括",
    "主要包括",
    "有",
    "为",
    "如",
    "例如",
    "可见",
    "可有",
    "出现",
    "可出现",
    "表现为",
    "主要表现为",
    "症状为",
    "特征为",
)

SYMPTOM_LEXICON = {
    "头晕",
    "头痛",
    "发热",
    "咳嗽",
    "咳痰",
    "胸痛",
    "胸闷",
    "心悸",
    "气短",
    "呼吸困难",
    "恶心",
    "呕吐",
    "腹痛",
    "腹泻",
    "便秘",
    "乏力",
    "疲劳",
    "水肿",
    "麻木",
    "瘙痒",
    "黄疸",
    "出血",
    "贫血",
    "视物模糊",
    "意识丧失",
    "昏迷",
    "多饮",
    "多尿",
    "多食",
    "体重下降",
    "食欲减退",
    "心绞痛",
    "发绀",
}

SYMPTOM_SUFFIXES = ("痛", "疼痛", "热", "咳", "肿", "麻木", "困难", "乏力", "出血", "昏迷", "黄疸", "心悸")

DRUG_LEXICON = {
    "阿司匹林",
    "胰岛素",
    "二甲双胍",
    "硝酸甘油",
    "硝酸酯类",
    "β受体阻滞剂",
    "ACEI",
    "ARB",
    "他汀",
    "他汀类药物",
    "抗血小板药",
    "抗凝药",
    "抗生素",
    "降糖药",
    "降压药",
    "利尿剂",
    "钙拮抗剂",
    "青霉素",
    "头孢",
    "异烟肼",
    "利福平",
    "乙胺丁醇",
    "吡嗪酰胺",
    "布洛芬",
    "对乙酰氨基酚",
    "奥美拉唑",
    "硝苯地平",
    "美托洛尔",
}

DRUG_PATTERN = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9β-]{2,24}"
    r"(?:药|药物|素|片|胶囊|剂|针|酯|胺|霉素|沙星|他汀|普利|沙坦|洛尔|地平|拉唑)"
)

EXAM_LEXICON = {
    "血常规",
    "尿常规",
    "心电图",
    "动态心电图",
    "超声心动图",
    "冠脉CT血管成像",
    "冠脉造影",
    "胃镜",
    "肠镜",
    "CT",
    "MRI",
    "X线",
    "B超",
    "血糖检测",
    "尿糖检测",
    "体格检查",
    "血液检查",
    "影像学检查",
    "实验室检查",
    "超声检查",
    "病理检查",
}

EXAM_PATTERN = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9-]{1,24}"
    r"(?:检查|检测|检验|测定|试验|造影|心电图|CT|MRI|超声|胃镜|肠镜|血常规|尿常规)"
)

TREATMENT_LEXICON = {
    "手术治疗",
    "一般治疗",
    "中医治疗",
    "介入治疗",
    "放射治疗",
    "化疗",
    "放疗",
    "靶向治疗",
    "免疫治疗",
    "生活方式干预",
    "饮食控制",
    "运动治疗",
    "康复训练",
    "康复治疗",
    "支持治疗",
    "对症治疗",
    "抗感染治疗",
    "降压治疗",
    "降糖治疗",
    "补液治疗",
    "氧疗",
    "透析",
    "经皮冠状动脉介入治疗",
    "冠状动脉旁路移植术",
    "PCI",
    "CABG",
    "外科手术",
    "微创手术",
    "开放手术",
    "营养支持",
    "物理治疗",
}

TREATMENT_PATTERN = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9（）()β-]{0,16}"
    r"(?:手术治疗|手术|介入治疗|康复训练|康复治疗|饮食控制|运动治疗|氧疗|透析|化疗|放疗|免疫治疗|靶向治疗|中医治疗|生活方式干预)"
)

DISEASE_SUFFIXES = (
    "病",
    "症",
    "炎",
    "癌",
    "综合征",
    "衰竭",
    "损害",
    "病变",
    "感染",
    "梗死",
    "出血",
    "畸形",
    "缺陷",
    "坏死",
    "休克",
    "中毒",
    "梗阻",
)

DISEASE_PATTERN = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9β-]{2,10}"
    r"(?:病|症|炎|癌|综合征|衰竭|损害|病变|感染|梗死|出血|畸形|缺陷|坏死|休克|中毒|梗阻)"
)

KNOWN_DISEASE_TERMS = {
    "高血压",
    "糖尿病",
    "冠心病",
    "脑卒中",
    "脑梗死",
    "脑出血",
    "心力衰竭",
    "心律失常",
    "心肌梗死",
    "肾衰竭",
    "呼吸衰竭",
    "休克",
    "感染",
    "肺炎",
    "贫血",
    "低血糖",
    "低钙血症",
    "上消化道出血",
    "消化道出血",
    "肾功能损害",
    "周围神经病变",
    "代谢综合征",
}

BERT_ANCHORS = {
    "Symptom": "症状 临床表现 不适 体征",
    "Drug": "药品 药物 用药 治疗药",
    "Examination": "检查 检验 项目 诊断 检测",
    "Treatment": "非药物治疗 手术 康复 饮食 运动 介入",
    "Disease": "疾病 并发症 病变 综合征",
}


@dataclass
class Node:
    node_id: str
    name: str
    label: str
    source_doc_ids: set[str] = field(default_factory=set)
    description: str = ""


@dataclass
class Relationship:
    start_id: str
    end_id: str
    rel_type: str
    source_doc_id: str
    evidence: str
    confidence: float
    section: str


class BertScorer:
    """Score extraction candidates with a required local BERT model."""

    def __init__(self, model_path: str = "") -> None:
        self.enabled = False
        self.model_path = self.resolve_model_path(model_path)
        self.tokenizer = None
        self.model = None
        self.torch = None
        self.anchor_vectors: dict[str, Any] = {}
        self.score_cache: dict[tuple[str, str, str], float] = {}

        try:
            import torch
            from transformers import AutoModel, AutoTokenizer

            self.torch = torch
            self.tokenizer = AutoTokenizer.from_pretrained(self.model_path)
            self.model = AutoModel.from_pretrained(self.model_path)
            self.model.eval()
            self.enabled = True
            for label, anchor in BERT_ANCHORS.items():
                self.anchor_vectors[label] = self.embed(anchor)
        except Exception as exc:
            raise RuntimeError(f"failed to load BERT model from {self.model_path}: {exc}") from exc

    @staticmethod
    def resolve_model_path(model_path: str = "") -> str:
        resolved = model_path or os.environ.get("NLP_BERT_MODEL", "")
        if not resolved and DEFAULT_BERT_MODEL_DIR.exists():
            resolved = str(DEFAULT_BERT_MODEL_DIR)
        if not resolved:
            raise RuntimeError(
                "BERT model is required. Put bert-base-chinese under "
                "data_processing/nlp/models/bert-base-chinese, set NLP_BERT_MODEL, "
                "or pass --bert-model."
            )
        return resolved

    def embed(self, text: str) -> Any:
        if not self.enabled or self.tokenizer is None or self.model is None or self.torch is None:
            return None
        with self.torch.no_grad():
            batch = self.tokenizer(text, return_tensors="pt", truncation=True, max_length=64)
            output = self.model(**batch)
            hidden = output.last_hidden_state
            return hidden[:, 0, :].squeeze(0)

    def score(self, label: str, candidate: str, evidence: str) -> float:
        if not self.enabled or self.torch is None:
            return 0.5
        cache_key = (label, candidate, evidence[:120])
        cached_score = self.score_cache.get(cache_key)
        if cached_score is not None:
            return cached_score
        anchor = self.anchor_vectors.get(label)
        vector = self.embed(f"{candidate}。{evidence[:120]}")
        if anchor is None or vector is None:
            return 0.5
        similarity = self.torch.nn.functional.cosine_similarity(anchor, vector, dim=0).item()
        score = max(0.0, min(1.0, (similarity + 1.0) / 2.0))
        self.score_cache[cache_key] = score
        return score


class NlpTools:
    def __init__(self, bert_model: str = "", disease_vocab: set[str] | None = None) -> None:
        self.spacy_nlp = spacy.blank("zh")
        self.bert = BertScorer(bert_model)
        self.disease_vocab = set(disease_vocab or set()) | set(KNOWN_DISEASE_TERMS)
        for term in (
            set(DRUG_LEXICON)
            | set(EXAM_LEXICON)
            | set(TREATMENT_LEXICON)
            | set(SYMPTOM_LEXICON)
            | self.disease_vocab
        ):
            jieba.add_word(term, freq=20000)

    def jieba_terms(self, text: str) -> list[tuple[str, str]]:
        return [(word, flag) for word, flag in pseg.cut(text) if word.strip()]

    def spacy_tokens(self, text: str) -> list[str]:
        return [token.text for token in self.spacy_nlp(text) if token.text.strip()]

    def bert_score(self, label: str, candidate: str, evidence: str) -> float:
        return self.bert.score(label, candidate, evidence)


def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = text.replace("\xa0", " ").replace("\u3000", " ")
    text = REFERENCE_RE.sub("", text)
    return "\n".join(
        WHITESPACE_RE.sub(" ", line).strip()
        for line in text.splitlines()
        if WHITESPACE_RE.sub(" ", line).strip()
    )


def normalize_name(value: Any) -> str:
    text = clean_text(value)
    text = re.sub(r"^[（(【\[]+", "", text)
    text = re.sub(r"[）)】\]]+$", "", text)
    text = text.strip(" ：:，,。；;、")
    text = re.sub(r"^(如|例如|比如|包括|主要包括|常见|主要|可|会|需|应|的|发生)", "", text)
    text = re.sub(r"(等|等等|为主|之一|相关)$", "", text)
    return text.strip(" ：:，,。；;、")


def source_doc_id(document: dict[str, Any]) -> str:
    return clean_text(document.get("doc_id")) or str(document.get("_id"))


def is_candidate_term(value: Any, max_len: int = 24) -> bool:
    """Keep only structurally usable candidates.

    Domain-specific filtering is intentionally left to postprocess_csv.py.
    """

    term = normalize_name(value)
    if len(term) < 2 or len(term) > max_len:
        return False
    if not (CHINESE_RE.search(term) or re.search(r"[A-Za-z0-9]", term)):
        return False
    if len(re.findall(r"[，,。；;]", term)) > 0:
        return False
    return True


def split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for paragraph in clean_text(text).splitlines():
        parts = [part.strip() for part in SENTENCE_SPLIT_RE.split(paragraph) if part.strip()]
        sentences.extend(parts)
    return sentences


def short_evidence(sentence: str, limit: int = 180) -> str:
    sentence = clean_text(sentence).replace("\n", " ")
    if len(sentence) <= limit:
        return sentence
    return f"{sentence[:limit]}..."


def heading_matches(heading: str, choices: Iterable[str]) -> bool:
    return any(choice in heading for choice in choices)


def split_list_terms(text: str, max_len: int = 24) -> list[str]:
    value = normalize_name(text)
    value = re.split(r"[。！？!?；;]", value, maxsplit=1)[0]
    value = re.sub(
        r"^(包括|主要包括|有|为|如|例如|可见|可有|可出现|会出现|表现为|主要表现为|症状为|特征为)[:：]?",
        "",
        value,
    )
    raw_parts = re.split(r"[、，,；;/|]|以及|或者|或|和|与", value)

    terms: list[str] = []
    seen: set[str] = set()
    for raw_part in raw_parts:
        part = normalize_name(raw_part)
        part = re.sub(r"^\d+[.、]", "", part)
        part = re.sub(r"^[一二三四五六七八九十]+[、.]", "", part)
        if is_candidate_term(part, max_len=max_len) and part not in seen:
            terms.append(part)
            seen.add(part)
    return terms


def list_after_markers(sentence: str, max_len: int = 24) -> list[str]:
    hit_positions = [(sentence.find(marker), marker) for marker in LIST_MARKERS if marker in sentence]
    hit_positions = [(pos, marker) for pos, marker in hit_positions if pos >= 0]
    if not hit_positions:
        return []
    pos, marker = min(hit_positions, key=lambda item: item[0])
    return split_list_terms(sentence[pos + len(marker) :], max_len=max_len)


def find_known_terms(text: str, terms: Iterable[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for term in sorted(terms, key=len, reverse=True):
        if term in text and term not in seen:
            found.append(term)
            seen.add(term)
    return found


def find_pattern_terms(text: str, pattern: re.Pattern[str], max_len: int) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for match in pattern.findall(text):
        term = normalize_name(match)
        if is_candidate_term(term, max_len=max_len) and term not in seen:
            terms.append(term)
            seen.add(term)
    return terms


def lexical_candidates(tools: NlpTools, sentence: str, max_len: int) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for word, flag in tools.jieba_terms(sentence):
        term = normalize_name(word)
        if flag.startswith(("n", "v")) and is_candidate_term(term, max_len=max_len) and term not in seen:
            candidates.append(term)
            seen.add(term)
    for token in tools.spacy_tokens(sentence):
        term = normalize_name(token)
        if is_candidate_term(term, max_len=max_len) and term not in seen:
            candidates.append(term)
            seen.add(term)
    return candidates


def confidence(tools: NlpTools, label: str, base: float, candidate: str, evidence: str) -> float:
    bert_score = tools.bert_score(label, candidate, evidence)
    score = (base * 0.75) + (bert_score * 0.25)
    return round(max(0.0, min(0.99, score)), 2)


def extract_symptoms(tools: NlpTools, text: str) -> list[tuple[str, str, float]]:
    results: list[tuple[str, str, float]] = []
    seen: set[str] = set()
    for sentence in split_sentences(text):
        terms = find_known_terms(sentence, SYMPTOM_LEXICON)
        terms.extend(list_after_markers(sentence, max_len=14))
        for term in lexical_candidates(tools, sentence, max_len=12):
            if term in SYMPTOM_LEXICON or any(term.endswith(suffix) for suffix in SYMPTOM_SUFFIXES):
                terms.append(term)
        for term in terms:
            term = normalize_name(term)
            if term in seen or not is_candidate_term(term, max_len=14):
                continue
            if term not in SYMPTOM_LEXICON and not any(term.endswith(suffix) for suffix in SYMPTOM_SUFFIXES):
                continue
            results.append((term, sentence, confidence(tools, "Symptom", 0.86, term, sentence)))
            seen.add(term)
    return results


def extract_drugs(tools: NlpTools, text: str) -> list[tuple[str, str, float]]:
    results: list[tuple[str, str, float]] = []
    seen: set[str] = set()
    for sentence in split_sentences(text):
        terms = find_known_terms(sentence, DRUG_LEXICON)
        terms.extend(find_pattern_terms(sentence, DRUG_PATTERN, max_len=24))
        for term in terms:
            term = normalize_name(term)
            if term in seen or not is_candidate_term(term, max_len=24):
                continue
            results.append((term, sentence, confidence(tools, "Drug", 0.86, term, sentence)))
            seen.add(term)
    return results


def extract_examinations(tools: NlpTools, text: str) -> list[tuple[str, str, float]]:
    results: list[tuple[str, str, float]] = []
    seen: set[str] = set()
    for sentence in split_sentences(text):
        terms = find_known_terms(sentence, EXAM_LEXICON)
        terms.extend(find_pattern_terms(sentence, EXAM_PATTERN, max_len=24))
        for term in terms:
            term = normalize_name(term)
            if term in seen or not is_candidate_term(term, max_len=24):
                continue
            results.append((term, sentence, confidence(tools, "Examination", 0.86, term, sentence)))
            seen.add(term)
    return results


def extract_treatments(tools: NlpTools, text: str, heading: str = "") -> list[tuple[str, str, float]]:
    results: list[tuple[str, str, float]] = []
    seen: set[str] = set()

    heading_name = normalize_name(heading)
    if "治疗" in heading_name:
        if is_candidate_term(heading_name, max_len=18):
            if heading_name.count("（") == heading_name.count("）") and heading_name.count("(") == heading_name.count(")"):
                results.append((heading_name, heading_name, confidence(tools, "Treatment", 0.9, heading_name, heading_name)))
                seen.add(heading_name)

    for sentence in split_sentences(text):
        terms = find_known_terms(sentence, TREATMENT_LEXICON)
        if "手术" in sentence:
            terms.append("手术治疗")
        if "化疗" in sentence:
            terms.append("化疗")
        if "放疗" in sentence:
            terms.append("放疗")
        if "康复" in sentence:
            terms.append("康复治疗")
        if "饮食" in sentence or "营养" in sentence:
            terms.append("饮食控制")
        if "运动" in sentence:
            terms.append("运动治疗")
        for term in terms:
            term = normalize_name(term)
            if term in seen or not is_candidate_term(term, max_len=18):
                continue
            if "？" in term or "?" in term or term.count("（") != term.count("）") or term.count("(") != term.count(")"):
                continue
            results.append((term, sentence, confidence(tools, "Treatment", 0.84, term, sentence)))
            seen.add(term)
    return results


def refine_disease_term(term: str) -> str:
    value = normalize_name(term)
    value = re.split(r"[:：]", value, maxsplit=1)[0]
    for trigger in ("导致", "引发", "引起", "造成", "诱发", "出现", "并发", "包括", "发生"):
        if trigger in value:
            value = value.split(trigger, 1)[1]
    return normalize_name(value)


def extract_complication_diseases(tools: NlpTools, text: str) -> list[tuple[str, str, float]]:
    results: list[tuple[str, str, float]] = []
    seen: set[str] = set()
    for sentence in split_sentences(text):
        if not any(cue in sentence for cue in ("并发", "并发症", "合并症", "合并")):
            continue
        terms = find_pattern_terms(sentence, DISEASE_PATTERN, max_len=14)
        for term in terms:
            term = refine_disease_term(term)
            if term in seen or not is_candidate_term(term, max_len=14):
                continue
            if any(fragment in term for fragment in ("症状", "诊断", "检查", "体检", "治疗", "生活质量")):
                continue
            if not any(suffix in term for suffix in DISEASE_SUFFIXES):
                continue
            if term not in tools.disease_vocab:
                continue
            results.append((term, sentence, confidence(tools, "Disease", 0.82, term, sentence)))
            seen.add(term)
    return results


class GraphCsvBuilder:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.relationships: dict[tuple[str, str, str, str], Relationship] = {}

    def add_node(self, label: str, name: str, doc_id: str, description: str = "") -> str | None:
        name = normalize_name(name)
        if label not in NODE_TYPES or not is_candidate_term(name, max_len=40):
            return None
        node_id = stable_node_id(label, name)
        node = self.nodes.get(node_id)
        if node is None:
            node = Node(node_id=node_id, name=name, label=label, description=clean_text(description))
            self.nodes[node_id] = node
        if doc_id:
            node.source_doc_ids.add(doc_id)
        if not node.description and description:
            node.description = clean_text(description)
        return node_id

    def add_relation(
        self,
        start_label: str,
        start_name: str,
        end_label: str,
        end_name: str,
        rel_type: str,
        doc_id: str,
        evidence: str,
        score: float,
        section: str,
    ) -> None:
        if rel_type not in RELATION_TYPES:
            return
        start_id = self.add_node(start_label, start_name, doc_id)
        end_id = self.add_node(end_label, end_name, doc_id)
        if not start_id or not end_id or start_id == end_id:
            return
        relation = Relationship(
            start_id=start_id,
            end_id=end_id,
            rel_type=rel_type,
            source_doc_id=doc_id,
            evidence=short_evidence(evidence),
            confidence=round(score, 2),
            section=normalize_name(section),
        )
        key = (start_id, end_id, rel_type, doc_id)
        existing = self.relationships.get(key)
        if existing is None or relation.confidence > existing.confidence:
            self.relationships[key] = relation

    def write_csv(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)

        with open(output_dir / "nodes.csv", "w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=NODE_FIELDS,
            )
            writer.writeheader()
            for node in sorted(self.nodes.values(), key=lambda item: (item.label, item.name)):
                writer.writerow(
                    {
                        NODE_ID: node.node_id,
                        NODE_NAME: node.name,
                        NODE_LABEL: node.label,
                        NODE_DOCS: ";".join(sorted(node.source_doc_ids)),
                        NODE_DESC: node.description,
                    }
                )

        with open(output_dir / "relationships.csv", "w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=RELATION_FIELDS,
            )
            writer.writeheader()
            for relation in sorted(
                self.relationships.values(),
                key=lambda item: (item.rel_type, item.source_doc_id, item.start_id, item.end_id),
            ):
                writer.writerow(
                    {
                        REL_START: relation.start_id,
                        REL_END: relation.end_id,
                        REL_TYPE: relation.rel_type,
                        REL_DOC: relation.source_doc_id,
                        REL_EVIDENCE: relation.evidence,
                        REL_CONF: relation.confidence,
                        REL_SECTION: relation.section,
                    }
                )


def iter_cleaned_documents(collection, keyword: str | None, limit: int) -> Iterable[dict[str, Any]]:
    query: dict[str, Any] = {"status": "cleaned", "doc_id": {"$exists": True, "$ne": None}}
    if keyword:
        query["$or"] = [{"keyword": keyword}, {"title": keyword}]

    cursor = collection.find(query).sort([("keyword", 1), ("updated_at", -1)])
    if limit > 0:
        cursor = cursor.limit(limit)
    return cursor


def build_disease_vocab(collection) -> set[str]:
    vocab = set(KNOWN_DISEASE_TERMS)
    cursor = collection.find(
        {"status": "cleaned", "doc_id": {"$exists": True, "$ne": None}},
        {"title": 1, "keyword": 1, "sections.heading": 1},
    )
    for document in cursor:
        for value in (document.get("title"), document.get("keyword")):
            term = normalize_name(value)
            if is_candidate_term(term, max_len=24):
                vocab.add(term)

        sections = document.get("sections")
        if not isinstance(sections, list):
            continue
        for section in sections:
            if not isinstance(section, dict):
                continue
            heading = normalize_name(section.get("heading"))
            if is_candidate_term(heading, max_len=14) and any(suffix in heading for suffix in DISEASE_SUFFIXES):
                vocab.add(heading)
    return vocab


def process_basic_info(builder: GraphCsvBuilder, tools: NlpTools, document: dict[str, Any], disease_name: str, doc_id: str) -> None:
    basic_info = document.get("basic_info")
    if not isinstance(basic_info, dict):
        return
    for raw_key, raw_value in basic_info.items():
        key = normalize_name(raw_key)
        value = clean_text(raw_value)
        if not value:
            continue
        if "症状" in key:
            for term in split_list_terms(value, max_len=14):
                if term in SYMPTOM_LEXICON or any(term.endswith(suffix) for suffix in SYMPTOM_SUFFIXES):
                    builder.add_relation("Disease", disease_name, "Symptom", term, "HAS_SYMPTOM", doc_id, value, 0.92, key)
        elif "治疗" in key:
            for term, evidence, score in extract_treatments(tools, value, key):
                builder.add_relation("Disease", disease_name, "Treatment", term, "TREATED_BY", doc_id, evidence, score, key)


def process_section(builder: GraphCsvBuilder, tools: NlpTools, disease_name: str, doc_id: str, heading: str, content: str) -> None:
    heading = normalize_name(heading)
    text = clean_text(content)
    if not text:
        return

    if heading_matches(heading, SYMPTOM_HEADINGS):
        for term, evidence, score in extract_symptoms(tools, text):
            builder.add_relation("Disease", disease_name, "Symptom", term, "HAS_SYMPTOM", doc_id, evidence, score, heading)

    if heading_matches(heading, EXAM_HEADINGS):
        for term, evidence, score in extract_examinations(tools, text):
            builder.add_relation("Disease", disease_name, "Examination", term, "REQUIRES_EXAM", doc_id, evidence, score, heading)

    if heading_matches(heading, DRUG_HEADINGS):
        for term, evidence, score in extract_drugs(tools, text):
            builder.add_relation("Drug", term, "Disease", disease_name, "TREATS_DISEASE", doc_id, evidence, score, heading)

    if heading_matches(heading, TREATMENT_HEADINGS):
        for term, evidence, score in extract_drugs(tools, text):
            builder.add_relation("Drug", term, "Disease", disease_name, "TREATS_DISEASE", doc_id, evidence, score, heading)
        for term, evidence, score in extract_treatments(tools, text, heading):
            builder.add_relation("Disease", disease_name, "Treatment", term, "TREATED_BY", doc_id, evidence, score, heading)

    if heading_matches(heading, COMPLICATION_HEADINGS):
        for term, evidence, score in extract_complication_diseases(tools, text):
            builder.add_relation("Disease", disease_name, "Disease", term, "HAS_COMPLICATION", doc_id, evidence, score, heading)

    for sentence in split_sentences(text):
        if any(cue in sentence for cue in ("症状", "表现", "出现", "伴有", "可见")):
            for term, evidence, score in extract_symptoms(tools, sentence):
                builder.add_relation("Disease", disease_name, "Symptom", term, "HAS_SYMPTOM", doc_id, evidence, min(score, 0.82), heading)

        if any(cue in sentence for cue in ("检查", "检验", "检测", "诊断", "造影", "心电图", "CT", "MRI", "超声")):
            for term, evidence, score in extract_examinations(tools, sentence):
                builder.add_relation("Disease", disease_name, "Examination", term, "REQUIRES_EXAM", doc_id, evidence, min(score, 0.82), heading)

        if any(cue in sentence for cue in ("治疗", "手术", "康复", "饮食", "运动", "介入", "透析", "氧疗")):
            for term, evidence, score in extract_drugs(tools, sentence):
                builder.add_relation("Drug", term, "Disease", disease_name, "TREATS_DISEASE", doc_id, evidence, min(score, 0.82), heading)
            for term, evidence, score in extract_treatments(tools, sentence, heading):
                builder.add_relation("Disease", disease_name, "Treatment", term, "TREATED_BY", doc_id, evidence, min(score, 0.8), heading)



def process_document(builder: GraphCsvBuilder, tools: NlpTools, document: dict[str, Any]) -> None:
    doc_id = source_doc_id(document)
    disease_name = normalize_name(document.get("title")) or normalize_name(document.get("keyword"))
    if not disease_name:
        return

    builder.add_node("Disease", disease_name, doc_id, description=clean_text(document.get("summary")))
    process_basic_info(builder, tools, document, disease_name, doc_id)

    sections = document.get("sections")
    if not isinstance(sections, list):
        return
    for section in sections:
        if not isinstance(section, dict):
            continue
        process_section(
            builder,
            tools,
            disease_name=disease_name,
            doc_id=doc_id,
            heading=clean_text(section.get("heading")),
            content=clean_text(section.get("content")),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract candidate medical graph CSV files with required BERT scoring.")
    parser.add_argument("--params", type=Path, default=DEFAULT_DEBUG_PARAMS_PATH, help="JSON debug parameter file.")
    parser.add_argument("--keyword", help="Only process one disease keyword/title.")
    parser.add_argument("--limit", type=int, help="Maximum cleaned documents to process.")
    parser.add_argument("--output-dir", type=Path, help="CSV output directory.")
    parser.add_argument(
        "--bert-model",
        help="Local HuggingFace BERT model path. Defaults to nlp/models/bert-base-chinese or NLP_BERT_MODEL.",
    )
    args = parser.parse_args()
    debug_params = load_debug_params(args.params)

    keyword = args.keyword if args.keyword is not None else config_keyword(debug_params.get("keyword"))
    limit = args.limit if args.limit is not None else config_int(debug_params.get("limit"), 0)
    output_dir = args.output_dir or config_path(debug_params.get("raw_output_dir"), DEFAULT_OUTPUT_DIR)
    bert_model_path = args.bert_model
    if bert_model_path is None:
        configured_bert = config_path(debug_params.get("bert_model"))
        bert_model_path = str(configured_bert) if configured_bert is not None else ""

    config = load_config()
    mongodb = config["mongodb"]
    cleaning = config["cleaning"]

    client = MongoClient(mongodb["uri"], serverSelectionTimeoutMS=5000)
    collection = client[mongodb["database"]][cleaning["target_collection"]]

    try:
        tools = NlpTools(
            bert_model=bert_model_path,
            disease_vocab=build_disease_vocab(collection),
        )
    except RuntimeError as exc:
        client.close()
        raise SystemExit(str(exc)) from exc
    builder = GraphCsvBuilder()
    scanned = 0
    try:
        for document in iter_cleaned_documents(collection, keyword, limit):
            scanned += 1
            title = normalize_name(document.get("title")) or normalize_name(document.get("keyword")) or str(document.get("_id"))
            print(f"processing document {scanned}: {title}", flush=True)
            process_document(builder, tools, document)
            print(
                f"processed document {scanned}: nodes={len(builder.nodes)}, relationships={len(builder.relationships)}",
                flush=True,
            )
    finally:
        client.close()

    builder.write_csv(output_dir)
    print(
        "nlp csv generated: "
        f"documents={scanned}, "
        f"nodes={len(builder.nodes)}, "
        f"relationships={len(builder.relationships)}, "
        f"bert={tools.bert.model_path}, "
        f"output={output_dir}"
    )


if __name__ == "__main__":
    main()
