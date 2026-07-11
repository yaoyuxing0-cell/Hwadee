"""
Extract medical entities and relations from cleaned MongoDB documents to CSV.

The generated CSV files are designed for Neo4j import:

- output/nodes.csv
- output/relationships.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from pymongo import MongoClient


CONFIG_PATH = Path(__file__).resolve().parents[1] / "crawler" / "crawler_config.json"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "output"

NODE_TYPES = {
    "Disease",
    "Symptom",
    "Drug",
    "Examination",
    "Treatment",
    "Department",
    "Complication",
    "Population",
}

RELATION_TYPES = {
    "HAS_SYMPTOM",
    "TREATED_BY",
    "TREATED_WITH_DRUG",
    "REQUIRES_EXAM",
    "HAS_COMPLICATION",
    "BELONGS_TO_DEPARTMENT",
    "CONTRAINDICATED_FOR",
    "INTERACTS_WITH",
}

REFERENCE_RE = re.compile(r"\[\d+(?:-\d+)?\]")
WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])")
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")

STOP_TERMS = {
    "疾病",
    "患者",
    "医生",
    "医院",
    "情况",
    "方面",
    "方式",
    "方法",
    "治疗",
    "检查",
    "症状",
    "原因",
    "因素",
    "药物",
    "手术",
    "预防",
    "诊断",
    "表现",
    "其他",
    "以及",
    "包括",
    "常见",
    "主要",
}

BAD_TERM_SUBSTRINGS = (
    "可能",
    "应该",
    "需要",
    "可以",
    "由于",
    "排除",
    "以下",
    "以上",
    "一般",
    "无症状",
    "检查",
    "诊断",
    "严重时",
    "发病率",
    "可通过",
    "相对少见",
    "重要原因",
)

SYMPTOM_HEADINGS = (
    "症状",
    "常见症状",
    "典型症状",
    "早期症状",
    "伴随症状",
    "临床表现",
)
TREATMENT_HEADINGS = (
    "治疗",
    "一般治疗",
    "手术治疗",
    "中医治疗",
    "急性期治疗",
    "前沿治疗",
    "康复治疗",
)
DRUG_HEADINGS = ("药物治疗", "用药", "药品", "药物")
EXAM_HEADINGS = (
    "检查",
    "诊断",
    "体格检查",
    "实验室检查",
    "影像学检查",
    "诊断依据",
    "诊断流程",
    "鉴别诊断",
)
COMPLICATION_HEADINGS = ("并发症",)
DEPARTMENT_HEADINGS = ("就诊科室", "就医", "首诊科室")

LIST_MARKERS = (
    "包括",
    "主要包括",
    "有",
    "为",
    "如",
    "例如",
    "可见",
    "可有",
    "可出现",
    "会出现",
    "表现为",
    "主要表现为",
    "症状为",
    "特征为",
)

SYMPTOM_HINTS = (
    "痛",
    "疼痛",
    "发热",
    "咳嗽",
    "咳痰",
    "乏力",
    "疲劳",
    "头晕",
    "头痛",
    "胸闷",
    "胸痛",
    "心悸",
    "气短",
    "恶心",
    "呕吐",
    "腹泻",
    "便秘",
    "出血",
    "水肿",
    "麻木",
    "瘙痒",
    "黄疸",
    "呼吸困难",
    "视物模糊",
    "体重下降",
    "多饮",
    "多尿",
    "多食",
    "昏迷",
    "意识丧失",
)

DRUG_TERMS = (
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
    "血管紧张素转化酶抑制剂",
    "血管紧张素受体拮抗剂",
    "青霉素",
    "头孢",
    "异烟肼",
    "利福平",
    "乙胺丁醇",
    "吡嗪酰胺",
    "布洛芬",
    "对乙酰氨基酚",
)

DRUG_PATTERN = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9β-]{2,24}"
    r"(?:药|药物|素|片|胶囊|剂|针|酯|胺|霉素|沙星|他汀|普利|沙坦|洛尔|地平)"
)

TREATMENT_TERMS = (
    "药物治疗",
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
    "康复治疗",
    "支持治疗",
    "对症治疗",
    "抗感染治疗",
    "降压治疗",
    "降糖治疗",
    "补液治疗",
    "氧疗",
    "经皮冠状动脉介入治疗",
    "冠状动脉旁路移植术",
    "PCI",
    "CABG",
)

TREATMENT_PATTERN = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9（）()β-]{2,28}"
    r"(?:治疗|疗法|手术|介入|化疗|放疗|透析|康复|置换|移植|切除|干预)"
)

EXAM_TERMS = (
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
)

EXAM_PATTERN = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9-]{1,24}"
    r"(?:检查|检测|测定|试验|造影|心电图|CT|MRI|超声|胃镜|肠镜|血常规|尿常规)"
)

DEPARTMENT_TERMS = (
    "内分泌科",
    "心内科",
    "心血管内科",
    "呼吸内科",
    "消化内科",
    "神经内科",
    "肾内科",
    "血液科",
    "风湿免疫科",
    "肿瘤科",
    "普外科",
    "骨科",
    "急诊科",
    "眼科",
    "妇产科",
    "儿科",
    "感染科",
    "全科医学科",
    "皮肤科",
    "泌尿外科",
    "胸外科",
)

POPULATION_TERMS = (
    "儿童",
    "青少年",
    "成人",
    "老年人",
    "老人",
    "孕妇",
    "妊娠期妇女",
    "哺乳期妇女",
    "婴幼儿",
    "女性",
    "男性",
    "肥胖者",
    "运动员",
    "过敏者",
    "肝功能不全者",
    "肾功能不全者",
    "老年患者",
    "糖尿病患者",
    "高血压患者",
)

CONTRAINDICATION_MARKERS = ("禁用", "禁忌", "不宜", "慎用", "避免使用", "不能使用")
INTERACTION_MARKERS = ("相互作用", "合用", "联用", "同用", "相互影响")
COMPLICATION_SUFFIXES = ("病", "症", "炎", "癌", "衰竭", "损害", "病变", "感染", "梗死", "出血", "畸形", "缺陷", "坏死", "休克", "中毒", "梗阻")


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
    text = re.sub(r"^(如|例如|比如|包括|主要包括|常见|主要|可|会|需|应)", "", text)
    text = re.sub(r"^(的|发生)", "", text)
    text = re.sub(r"(等|等等|为主|之一|相关)$", "", text)
    return text.strip(" ：:，,。；;、")


def stable_id(label: str, name: str) -> str:
    digest = hashlib.sha1(f"{label}:{name}".encode("utf-8")).hexdigest()
    return f"{label}:{digest}"


def source_doc_id(document: dict[str, Any]) -> str:
    return clean_text(document.get("doc_id")) or str(document.get("_id"))


def is_valid_term(value: Any, max_len: int = 24) -> bool:
    term = normalize_name(value)
    if not term or term in STOP_TERMS:
        return False
    if any(fragment in term for fragment in BAD_TERM_SUBSTRINGS):
        return False
    if term.endswith(("时", "后", "前")):
        return False
    if len(term) < 2 or len(term) > max_len:
        return False
    if not (CHINESE_RE.search(term) or re.search(r"[A-Za-z0-9]", term)):
        return False
    if term.endswith(("患者", "医生", "人群", "疾病")) and len(term) > 8:
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


def split_list_terms(text: str, max_len: int = 24) -> list[str]:
    value = normalize_name(text)
    value = re.split(r"[。！？!?；;]", value, maxsplit=1)[0]
    value = re.sub(r"^(包括|主要包括|有|为|如|例如|可见|可有|可出现|会出现|表现为|主要表现为|症状为|特征为)[:：]?", "", value)
    raw_parts = re.split(r"[、，,；;/|]|以及|或者|或|和|与", value)

    terms: list[str] = []
    seen: set[str] = set()
    for raw_part in raw_parts:
        part = normalize_name(raw_part)
        part = re.sub(r"^\d+[.、]", "", part)
        part = re.sub(r"^[一二三四五六七八九十]+[、.]", "", part)
        if is_valid_term(part, max_len=max_len) and part not in seen:
            terms.append(part)
            seen.add(part)
    return terms


def refine_trigger_term(term: str) -> str:
    value = normalize_name(term)
    value = re.split(r"[:：]", value, maxsplit=1)[0]
    for trigger in ("导致", "引发", "引起", "造成", "诱发", "出现", "并发", "包括", "发生"):
        if trigger in value:
            value = value.split(trigger, 1)[1]
    return normalize_name(value)


def list_after_markers(sentence: str, markers: Iterable[str], max_len: int = 24) -> list[str]:
    hit_positions = [(sentence.find(marker), marker) for marker in markers if marker in sentence]
    hit_positions = [(pos, marker) for pos, marker in hit_positions if pos >= 0]
    if not hit_positions:
        return []

    pos, marker = min(hit_positions, key=lambda item: item[0])
    phrase = sentence[pos + len(marker) :]
    return split_list_terms(phrase, max_len=max_len)


def find_known_terms(text: str, terms: Iterable[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for term in sorted(terms, key=len, reverse=True):
        if term in text and term not in seen:
            found.append(term)
            seen.add(term)
    return found


def find_pattern_terms(text: str, pattern: re.Pattern[str], max_len: int = 28) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for match in pattern.findall(text):
        term = normalize_name(match)
        if is_valid_term(term, max_len=max_len) and term not in seen:
            terms.append(term)
            seen.add(term)
    return terms


def extract_context_terms(
    text: str,
    markers: Iterable[str] = LIST_MARKERS,
    max_len: int = 24,
) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for sentence in split_sentences(text):
        terms = list_after_markers(sentence, markers, max_len=max_len)
        if not terms and len(sentence) <= 80:
            terms = split_list_terms(sentence, max_len=max_len)
        for term in terms:
            if term not in seen:
                results.append((term, sentence))
                seen.add(term)
    return results


def extract_symptoms(text: str) -> list[tuple[str, str]]:
    results = extract_context_terms(text, max_len=18)
    seen = {term for term, _ in results}
    for sentence in split_sentences(text):
        for hint in SYMPTOM_HINTS:
            if hint in sentence and is_valid_term(hint, max_len=12) and hint not in seen:
                results.append((hint, sentence))
                seen.add(hint)
    return results


def extract_treatments(text: str, heading: str = "") -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    heading_name = normalize_name(heading)
    if "治疗" in heading_name and is_valid_term(heading_name, max_len=18):
        results.append((heading_name, heading_name))
        seen.add(heading_name)

    for term in find_known_terms(text, TREATMENT_TERMS):
        if term not in seen:
            results.append((term, text))
            seen.add(term)

    for term in find_pattern_terms(text, TREATMENT_PATTERN, max_len=28):
        if term not in seen and term not in STOP_TERMS:
            results.append((term, text))
            seen.add(term)
    return results


def extract_drugs(text: str) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for sentence in split_sentences(text):
        for term in find_known_terms(sentence, DRUG_TERMS):
            if term not in seen:
                results.append((term, sentence))
                seen.add(term)
        for term in find_pattern_terms(sentence, DRUG_PATTERN, max_len=24):
            if term not in seen and term not in {"药物", "药品", "用药"}:
                results.append((term, sentence))
                seen.add(term)
    return results


def extract_examinations(text: str, heading: str = "") -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    heading_name = normalize_name(heading)
    if any(token in heading_name for token in ("检查", "诊断")) and is_valid_term(heading_name, max_len=18):
        results.append((heading_name, heading_name))
        seen.add(heading_name)

    for sentence in split_sentences(text):
        for term in find_known_terms(sentence, EXAM_TERMS):
            if term not in seen:
                results.append((term, sentence))
                seen.add(term)
        for term in find_pattern_terms(sentence, EXAM_PATTERN, max_len=24):
            if term not in seen and term not in {"检查", "检测", "诊断"}:
                results.append((term, sentence))
                seen.add(term)
    return results


def extract_departments(text: str) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for sentence in split_sentences(text):
        for term in find_known_terms(sentence, DEPARTMENT_TERMS):
            if term not in seen:
                results.append((term, sentence))
                seen.add(term)
    return results


def extract_complications(text: str) -> list[tuple[str, str]]:
    results = extract_context_terms(text, markers=("包括", "有", "并发", "导致", "引发", "出现"), max_len=22)
    filtered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for term, sentence in results:
        term = refine_trigger_term(term)
        if term in seen:
            continue
        if any(suffix in term for suffix in COMPLICATION_SUFFIXES):
            if is_valid_term(term, max_len=18):
                filtered.append((term, sentence))
                seen.add(term)
    return filtered


def extract_populations(text: str) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for sentence in split_sentences(text):
        for term in find_known_terms(sentence, POPULATION_TERMS):
            if term not in seen:
                results.append((term, sentence))
                seen.add(term)
    return results


def heading_matches(heading: str, choices: Iterable[str]) -> bool:
    return any(choice in heading for choice in choices)


class GraphCsvBuilder:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.relationships: dict[tuple[str, str, str, str], Relationship] = {}

    def add_node(
        self,
        label: str,
        name: str,
        doc_id: str,
        description: str = "",
    ) -> str | None:
        name = normalize_name(name)
        if label not in NODE_TYPES or not is_valid_term(name, max_len=40):
            return None

        node_id = stable_id(label, name)
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
        confidence: float,
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
            confidence=round(confidence, 2),
            section=normalize_name(section),
        )
        key = (start_id, end_id, rel_type, doc_id)
        existing = self.relationships.get(key)
        if existing is None or relation.confidence > existing.confidence:
            self.relationships[key] = relation

    def write_csv(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)

        nodes_path = output_dir / "nodes.csv"
        with open(nodes_path, "w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=[
                    "node_id:ID",
                    "name",
                    "type:LABEL",
                    "source_doc_ids",
                    "description",
                ],
            )
            writer.writeheader()
            for node in sorted(self.nodes.values(), key=lambda item: (item.label, item.name)):
                writer.writerow(
                    {
                        "node_id:ID": node.node_id,
                        "name": node.name,
                        "type:LABEL": node.label,
                        "source_doc_ids": ";".join(sorted(node.source_doc_ids)),
                        "description": node.description,
                    }
                )

        relationships_path = output_dir / "relationships.csv"
        with open(relationships_path, "w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=[
                    ":START_ID",
                    ":END_ID",
                    ":TYPE",
                    "source_doc_id",
                    "evidence",
                    "confidence:float",
                    "section",
                ],
            )
            writer.writeheader()
            for relation in sorted(
                self.relationships.values(),
                key=lambda item: (item.rel_type, item.source_doc_id, item.start_id, item.end_id),
            ):
                writer.writerow(
                    {
                        ":START_ID": relation.start_id,
                        ":END_ID": relation.end_id,
                        ":TYPE": relation.rel_type,
                        "source_doc_id": relation.source_doc_id,
                        "evidence": relation.evidence,
                        "confidence:float": relation.confidence,
                        "section": relation.section,
                    }
                )


def process_basic_info(
    builder: GraphCsvBuilder,
    document: dict[str, Any],
    disease_name: str,
    doc_id: str,
) -> None:
    basic_info = document.get("basic_info")
    if not isinstance(basic_info, dict):
        return

    for raw_key, raw_value in basic_info.items():
        key = normalize_name(raw_key)
        value = clean_text(raw_value)
        if not value:
            continue

        if "科室" in key:
            for term in split_list_terms(value, max_len=16) + [item[0] for item in extract_departments(value)]:
                builder.add_relation(
                    "Disease",
                    disease_name,
                    "Department",
                    term,
                    "BELONGS_TO_DEPARTMENT",
                    doc_id,
                    value,
                    0.95,
                    key,
                )
        elif "症状" in key:
            for term in split_list_terms(value, max_len=18):
                builder.add_relation("Disease", disease_name, "Symptom", term, "HAS_SYMPTOM", doc_id, value, 0.92, key)
        elif "并发" in key:
            for term in split_list_terms(value, max_len=22):
                builder.add_relation(
                    "Disease",
                    disease_name,
                    "Complication",
                    term,
                    "HAS_COMPLICATION",
                    doc_id,
                    value,
                    0.92,
                    key,
                )
        elif "治疗" in key:
            for term in split_list_terms(value, max_len=24):
                builder.add_relation("Disease", disease_name, "Treatment", term, "TREATED_BY", doc_id, value, 0.9, key)


def process_section(
    builder: GraphCsvBuilder,
    disease_name: str,
    doc_id: str,
    heading: str,
    content: str,
) -> None:
    heading = normalize_name(heading)
    text = clean_text(content)
    if not text:
        return

    if heading_matches(heading, SYMPTOM_HEADINGS):
        for term, evidence in extract_symptoms(text):
            builder.add_relation("Disease", disease_name, "Symptom", term, "HAS_SYMPTOM", doc_id, evidence, 0.86, heading)

    if heading_matches(heading, TREATMENT_HEADINGS):
        for term, evidence in extract_treatments(text, heading):
            builder.add_relation("Disease", disease_name, "Treatment", term, "TREATED_BY", doc_id, evidence, 0.84, heading)

    if heading_matches(heading, DRUG_HEADINGS):
        for term, evidence in extract_drugs(text):
            builder.add_relation(
                "Disease",
                disease_name,
                "Drug",
                term,
                "TREATED_WITH_DRUG",
                doc_id,
                evidence,
                0.84,
                heading,
            )

    if heading_matches(heading, EXAM_HEADINGS):
        for term, evidence in extract_examinations(text, heading):
            builder.add_relation(
                "Disease",
                disease_name,
                "Examination",
                term,
                "REQUIRES_EXAM",
                doc_id,
                evidence,
                0.84,
                heading,
            )

    if heading_matches(heading, COMPLICATION_HEADINGS):
        for term, evidence in extract_complications(text):
            builder.add_relation(
                "Disease",
                disease_name,
                "Complication",
                term,
                "HAS_COMPLICATION",
                doc_id,
                evidence,
                0.84,
                heading,
            )

    if heading_matches(heading, DEPARTMENT_HEADINGS):
        for term, evidence in extract_departments(text):
            builder.add_relation(
                "Disease",
                disease_name,
                "Department",
                term,
                "BELONGS_TO_DEPARTMENT",
                doc_id,
                evidence,
                0.86,
                heading,
            )

    for sentence in split_sentences(text):
        if any(marker in sentence for marker in CONTRAINDICATION_MARKERS):
            populations = extract_populations(sentence)
            if not populations:
                continue

            drugs = extract_drugs(sentence)
            treatments = extract_treatments(sentence)
            for population, evidence in populations:
                if drugs:
                    for drug, _ in drugs:
                        builder.add_relation(
                            "Drug",
                            drug,
                            "Population",
                            population,
                            "CONTRAINDICATED_FOR",
                            doc_id,
                            evidence,
                            0.78,
                            heading,
                        )
                elif treatments:
                    for treatment, _ in treatments:
                        builder.add_relation(
                            "Treatment",
                            treatment,
                            "Population",
                            population,
                            "CONTRAINDICATED_FOR",
                            doc_id,
                            evidence,
                            0.72,
                            heading,
                        )
                else:
                    builder.add_relation(
                        "Disease",
                        disease_name,
                        "Population",
                        population,
                        "CONTRAINDICATED_FOR",
                        doc_id,
                        evidence,
                        0.65,
                        heading,
                    )

        if any(marker in sentence for marker in INTERACTION_MARKERS):
            drugs = [drug for drug, _ in extract_drugs(sentence)]
            drugs = sorted(dict.fromkeys(drugs))
            if len(drugs) >= 2:
                for index, start_drug in enumerate(drugs):
                    for end_drug in drugs[index + 1 :]:
                        builder.add_relation(
                            "Drug",
                            start_drug,
                            "Drug",
                            end_drug,
                            "INTERACTS_WITH",
                            doc_id,
                            sentence,
                            0.72,
                            heading,
                        )


def iter_cleaned_documents(collection, keyword: str | None, limit: int) -> Iterable[dict[str, Any]]:
    query: dict[str, Any] = {"status": "cleaned", "doc_id": {"$exists": True, "$ne": None}}
    if keyword:
        query["$or"] = [{"keyword": keyword}, {"title": keyword}]

    cursor = collection.find(query).sort([("keyword", 1), ("updated_at", -1)])
    if limit > 0:
        cursor = cursor.limit(limit)
    return cursor


def process_document(builder: GraphCsvBuilder, document: dict[str, Any]) -> None:
    doc_id = source_doc_id(document)
    disease_name = normalize_name(document.get("title")) or normalize_name(document.get("keyword"))
    if not disease_name:
        return

    builder.add_node("Disease", disease_name, doc_id, description=clean_text(document.get("summary")))
    process_basic_info(builder, document, disease_name, doc_id)

    sections = document.get("sections")
    if not isinstance(sections, list):
        return

    for section in sections:
        if not isinstance(section, dict):
            continue
        process_section(
            builder,
            disease_name=disease_name,
            doc_id=doc_id,
            heading=clean_text(section.get("heading")),
            content=clean_text(section.get("content")),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract NLP graph CSV files from cleaned MongoDB documents.")
    parser.add_argument("--keyword", help="Only process one disease keyword/title.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum cleaned documents to process.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="CSV output directory.")
    args = parser.parse_args()

    config = load_config()
    mongodb = config["mongodb"]
    cleaning = config["cleaning"]

    client = MongoClient(mongodb["uri"], serverSelectionTimeoutMS=5000)
    collection = client[mongodb["database"]][cleaning["target_collection"]]

    builder = GraphCsvBuilder()
    scanned = 0
    try:
        for document in iter_cleaned_documents(collection, args.keyword, args.limit):
            scanned += 1
            process_document(builder, document)
    finally:
        client.close()

    builder.write_csv(args.output_dir)
    print(
        "nlp csv generated: "
        f"documents={scanned}, "
        f"nodes={len(builder.nodes)}, "
        f"relationships={len(builder.relationships)}, "
        f"output={args.output_dir}"
    )


if __name__ == "__main__":
    main()
