from neo4j import GraphDatabase
from neo4j_config import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER

# ===== 连接配置 =====
uri = NEO4J_URI
username = NEO4J_USER
password = NEO4J_PASSWORD  # 替换成你的密码

driver = GraphDatabase.driver(uri, auth=(username, password))


# ===== 1. 获取所有节点 =====
def get_nodes():
    with driver.session() as session:
        result = session.run("""
            MATCH (n)
            RETURN n.node_id AS node_id, n.name AS name, labels(n)[0] AS label
        """)

        nodes = {
            "Disease": {},
            "Symptom": {},
            "Drug": {},
            "Examination": {},
            "Treatment": {}
        }

        for record in result:
            label = record["label"]
            if label in nodes:
                nodes[label][record["node_id"]] = record["name"]

        return nodes


# ===== 2. 获取所有关系（含关系类型属性） =====
def get_relationships():
    with driver.session() as session:
        result = session.run("""
            MATCH (source)-[r]->(target)
            RETURN source.node_id AS source_id, 
                   target.node_id AS target_id, 
                   r.type AS rel_type
        """)

        rels = []
        for record in result:
            rels.append({
                "source_id": record["source_id"],
                "target_id": record["target_id"],
                "rel_type": record["rel_type"]
            })
        return rels


# ===== 3. 查询函数（使用 r.type 属性） =====

# 查询疾病的症状
def get_symptoms(disease_name):
    with driver.session() as session:
        result = session.run("""
            MATCH (d:Disease {name: $name})-[r]->(s:Symptom)
            WHERE r.type = 'HAS_SYMPTOM'
            RETURN s.name AS symptom
        """, name=disease_name)
        return [record["symptom"] for record in result]


# 查询疾病需要的检查
def get_exams(disease_name):
    with driver.session() as session:
        result = session.run("""
            MATCH (d:Disease {name: $name})-[r]->(e:Examination)
            WHERE r.type = 'REQUIRES_EXAM'
            RETURN e.name AS exam
        """, name=disease_name)
        return [record["exam"] for record in result]


# 查询疾病采用的治疗方案
def get_treatments(disease_name):
    with driver.session() as session:
        result = session.run("""
            MATCH (d:Disease {name: $name})-[r]->(t:Treatment)
            WHERE r.type = 'TREATED_BY'
            RETURN t.name AS treatment
        """, name=disease_name)
        return [record["treatment"] for record in result]


# 查询药物治疗哪些疾病
def get_diseases_by_drug(drug_name):
    with driver.session() as session:
        result = session.run("""
            MATCH (drug:Drug {name: $name})-[r]->(d:Disease)
            WHERE r.type = 'TREATS_DISEASE'
            RETURN d.name AS disease
        """, name=drug_name)
        return [record["disease"] for record in result]


# ===== 4. 综合查询：疾病的所有信息 =====
def get_disease_full_info(disease_name):
    with driver.session() as session:
        result = session.run("""
            MATCH (d:Disease {name: $name})
            OPTIONAL MATCH (d)-[r1]->(s:Symptom) WHERE r1.type = 'HAS_SYMPTOM'
            OPTIONAL MATCH (d)-[r2]->(e:Examination) WHERE r2.type = 'REQUIRES_EXAM'
            OPTIONAL MATCH (d)-[r3]->(t:Treatment) WHERE r3.type = 'TREATED_BY'
            OPTIONAL MATCH (drug:Drug)-[r4]->(d) WHERE r4.type = 'TREATS_DISEASE'
            RETURN d.name AS disease,
                   collect(DISTINCT s.name) AS symptoms,
                   collect(DISTINCT e.name) AS examinations,
                   collect(DISTINCT t.name) AS treatments,
                   collect(DISTINCT drug.name) AS drugs
        """, name=disease_name)
        record = result.single()
        return {
            "disease": record["disease"],
            "symptoms": record["symptoms"],
            "examinations": record["examinations"],
            "treatments": record["treatments"],
            "drugs": record["drugs"]
        }


# ===== 5. 主程序 =====
if __name__ == "__main__":
    nodes = get_nodes()
    rels = get_relationships()

    print("=" * 50)
    print("节点统计")
    print("=" * 50)
    for label, dict_obj in nodes.items():
        print(f"{label}: {len(dict_obj)} 个")

    print("\n" + "=" * 50)
    print("关系统计（按 r.type 属性）")
    print("=" * 50)
    rel_counts = {}
    for r in rels:
        rel_counts[r["rel_type"]] = rel_counts.get(r["rel_type"], 0) + 1
    for rel_type, count in rel_counts.items():
        print(f"{rel_type}: {count} 条")

    # 示例查询：高血压
    print("\n" + "=" * 50)
    print("示例查询：高血压")
    print("=" * 50)

    info = get_disease_full_info("高血压")
    print(f"疾病: {info['disease']}")
    print(f"症状: {', '.join(info['symptoms']) if info['symptoms'] else '无'}")
    print(f"检查: {', '.join(info['examinations']) if info['examinations'] else '无'}")
    print(f"治疗方案: {', '.join(info['treatments']) if info['treatments'] else '无'}")
    print(f"治疗药物: {', '.join(info['drugs']) if info['drugs'] else '无'}")

driver.close()
