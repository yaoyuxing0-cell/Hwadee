"""
知识图谱查询服务层
封装所有 Neo4j Cypher 查询，对外暴露纯数据接口
"""
from neo4j import GraphDatabase


class KGService:
    def __init__(self, url: str, user: str, password: str):
        self.driver = GraphDatabase.driver(url, auth=(user, password))

    def close(self):
        self.driver.close()

    # ================================================================
    # 接口 1：搜索联想  /api/v1/search/suggest?keyword=xxx
    # ================================================================
    def search_suggest(self, keyword: str, limit: int = 20) -> list[str]:
        """关键词模糊搜索实体名称列表"""
        cypher = """
            MATCH (n:Entity)
            WHERE n.name CONTAINS $keyword
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
    def get_graph_data(self, entity_name: str, depth: int = 1) -> dict | None:
        """
        获取以某实体为中心的子图
        返回 {nodes: [...], links: [...]}，节点不存在时返回 None
        """
        # 第一步：验证实体是否存在
        exists_cypher = "MATCH (n:Entity {name: $name}) RETURN count(n) > 0 AS ok"
        with self.driver.session() as session:
            if not session.run(exists_cypher, name=entity_name).single()["ok"]:
                return None

        # 第二步：查中心节点 + 一度邻居
        cypher = """
            MATCH (center:Entity {name: $name})
            OPTIONAL MATCH (center)-[r]-(neighbor:Entity)
            RETURN center.name AS center_name,
                   center.category AS center_category,
                   type(r) AS relation,
                   neighbor.name AS neighbor_name,
                   neighbor.category AS neighbor_category
            LIMIT 200
        """
        with self.driver.session() as session:
            result = session.run(cypher, name=entity_name)

            nodes_map = {}   # name -> {name, category, degree}
            links = []

            for record in result:
                c_name = record["center_name"]
                c_cat = record.get("center_category") or 0
                n_name = record["neighbor_name"]
                n_cat = record.get("neighbor_category") or 0
                rel = record["relation"]

                # 中心节点（可能出现多次，每次用最新 category 覆盖）
                nodes_map[c_name] = {"name": c_name, "category": c_cat, "degree": 0}

                if n_name is not None:
                    nodes_map[n_name] = {"name": n_name, "category": n_cat, "degree": 0}
                    links.append({
                        "source": c_name,
                        "target": n_name,
                        "label": {
                            "show": True,
                            "formatter": rel or ""
                        }
                    })

            # 第三步：计算度数 → symbolSize
            for link in links:
                if link["source"] in nodes_map:
                    nodes_map[link["source"]]["degree"] += 1
                if link["target"] in nodes_map:
                    nodes_map[link["target"]]["degree"] += 1

            degrees = [v["degree"] for v in nodes_map.values() if v["degree"] > 0]
            max_d = max(degrees) if degrees else 1
            min_d = min(degrees) if degrees else 1

            nodes = []
            for info in nodes_map.values():
                d = info["degree"]
                if max_d == min_d:
                    size = 40
                else:
                    size = round(20 + (d - min_d) / (max_d - min_d) * 40)
                nodes.append({
                    "name": info["name"],
                    "category": info["category"],
                    "symbolSize": size
                })

        return {"nodes": nodes, "links": links}

    # ================================================================
    # 接口 3：实体详情  /api/v1/entity/detail?name=xxx
    # ================================================================
    def get_entity_detail(self, name: str) -> dict | None:
        """
        获取实体详情
        返回格式：
        {
            "name": "高血压",
            "category": "Disease (疾病)",
            "definition": "百科定义...",
            "indications": "适应症/诊疗范围...",
            "badReactions": "不良反应/注意事项..."
        }
        """
        cypher_check = "MATCH (n:Entity {name: $name}) RETURN n LIMIT 1"
        cypher_attrs = """
            MATCH (n:Entity {name: $name})
            RETURN n.name AS name,
                   n.category AS category,
                   n.简介 AS definition
        """

        with self.driver.session() as session:
            # 存在性检查
            if not session.run(cypher_check, name=name).single():
                return None

            # 节点属性
            attr = session.run(cypher_attrs, name=name).single()
            detail = {
                "name": attr["name"],
                "category": attr.get("category") or "",
                "definition": attr.get("definition") or "",
            }

            # indications / badReactions：优先从节点属性取，没有则从关系聚合
            detail["indications"] = self._get_aggregated_relation(
                name, ["适应症", "主治", "治疗范围", "应用", "作用", "功能"]
            )
            detail["badReactions"] = self._get_aggregated_relation(
                name, ["不良反应", "副作用", "禁忌", "并发症", "注意事项", "毒副作用"]
            )

        return detail

    def _get_aggregated_relation(self, entity_name: str, relation_types: list[str]) -> str:
        """从指定关系类型聚合值，用分号连接"""
        cypher = """
            MATCH (n:Entity {name: $name})-[r]-(m:Entity)
            WHERE type(r) IN $rel_types
            RETURN m.name AS value
        """
        with self.driver.session() as session:
            records = session.run(cypher, name=entity_name, rel_types=relation_types)
            values = [r["value"] for r in records]
        return "；".join(values) if values else ""

