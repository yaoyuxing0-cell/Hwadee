"""
知识图谱查询服务层
封装所有 Neo4j Cypher 查询，对外暴露纯数据接口
"""
from neo4j import GraphDatabase

from config import CATEGORY_TO_LABEL, LABEL_TO_CATEGORY

# 实际 Neo4j 数据模型中的 5 种节点标签（顺序即 category 编号）
_NODE_LABELS = list(CATEGORY_TO_LABEL.values())   # ['Disease','Symptom','Drug','Examination','Treatment']
_CATEGORY_MAP = LABEL_TO_CATEGORY                  # {'Disease':0, 'Symptom':1, ...}


def _label_filter(v: str) -> str:
    """生成 Cypher WHERE 条件：匹配变量 v 的标签是 5 种之一"""
    return '(' + ' OR '.join(f'{v}:{l}' for l in _NODE_LABELS) + ')'


def _neighbor_filter(var: str, category: int | None = None) -> str:
    """生成邻居节点的 Cypher WHERE 条件。
    category=None → 匹配全部 5 种标签（现有行为）
    category=0-4 → 仅匹配该标签 (如 var:Disease)
    """
    if category is None:
        return _label_filter(var)
    label = _NODE_LABELS[category]
    return f'{var}:{label}'


class KGService:
    def __init__(self, url: str, user: str, password: str):
        self.driver = GraphDatabase.driver(url, auth=(user, password))

    def close(self):
        self.driver.close()

    # ================================================================
    # 接口 1：搜索联想  /api/v1/search/suggest?keyword=xxx
    # ================================================================
    def search_suggest(self, keyword: str, limit: int = 20,
                       category: int | None = None) -> list[str]:
        """关键词模糊搜索实体名称列表。
        category: 可选 0-4，仅搜索该类型实体；None 搜索所有类型。
        """
        cypher = f"""
            MATCH (n)
            WHERE n.name CONTAINS $keyword AND {_neighbor_filter('n', category)}
            RETURN n.name AS name
            ORDER BY n.name
            LIMIT $limit
        """
        with self.driver.session() as session:
            result = session.run(cypher, keyword=keyword, limit=limit)
            return [record["name"] for record in result]

    # ================================================================
    # 接口 2：图谱核心数据  /api/v1/graph/data?entityName=xxx&depth=1
    # ================================================================
    def get_graph_data(self, entity_name: str, depth: int = 1,
                       category: int | None = None) -> dict | None:
        """
        获取以某实体为中心的子图
        返回 {nodes: [...], relationships: [...]}，节点不存在时返回 None
        category: 可选 0-4，仅保留该类型的邻居节点；None 保留所有类型。
                  中心节点始终保留，不受 category 限制。
        Cypher 直接构造目标 JSON 格式，Python 只做度数归一化和透传
        """
        if not self._entity_exists(entity_name):
            return None

        depth = max(1, min(depth, 3))  # 限制 1-3

        with self.driver.session() as session:
            if depth == 1:
                record = session.run(
                    self._graph_cypher_1hop(category), name=entity_name, node_labels=_NODE_LABELS
                ).single()
            else:
                record = session.run(
                    self._graph_cypher_multihop(depth, category), name=entity_name, node_labels=_NODE_LABELS
                ).single()

            if not record:
                return None

            nodes = record["nodes"] or []
            relationships = record["relationships"] or []

        # 后处理：根据度数归一化 node size
        self._normalize_node_sizes(nodes, relationships)

        return {"nodes": nodes, "relationships": relationships}

    # ----------------------------------------------------------------
    # Cypher 模板：1-hop 子图（LIMIT 200 行 → collect 成目标格式）
    # ----------------------------------------------------------------
    @staticmethod
    def _graph_cypher_1hop(category: int | None = None) -> str:
        return f"""
            MATCH (center {{name: $name}})
            WHERE {_label_filter('center')}
            OPTIONAL MATCH (center)-[r]-(neighbor)
            WHERE {_neighbor_filter('neighbor', category)}
            WITH center, r, neighbor
            LIMIT 200
            WITH center,
                 collect(DISTINCT neighbor) AS raw_neighbors,
                 collect(DISTINCT r) AS raw_rels
            WITH [n IN raw_neighbors WHERE n IS NOT NULL] + center AS all_n,
                 [r IN raw_rels WHERE r IS NOT NULL] AS all_r
            UNWIND all_n AS n
            WITH all_r,
                 collect(DISTINCT {{
                     id: n.name,
                     caption: n.name,
                     nodeType: [l IN labels(n) WHERE l IN $node_labels][0],
                     color: CASE [l IN labels(n) WHERE l IN $node_labels][0]
                         WHEN 'Disease' THEN '#D73027'
                         WHEN 'Symptom' THEN '#FDAE61'
                         WHEN 'Drug' THEN '#4575B4'
                         WHEN 'Examination' THEN '#91BFDB'
                         WHEN 'Treatment' THEN '#1A9850'
                         ELSE '#999999' END,
                     size: 40,
                     description: coalesce(n.description, '')
                 }}) AS nodes
            RETURN nodes,
                   [r IN all_r | {{
                       id: 'rel_' + type(r) + '_' + startNode(r).name + '_' + endNode(r).name,
                       from: startNode(r).name,
                       to: endNode(r).name,
                       type: type(r),
                       caption: type(r),
                       color: '#999999'
                   }}] AS relationships
        """

    # ----------------------------------------------------------------
    # Cypher 模板：多跳子图（depth 2-3）
    # ----------------------------------------------------------------
    @staticmethod
    def _graph_cypher_multihop(depth: int, category: int | None = None) -> str:
        return f"""
            MATCH (center {{name: $name}})
            WHERE {_label_filter('center')}
            OPTIONAL MATCH path = (center)-[*1..{depth}]-(neighbor)
            WHERE {_neighbor_filter('neighbor', category)}
            UNWIND relationships(path) AS r
            WITH center, neighbor, r
            LIMIT 300
            WITH center,
                 collect(DISTINCT neighbor) AS raw_neighbors,
                 collect(DISTINCT r) AS raw_rels
            WITH [n IN raw_neighbors WHERE n IS NOT NULL] + center AS all_n,
                 [r IN raw_rels WHERE r IS NOT NULL] AS all_r
            UNWIND all_n AS n
            WITH all_r,
                 collect(DISTINCT {{
                     id: n.name,
                     caption: n.name,
                     nodeType: [l IN labels(n) WHERE l IN $node_labels][0],
                     color: CASE [l IN labels(n) WHERE l IN $node_labels][0]
                         WHEN 'Disease' THEN '#D73027'
                         WHEN 'Symptom' THEN '#FDAE61'
                         WHEN 'Drug' THEN '#4575B4'
                         WHEN 'Examination' THEN '#91BFDB'
                         WHEN 'Treatment' THEN '#1A9850'
                         ELSE '#999999' END,
                     size: 40,
                     description: coalesce(n.description, '')
                 }}) AS nodes
            RETURN nodes,
                   [r IN all_r | {{
                       id: 'rel_' + type(r) + '_' + startNode(r).name + '_' + endNode(r).name,
                       from: startNode(r).name,
                       to: endNode(r).name,
                       type: type(r),
                       caption: type(r),
                       color: '#999999'
                   }}] AS relationships
        """

    # ----------------------------------------------------------------
    # 增量扩展：获取某实体 1-hop 邻居，排除已在图中的节点
    # ----------------------------------------------------------------
    def get_expand_data(self, entity_name: str, exclude_ids: list[str]) -> dict:
        """获取实体 1-hop 邻居（排除已有节点），返回同 get_graph_data 格式"""
        cypher = f"""
            MATCH (center {{name: $name}})
            WHERE {_label_filter('center')}
            OPTIONAL MATCH (center)-[r]-(neighbor)
            WHERE {_label_filter('neighbor')}
              AND NOT neighbor.name IN $exclude
            WITH center, r, neighbor
            LIMIT 200
            WITH center,
                 collect(DISTINCT neighbor) AS raw_neighbors,
                 collect(DISTINCT r) AS raw_rels
            WITH [n IN raw_neighbors WHERE n IS NOT NULL] + center AS all_n,
                 [r IN raw_rels WHERE r IS NOT NULL] AS all_r
            UNWIND all_n AS n
            WITH all_r,
                 collect(DISTINCT {{
                     id: n.name,
                     caption: n.name,
                     nodeType: [l IN labels(n) WHERE l IN $node_labels][0],
                     color: CASE [l IN labels(n) WHERE l IN $node_labels][0]
                         WHEN 'Disease' THEN '#D73027'
                         WHEN 'Symptom' THEN '#FDAE61'
                         WHEN 'Drug' THEN '#4575B4'
                         WHEN 'Examination' THEN '#91BFDB'
                         WHEN 'Treatment' THEN '#1A9850'
                         ELSE '#999999' END,
                     size: 40,
                     description: coalesce(n.description, '')
                 }}) AS nodes
            RETURN nodes,
                   [r IN all_r | {{
                       id: 'rel_' + type(r) + '_' + startNode(r).name + '_' + endNode(r).name,
                       from: startNode(r).name,
                       to: endNode(r).name,
                       type: type(r),
                       caption: type(r),
                       color: '#999999'
                   }}] AS relationships
        """
        with self.driver.session() as session:
            record = session.run(
                cypher, name=entity_name, node_labels=_NODE_LABELS,
                exclude=exclude_ids if exclude_ids else ['']
            ).single()
            if not record:
                return {"nodes": [], "relationships": []}
            nodes = record["nodes"] or []
            relationships = record["relationships"] or []

        self._normalize_node_sizes(nodes, relationships)
        return {"nodes": nodes, "relationships": relationships}

    # ----------------------------------------------------------------
    # 工具方法
    # ----------------------------------------------------------------
    def _entity_exists(self, name: str) -> bool:
        cypher = f"MATCH (n {{name: $name}}) WHERE {_label_filter('n')} RETURN count(n) > 0 AS ok"
        with self.driver.session() as session:
            return session.run(cypher, name=name).single()["ok"]

    @staticmethod
    def _normalize_node_sizes(nodes: list[dict], relationships: list[dict]) -> None:
        """根据度数归一化 node.size 到 20-60 范围"""
        degree = {n["id"]: 0 for n in nodes}
        for rel in relationships:
            if rel["from"] in degree:
                degree[rel["from"]] += 1
            if rel["to"] in degree:
                degree[rel["to"]] += 1

        vals = [v for v in degree.values() if v > 0]
        max_d = max(vals) if vals else 1
        min_d = min(vals) if vals else 1

        for node in nodes:
            d = degree[node["id"]]
            if max_d == min_d:
                node["size"] = 40
            else:
                node["size"] = round(20 + (d - min_d) / (max_d - min_d) * 40)

    # ================================================================
    # 接口 3：实体详情  /api/v1/entity/detail?name=xxx
    # ================================================================
    def get_entity_detail(self, name: str) -> dict | None:
        """
        获取实体详情
        返回格式：
        {
            "name": "高血压",
            "category": 0,      # Disease→0, Symptom→1, Drug→2, Examination→3, Treatment→4
            "definition": "...",
            "indications": "...",
            "badReactions": "..."
        }
        """
        cypher_check = f"MATCH (n {{name: $name}}) WHERE {_label_filter('n')} RETURN n LIMIT 1"
        cypher_attrs = f"""
            MATCH (n {{name: $name}})
            WHERE {_label_filter('n')}
            RETURN n.name AS name,
                   [l IN labels(n) WHERE l IN $node_labels][0] AS category,
                   n.description AS definition
        """

        with self.driver.session() as session:
            # 存在性检查
            if not session.run(cypher_check, name=name).single():
                return None

            # 节点属性
            attr = session.run(cypher_attrs, name=name, node_labels=_NODE_LABELS).single()
            detail = {
                "name": attr["name"],
                "category": _CATEGORY_MAP.get(attr.get("category") or "", 0),
                "definition": attr.get("definition") or "",
            }

            # indications / badReactions：从关系聚合
            detail["indications"] = self._get_aggregated_relation(
                name, ["TREATS", "TREATED_BY"]
            )
            detail["badReactions"] = self._get_aggregated_relation(
                name, ["HAS_SYMPTOM", "HAS_COMPLICATION"]
            )

        return detail

    def _get_aggregated_relation(self, entity_name: str, relation_types: list[str]) -> str:
        """从指定关系类型聚合邻居节点值，用分号连接"""
        cypher = f"""
            MATCH (n {{name: $name}})-[r]-(m)
            WHERE type(r) IN $rel_types
              AND {_label_filter('n')}
              AND {_label_filter('m')}
            RETURN m.name AS value
        """
        with self.driver.session() as session:
            records = session.run(cypher, name=entity_name, rel_types=relation_types)
            values = [r["value"] for r in records]
        return "；".join(values) if values else ""

