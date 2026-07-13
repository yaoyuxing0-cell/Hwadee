"""
医疗知识图谱 — Flask 后端服务
提供 6 个 RESTful API 接口
"""
from flask import Flask, request, jsonify
from flask_cors import CORS
from kg_service import KGService
from user_service import UserService
from config import NEO4J_URL, NEO4J_USER, NEO4J_PASSWORD

app = Flask(__name__)
CORS(app)
app.config['JSON_AS_ASCII'] = False


# 强制所有响应使用 UTF-8，解决中文 latin-1 编码报错
@app.after_request
def force_utf8(response):
    response.headers['Content-Type'] = 'application/json; charset=utf-8'
    return response

kg = KGService(NEO4J_URL, NEO4J_USER, NEO4J_PASSWORD)
user_svc = UserService()


def ok(data):
    return jsonify({"code": 200, "message": "success", "data": data})


def fail(msg, code=400):
    return jsonify({"code": code, "message": msg, "data": None})


# ================================================================
# 接口 1：搜索联想
# ================================================================
@app.route('/api/v1/search/suggest')
def search_suggest():
    keyword = request.args.get('keyword', '').strip()
    if not keyword:
        return fail("keyword is required")

    try:
        names = kg.search_suggest(keyword)
        return ok(names)
    except Exception as e:
        return fail(str(e), 500)


# ================================================================
# 接口 2：图谱核心数据
# ================================================================
@app.route('/api/v1/graph/data')
def graph_data():
    entity_name = request.args.get('entityName', '').strip()
    if not entity_name:
        return fail("entityName is required")

    depth = request.args.get('depth', 1, type=int)

    try:
        data = kg.get_graph_data(entity_name, depth)
        if data is None:
            return fail(f"entity '{entity_name}' not found", 404)
        return ok(data)
    except Exception as e:
        return fail(str(e), 500)


# ================================================================
# 接口 3：实体详情
# ================================================================
@app.route('/api/v1/entity/detail')
def entity_detail():
    name = request.args.get('name', '').strip()
    if not name:
        return fail("name is required")

    try:
        data = kg.get_entity_detail(name)
        if data is None:
            return fail(f"entity '{name}' not found", 404)
        return ok(data)
    except Exception as e:
        return fail(str(e), 500)


# ================================================================
# 接口 4：用户注册
# ================================================================
@app.route('/api/v1/user/register', methods=['POST'])
def user_register():
    data = request.get_json(silent=True)
    if not data:
        return fail("request body must be JSON")

    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    role = (data.get('role') or '').strip()
    preferences = data.get('preferences') or []

    if not username or not password or not role:
        return fail("username, password, role are required")
    if not isinstance(preferences, list):
        return fail("preferences must be an array")

    try:
        result = user_svc.register(username, password, role, preferences)
        if result["success"]:
            return ok(None)
        else:
            return fail(result["message"], 409)
    except Exception as e:
        return fail(str(e), 500)


# ================================================================
# 接口 5：用户登录
# ================================================================
@app.route('/api/v1/user/login', methods=['POST'])
def user_login():
    data = request.get_json(silent=True)
    if not data:
        return fail("request body must be JSON")

    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()

    if not username or not password:
        return fail("username and password are required")

    try:
        user = user_svc.login(username, password)
        if user is None:
            return fail("用户名或密码错误", 401)
        return ok(user)
    except Exception as e:
        return fail(str(e), 500)


# ================================================================
# 接口 6：智能推荐
# ================================================================
@app.route('/api/v1/recommend/user-feed')
def user_feed():
    username = request.args.get('username', '').strip()
    if not username:
        return fail("username is required")

    try:
        articles = user_svc.get_user_feed(username)
        return ok(articles)
    except Exception as e:
        return fail(str(e), 500)


# ================================================================
# 启动
# ================================================================
if __name__ == '__main__':
    print("=" * 50)
    print("医疗知识图谱 API 服务")
    print(f"Neo4j: {NEO4J_URL}")
    print("接口:")
    print("  GET  /api/v1/search/suggest?keyword=xxx")
    print("  GET  /api/v1/graph/data?entityName=xxx&depth=1")
    print("  GET  /api/v1/entity/detail?name=xxx")
    print("  POST /api/v1/user/register")
    print("  POST /api/v1/user/login")
    print("  GET  /api/v1/recommend/user-feed?username=xxx")
    print("=" * 50)
    app.run(host='0.0.0.0', port=8080, debug=True)
