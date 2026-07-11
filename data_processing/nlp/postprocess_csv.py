"""
Post-process NLP graph CSV files before importing them into Neo4j.

The extractor is intentionally recall-oriented, so it may produce generic terms
or sentence fragments such as "一些新的药" and "CT检查是...". This script keeps
the same Neo4j CSV schema while applying precision-oriented validation:

1. Normalize entity names.
2. Keep only the five agreed node labels and relationship types.
3. Filter generic, overly long, sentence-like, or label-mismatched entities.
4. Check relationship directions and confidence.
5. Write rejected rows to a review file.
"""

from __future__ import annotations

import argparse
import csv
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from debug_config import (
    DEFAULT_DEBUG_PARAMS_PATH,
    config_float,
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
    RELATION_SCHEMA,
    stable_node_id,
)


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_DIR = BASE_DIR / "output"
DEFAULT_OUTPUT_DIR = BASE_DIR / "output_cleaned"

COMMON_GENERIC_TERMS = {
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
    "药品",
    "手术",
    "诊断",
    "表现",
    "其他",
    "以及",
    "包括",
    "主要",
    "常见",
    "一般",
    "严重",
    "相关",
    "临床",
    "医学",
}

GENERIC_BY_LABEL = {
    "Drug": {
        "药",
        "药物",
        "药品",
        "用药",
        "单药",
        "新药",
        "中医药",
        "中国医药",
        "抗菌药",
        "降压药",
        "降糖药",
        "利尿药",
    },
    "Examination": {
        "检查",
        "检测",
        "检验",
    "诊断",
    "影像学检查",
    "实验室检查",
    "体格检查",
    "辅助检查",
        "该检查",
        "这类检查",
    },
    "Symptom": {"症状", "体征", "临床表现", "表现", "不适"},
    "Treatment": {
        "治疗",
        "其他治疗",
        "合并治疗",
        "前沿治疗",
        "急症治疗",
        "急性期治疗",
        "治疗方法",
        "治疗方式",
        "治疗方案",
    },
}

BAD_FRAGMENTS = (
    "患者",
    "医生",
    "医院",
    "需要",
    "应该",
    "可以",
    "可能",
    "由于",
    "导致",
    "引起",
    "包括",
    "例如",
    "通常",
    "是否",
    "如果",
    "对于",
    "诊断主要",
    "治疗目标",
    "治疗手段",
    "怎么治疗",
    "怎样治疗",
    "常用",
    "建议",
    "指出",
    "启动",
    "改善",
    "早餐",
    "服药",
    "有些",
    "制定",
    "漏服",
    "标志",
    "根据",
    "正式获得",
    "此类",
    "的药",
    "住院",
    "属于",
    "整个",
    "最基本",
    "可重复",
    "继续",
    "考虑",
    "病人",
    "非法",
    "须测定",
    "出现",
    "多数",
    "开始",
    "处理",
    "发病率",
    "危险因素",
    "基础疾病",
    "生活质量",
    "指南推荐",
)

SENTENCE_PUNCT_RE = re.compile(r"[，。；！？]")
MULTISPACE_RE = re.compile(r"\s+")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
HAS_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
LETTER_RE = re.compile(r"[A-Za-z]")
YEAR_RE = re.compile(r"\d+\s*年")
UNBALANCED_BRACKET_RE = re.compile(r"[(（][^()（）]*$")

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
    "结石",
    "溃疡",
    "硬化",
)
DISEASE_ALLOWLIST = {
    "乳腺癌",
    "低钙血症",
    "冠状动脉粥样硬化性心脏病",
    "十二指肠溃疡",
    "心力衰竭",
    "心律失常",
    "慢性肾炎",
    "慢性阻塞性肺疾病",
    "抑郁症",
    "支气管哮喘",
    "甲状腺功能亢进症",
    "甲状腺功能减退",
    "白血病",
    "类风湿关节炎",
    "糖尿病",
    "肝硬化",
    "肺炎",
    "肺癌",
    "肺结核",
    "肾结石",
    "胃溃疡",
    "胃炎",
    "胆囊炎",
    "胆结石",
    "胸膜炎",
    "脂肪肝",
    "脑出血",
    "脑梗死",
    "腰椎间盘突出症",
    "贫血",
    "颈椎病",
    "骨质疏松症",
    "高血压",
    "周围神经病变",
}

SYMPTOM_KEYWORDS = {
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
    "昏迷",
    "眩晕",
    "压痛",
    "反跳痛",
    "厌食",
    "多饮",
    "多尿",
    "多食",
    "消瘦",
    "体重下降",
    "视物模糊",
    "意识丧失",
    "发绀",
    "尿频",
    "尿急",
    "尿痛",
    "抽搐",
    "休克",
}
SYMPTOM_SUFFIXES = (
    "痛",
    "疼痛",
    "发热",
    "咳嗽",
    "咳痰",
    "胸闷",
    "心悸",
    "气短",
    "困难",
    "乏力",
    "水肿",
    "麻木",
    "瘙痒",
    "黄疸",
    "出血",
    "昏迷",
    "眩晕",
    "压痛",
    "反跳痛",
    "便秘",
    "腹泻",
    "恶心",
    "呕吐",
    "厌食",
    "发绀",
)
SYMPTOM_PREFIX_RE = re.compile(
    r"^(?:是以|常常伴发|并伴有|伴有|伴随|伴|可出现|出现|表现为|主要表现为|严重时可出现|严重的|严重|明显|轻度|中度|重度|中重度|持续性|阵发性|反复|发作性的|发作的|亦可|为|即|呈|但)"
)

DRUG_ALLOWLIST = {
    "ACEI",
    "ARB",
    "DPP-4抑制剂",
    "GLP-1受体激动剂",
    "SGLT-2抑制剂",
    "PD-1抑制剂",
    "PD-L1抑制剂",
    "β受体阻滞剂",
    "β受体拮抗剂",
    "阿司匹林",
    "胰岛素",
    "二甲双胍",
    "硝酸甘油",
    "硝酸酯类",
    "他汀",
    "他汀类药物",
    "抗血小板药",
    "抗凝药",
    "抗生素",
    "头孢",
    "青霉素",
    "异烟肼",
    "利福平",
    "乙胺丁醇",
    "吡嗪酰胺",
    "布洛芬",
    "对乙酰氨基酚",
    "奥美拉唑",
    "硝苯地平",
    "美托洛尔",
    "中草药",
    "中药制剂",
}
DRUG_MARKERS = (
    "药",
    "药物",
    "剂",
    "素",
    "胺",
    "酯",
    "苷",
    "霉素",
    "沙星",
    "头孢",
    "青霉素",
    "胰岛素",
    "双胍",
    "地平",
    "洛尔",
    "普利",
    "沙坦",
    "他汀",
    "拉唑",
    "西林",
    "抑制剂",
    "阻滞剂",
    "拮抗剂",
    "激动剂",
)
BAD_DRUG_FRAGMENTS = (
    "一些",
    "一种",
    "两种",
    "多种",
    "新的",
    "这些",
    "不当",
    "不要",
    "停止",
    "随意",
    "交叉耐药",
    "危险因素",
    "严格控制",
    "推荐",
    "与机体",
    "与胰岛素",
    "和皮质激素",
    "要应用",
    "要进行",
    "还应",
    "进行药",
    "谨慎",
    "用药",
    "足量",
    "加强",
    "对耐多药",
    "耐多药",
    "可给予",
    "继续用药",
    "使用药",
    "致病因素",
    "因素",
    "元素",
    "口服铁剂",
    "口服",
    "选用",
    "抵消",
    "作为药",
    "代表性",
    "优先",
    "停用",
    "其中",
    "具体",
    "减少",
    "分别",
    "切忌",
    "自行",
    "考虑",
    "全身应用",
    "单凭",
    "只适用于",
    "合理规范",
    "同时",
    "联合用药",
    "所需",
    "将麻醉",
    "尚无",
    "尽早",
    "第二天",
    "积极的",
    "西药",
    "方剂",
    "成药",
    "抑制甲状腺激素",
)
DRUG_PHRASE_RE = re.compile(r"^(?:L时|中度|也|仍|以|但|低剂量|作为|使|可|应|要|还应|对|不|严|则|在|同时|只|单凭|属|尽早|尚无|合适|合理|含量|因|当以)")
DRUG_STRONG_MARKERS = (
    "药物",
    "剂",
    "胺",
    "酯",
    "苷",
    "霉素",
    "沙星",
    "头孢",
    "青霉素",
    "胰岛素",
    "双胍",
    "地平",
    "洛尔",
    "普利",
    "沙坦",
    "他汀",
    "拉唑",
    "西林",
    "抑制剂",
    "阻滞剂",
    "拮抗剂",
    "激动剂",
)

EXAM_ALLOWLIST = {
    "CT",
    "MRI",
    "X线",
    "B超",
    "CCT",
    "ECT",
    "LDCT",
    "PCT",
    "PET-CT",
    "心电图",
    "血常规",
    "尿常规",
    "胃镜",
    "肠镜",
    "冠状动脉造影",
    "冠状动脉CT血管成像",
    "超声心动图",
    "动态心电图",
    "13C或14C尿素呼气试验",
}
EXAM_MARKERS = (
    "检查",
    "检测",
    "检验",
    "测定",
    "试验",
    "造影",
    "心电图",
    "CT",
    "MRI",
    "X线",
    "B超",
    "超声",
    "胃镜",
    "肠镜",
    "血常规",
    "尿常规",
    "PET-CT",
)
BAD_EXAM_FRAGMENTS = (
    "是",
    "比",
    "每",
    "年内",
    "最常见",
    "常用检查",
    "诊断是",
    "检测是",
    "需要进行",
    "通过",
    "采用",
    "有无",
    "之前",
    "技术",
    "传统",
    "不耐受",
    "已将",
    "已能",
    "即",
    "发病",
    "因此",
    "急诊室",
    "治疗后",
    "疗效",
    "无创",
    "无放射性",
    "相关",
    "基层",
    "扫描可",
    "完善",
    "定期",
    "含量的",
    "另外",
)
EXAM_PHRASE_RE = re.compile(r"^(?:与|为|不|中国|临床|人工智能|也有|之前|U指|仅|以|作为|依据|做|全面|其中|其他|具体|典型|在|基于|发病|凡|另外|含量|因此|完善|定期|对|必要|慢性|整个|无|最|能|考虑|属于|该|这类|须)")

TREATMENT_ALLOWLIST = {
    "手术治疗",
    "外科手术",
    "微创手术",
    "开放手术",
    "介入治疗",
    "内科介入治疗",
    "冠状动脉旁路移植术",
    "PCI",
    "CABG",
    "康复训练",
    "康复治疗",
    "饮食控制",
    "运动治疗",
    "生活方式干预",
    "营养支持",
    "医学营养治疗",
    "物理治疗",
    "中医治疗",
    "放疗",
    "化疗",
    "免疫治疗",
    "靶向治疗",
    "分子靶向治疗",
    "透析",
    "氧疗",
    "成分输血治疗",
    "对症治疗",
}
TREATMENT_MARKERS = (
    "治疗",
    "手术",
    "康复",
    "训练",
    "饮食",
    "运动",
    "透析",
    "氧疗",
    "放疗",
    "化疗",
    "介入",
    "营养",
    "物理",
    "移植术",
)
NON_DRUG_TREATMENT_BLOCKLIST = (
    "药物治疗",
    "用药",
    "服药",
    "药品",
    "抗感染治疗",
    "抗血小板",
    "抗Hp",
    "抑酸",
    "激素",
    "铁剂",
    "降压",
    "降糖",
    "调脂",
)


@dataclass
class CleanNode:
    node_id: str
    name: str
    label: str
    source_doc_ids: set[str] = field(default_factory=set)
    description: str = ""


@dataclass
class CleanRelation:
    start_id: str
    end_id: str
    rel_type: str
    source_doc_id: str
    evidence: str
    confidence: float
    section: str


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, fieldnames: Iterable[str], rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(fieldnames))
        writer.writeheader()
        writer.writerows(rows)


def split_doc_ids(value: str) -> set[str]:
    return {item.strip() for item in re.split(r"[;,\s]+", value or "") if item.strip()}


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = CONTROL_RE.sub("", value)
    value = MULTISPACE_RE.sub("", value)
    return value.strip(" \t\r\n\"'“”‘’[]【】()（）:：,，.;。;；!！?？、")


def normalize_name(name: str, label: str) -> str:
    name = normalize_text(name)
    if label == "Symptom":
        changed = True
        while changed:
            new_name = SYMPTOM_PREFIX_RE.sub("", name)
            changed = new_name != name
            name = new_name
        name = name.removesuffix("等症状").removesuffix("症状")
    elif label == "Drug":
        name = re.sub(r"^(?:即|予|服用|使用|应用|给予|注射|吸入|口服|静脉|外用)", "", name)
    elif label == "Examination":
        name = name.removesuffix("等检查").removesuffix("等检测")
    return normalize_text(name)


def has_meaningful_text(name: str) -> bool:
    return bool(HAS_CJK_RE.search(name) or LETTER_RE.search(name))


def contains_bad_fragment(name: str, label: str) -> str | None:
    for fragment in BAD_FRAGMENTS:
        if fragment in name:
            return fragment
    if label == "Drug":
        for fragment in BAD_DRUG_FRAGMENTS:
            if fragment in name:
                return fragment
    if label == "Examination":
        for fragment in BAD_EXAM_FRAGMENTS:
            if fragment in name:
                return fragment
    if label == "Treatment":
        for fragment in NON_DRUG_TREATMENT_BLOCKLIST:
            if fragment in name:
                return fragment
    return None


def validate_common(name: str, label: str) -> str | None:
    if label not in NODE_TYPES:
        return "invalid_node_label"
    if not name:
        return "empty_name"
    if not has_meaningful_text(name):
        return "no_meaningful_text"
    if SENTENCE_PUNCT_RE.search(name):
        return "sentence_like_punctuation"
    if UNBALANCED_BRACKET_RE.search(name):
        return "unbalanced_bracket"
    if name in COMMON_GENERIC_TERMS or name in GENERIC_BY_LABEL.get(label, set()):
        return "generic_term"
    bad = contains_bad_fragment(name, label)
    if bad:
        return f"bad_fragment:{bad}"
    if len(name) < 2 and name not in {"CT"}:
        return "too_short"
    return None


def validate_disease(name: str) -> str | None:
    if len(name) > 24:
        return "disease_name_too_long"
    if any(word in name for word in ("症状", "检查", "治疗", "药物", "患者")):
        return "disease_contains_non_disease_word"
    if name in DISEASE_ALLOWLIST:
        return None
    if name in {"休克", "感染", "贫血"}:
        return None
    if not any(suffix in name for suffix in DISEASE_SUFFIXES):
        return "not_disease_like"
    return None


def validate_symptom(name: str) -> str | None:
    if len(name) > 14:
        return "symptom_name_too_long"
    if name.startswith("无"):
        return "negated_symptom"
    if name in DISEASE_ALLOWLIST:
        return "symptom_is_disease"
    if any(fragment in name for fragment in ("时有", "劳动", "体力", "诊治", "甚至")):
        return "symptom_phrase_fragment"
    if any(word in name for word in ("治疗", "检查", "诊断", "药", "疾病", "患者")):
        return "symptom_contains_wrong_domain_word"
    if any(keyword in name for keyword in SYMPTOM_KEYWORDS):
        return None
    if any(name.endswith(suffix) for suffix in SYMPTOM_SUFFIXES):
        return None
    return "not_symptom_like"


def validate_drug(name: str) -> str | None:
    if len(name) > 20:
        return "drug_name_too_long"
    if name in DRUG_ALLOWLIST:
        return None
    if DRUG_PHRASE_RE.search(name):
        return "drug_phrase_prefix"
    if re.fullmatch(r"[A-Za-z0-9βα+\-/]+", name):
        return "drug_abbreviation_without_class"
    if any(connector in name for connector in ("与", "和", "或", "等", "及")):
        return "compound_or_sentence_fragment"
    if "的" in name:
        return "drug_phrase_with_de"
    if any(marker in name for marker in DRUG_STRONG_MARKERS):
        return None
    if name.endswith("药") and len(name) <= 8:
        return None
    if name.endswith("素") and len(name) <= 8:
        return None
    return "not_drug_like"


def validate_exam(name: str) -> str | None:
    if len(name) > 24:
        return "exam_name_too_long"
    if name in EXAM_ALLOWLIST:
        return None
    if EXAM_PHRASE_RE.search(name):
        return "exam_phrase_prefix"
    if "的" in name:
        return "exam_phrase_with_de"
    if YEAR_RE.search(name):
        return "time_expression"
    if any(connector in name for connector in ("以及", "和", "及", "或", "等")):
        return "compound_exam"
    if any(marker in name for marker in EXAM_MARKERS):
        return None
    return "not_exam_like"


def validate_treatment(name: str) -> str | None:
    if len(name) > 18:
        return "treatment_name_too_long"
    if name in TREATMENT_ALLOWLIST:
        return None
    if any(marker in name for marker in TREATMENT_MARKERS):
        return None
    return "not_treatment_like"


def validate_node(name: str, label: str) -> str | None:
    common_reason = validate_common(name, label)
    if common_reason:
        return common_reason
    validators = {
        "Disease": validate_disease,
        "Symptom": validate_symptom,
        "Drug": validate_drug,
        "Examination": validate_exam,
        "Treatment": validate_treatment,
    }
    return validators[label](name)


def parse_confidence(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def reject_node(row: dict[str, str], reason: str) -> dict[str, object]:
    return {
        "item_type": "node",
        "reason": reason,
        "id": row.get(NODE_ID, ""),
        "name": row.get(NODE_NAME, ""),
        "type": row.get(NODE_LABEL, ""),
        "start_id": "",
        "end_id": "",
        "relationship_type": "",
        "confidence": "",
        "evidence": "",
        "source_doc_id": row.get(NODE_DOCS, ""),
    }


def reject_relation(row: dict[str, str], reason: str) -> dict[str, object]:
    return {
        "item_type": "relationship",
        "reason": reason,
        "id": "",
        "name": "",
        "type": "",
        "start_id": row.get(REL_START, ""),
        "end_id": row.get(REL_END, ""),
        "relationship_type": row.get(REL_TYPE, ""),
        "confidence": row.get(REL_CONF, ""),
        "evidence": row.get(REL_EVIDENCE, ""),
        "source_doc_id": row.get(REL_DOC, ""),
    }


def merge_node(target: CleanNode, row: dict[str, str], description: str) -> None:
    target.source_doc_ids.update(split_doc_ids(row.get(NODE_DOCS, "")))
    if description and (not target.description or len(description) > len(target.description)):
        target.description = description


def clean_nodes(
    rows: list[dict[str, str]],
) -> tuple[dict[str, CleanNode], dict[str, str], list[dict[str, object]]]:
    nodes: dict[str, CleanNode] = {}
    id_map: dict[str, str] = {}
    rejects: list[dict[str, object]] = []

    for row in rows:
        old_id = row.get(NODE_ID, "").strip()
        label = normalize_text(row.get(NODE_LABEL, ""))
        name = normalize_name(row.get(NODE_NAME, ""), label)
        reason = validate_node(name, label)
        if reason:
            rejected = dict(row)
            rejected[NODE_NAME] = name or row.get(NODE_NAME, "")
            rejected[NODE_LABEL] = label
            rejects.append(reject_node(rejected, reason))
            continue

        clean_id = stable_node_id(label, name)
        id_map[old_id] = clean_id
        description = normalize_text(row.get(NODE_DESC, ""))
        if clean_id not in nodes:
            nodes[clean_id] = CleanNode(
                node_id=clean_id,
                name=name,
                label=label,
                source_doc_ids=split_doc_ids(row.get(NODE_DOCS, "")),
                description=description,
            )
        else:
            merge_node(nodes[clean_id], row, description)

    return nodes, id_map, rejects


def clean_relations(
    rows: list[dict[str, str]],
    nodes: dict[str, CleanNode],
    id_map: dict[str, str],
    min_confidence: float,
) -> tuple[dict[tuple[str, str, str, str], CleanRelation], list[dict[str, object]]]:
    relations: dict[tuple[str, str, str, str], CleanRelation] = {}
    rejects: list[dict[str, object]] = []

    for row in rows:
        rel_type = normalize_text(row.get(REL_TYPE, ""))
        if rel_type not in RELATION_SCHEMA:
            rejects.append(reject_relation(row, "invalid_relationship_type"))
            continue

        confidence = parse_confidence(row.get(REL_CONF, ""))
        if confidence is None:
            rejects.append(reject_relation(row, "invalid_confidence"))
            continue
        if confidence < min_confidence:
            rejects.append(reject_relation(row, "low_confidence"))
            continue

        old_start = row.get(REL_START, "").strip()
        old_end = row.get(REL_END, "").strip()
        start_id = id_map.get(old_start)
        end_id = id_map.get(old_end)
        if not start_id or not end_id:
            rejects.append(reject_relation(row, "endpoint_node_rejected_or_missing"))
            continue

        expected_start, expected_end = RELATION_SCHEMA[rel_type]
        start_label = nodes[start_id].label
        end_label = nodes[end_id].label
        if (start_label, end_label) != (expected_start, expected_end):
            rejects.append(
                reject_relation(
                    row,
                    f"direction_or_label_mismatch:{start_label}->{end_label}",
                )
            )
            continue

        evidence = normalize_text(row.get(REL_EVIDENCE, ""))
        if len(evidence) < 4:
            rejects.append(reject_relation(row, "evidence_too_short"))
            continue

        source_doc_id = normalize_text(row.get(REL_DOC, ""))
        section = normalize_text(row.get(REL_SECTION, ""))
        key = (start_id, end_id, rel_type, source_doc_id)
        relation = CleanRelation(
            start_id=start_id,
            end_id=end_id,
            rel_type=rel_type,
            source_doc_id=source_doc_id,
            evidence=evidence,
            confidence=confidence,
            section=section,
        )
        if key not in relations or confidence > relations[key].confidence:
            relations[key] = relation

    return relations, rejects


def prune_unreferenced_nodes(
    nodes: dict[str, CleanNode],
    relations: dict[tuple[str, str, str, str], CleanRelation],
) -> tuple[dict[str, CleanNode], list[dict[str, object]]]:
    referenced = {
        relation.start_id
        for relation in relations.values()
    } | {
        relation.end_id
        for relation in relations.values()
    }
    kept: dict[str, CleanNode] = {}
    rejects: list[dict[str, object]] = []
    for node_id, node in nodes.items():
        if node.label == "Disease" or node_id in referenced:
            kept[node_id] = node
            continue
        rejects.append(
            {
                "item_type": "node",
                "reason": "unreferenced_after_relationship_filter",
                "id": node.node_id,
                "name": node.name,
                "type": node.label,
                "start_id": "",
                "end_id": "",
                "relationship_type": "",
                "confidence": "",
                "evidence": "",
                "source_doc_id": ";".join(sorted(node.source_doc_ids)),
            }
        )
    return kept, rejects


def node_to_row(node: CleanNode) -> dict[str, object]:
    return {
        NODE_ID: node.node_id,
        NODE_NAME: node.name,
        NODE_LABEL: node.label,
        NODE_DOCS: ";".join(sorted(node.source_doc_ids)),
        NODE_DESC: node.description,
    }


def relation_to_row(relation: CleanRelation) -> dict[str, object]:
    return {
        REL_START: relation.start_id,
        REL_END: relation.end_id,
        REL_TYPE: relation.rel_type,
        REL_DOC: relation.source_doc_id,
        REL_EVIDENCE: relation.evidence,
        REL_CONF: f"{relation.confidence:.2f}",
        REL_SECTION: relation.section,
    }


def build_summary(
    input_nodes: int,
    input_relations: int,
    kept_nodes: int,
    kept_relations: int,
    rejects: list[dict[str, object]],
) -> list[dict[str, object]]:
    node_rejects = sum(1 for item in rejects if item.get("item_type") == "node")
    relation_rejects = sum(1 for item in rejects if item.get("item_type") == "relationship")
    return [
        {"metric": "input_nodes", "value": input_nodes},
        {"metric": "kept_nodes", "value": kept_nodes},
        {"metric": "rejected_nodes", "value": node_rejects},
        {"metric": "input_relationships", "value": input_relations},
        {"metric": "kept_relationships", "value": kept_relations},
        {"metric": "rejected_relationships", "value": relation_rejects},
        {"metric": "total_rejected_rows", "value": len(rejects)},
    ]


def postprocess(input_dir: Path, output_dir: Path, min_confidence: float) -> None:
    node_rows = read_csv(input_dir / "nodes.csv")
    relation_rows = read_csv(input_dir / "relationships.csv")

    nodes, id_map, rejects = clean_nodes(node_rows)
    relations, relation_rejects = clean_relations(relation_rows, nodes, id_map, min_confidence)
    rejects.extend(relation_rejects)
    nodes, prune_rejects = prune_unreferenced_nodes(nodes, relations)
    rejects.extend(prune_rejects)

    node_rows_out = [node_to_row(node) for node in sorted(nodes.values(), key=lambda item: (item.label, item.name))]
    relation_rows_out = [
        relation_to_row(relation)
        for relation in sorted(
            relations.values(),
            key=lambda item: (item.rel_type, item.start_id, item.end_id, item.source_doc_id),
        )
        if relation.start_id in nodes and relation.end_id in nodes
    ]
    summary_rows = build_summary(
        input_nodes=len(node_rows),
        input_relations=len(relation_rows),
        kept_nodes=len(node_rows_out),
        kept_relations=len(relation_rows_out),
        rejects=rejects,
    )

    write_csv(output_dir / "nodes.csv", NODE_FIELDS, node_rows_out)
    write_csv(
        output_dir / "relationships.csv",
        RELATION_FIELDS,
        relation_rows_out,
    )
    write_csv(
        output_dir / "rejects.csv",
        [
            "item_type",
            "reason",
            "id",
            "name",
            "type",
            "start_id",
            "end_id",
            "relationship_type",
            "confidence",
            "evidence",
            "source_doc_id",
        ],
        rejects,
    )
    write_csv(output_dir / "summary.csv", ["metric", "value"], summary_rows)

    print(f"postprocess complete: {output_dir}")
    for row in summary_rows:
        print(f"{row['metric']}: {row['value']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean NLP Neo4j CSV output.")
    parser.add_argument("--params", type=Path, default=DEFAULT_DEBUG_PARAMS_PATH, help="JSON debug parameter file.")
    parser.add_argument("--input-dir", type=Path, help="Directory containing nodes.csv and relationships.csv.")
    parser.add_argument("--output-dir", type=Path, help="Directory for cleaned CSV files.")
    parser.add_argument("--min-confidence", type=float, help="Minimum relationship confidence to keep.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    debug_params = load_debug_params(args.params)
    input_dir = args.input_dir or config_path(debug_params.get("raw_output_dir"), DEFAULT_INPUT_DIR)
    output_dir = args.output_dir or config_path(debug_params.get("cleaned_output_dir"), DEFAULT_OUTPUT_DIR)
    min_confidence = (
        args.min_confidence
        if args.min_confidence is not None
        else config_float(debug_params.get("min_confidence"), 0.85)
    )
    postprocess(input_dir, output_dir, min_confidence)


if __name__ == "__main__":
    main()
