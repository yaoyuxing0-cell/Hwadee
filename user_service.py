"""
用户系统服务层
封装 MySQL 查询：注册、登录、偏好推荐
"""
import pymysql
from config import MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE


class UserService:
    def __init__(self):
        self.config = {
            "host": MYSQL_HOST,
            "port": MYSQL_PORT,
            "user": MYSQL_USER,
            "password": MYSQL_PASSWORD,
            "database": MYSQL_DATABASE,
            "charset": "utf8mb4",
        }

    def _get_conn(self):
        return pymysql.connect(**self.config)

    def _ensure_tables(self):
        """建表（幂等，不存在才建）"""
        sql_user = """
            CREATE TABLE IF NOT EXISTS user (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(64) NOT NULL UNIQUE,
                password VARCHAR(128) NOT NULL,
                role VARCHAR(32) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
        sql_pref = """
            CREATE TABLE IF NOT EXISTS user_preference (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(64) NOT NULL,
                preference VARCHAR(64) NOT NULL,
                INDEX idx_username (username)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql_user)
                cur.execute(sql_pref)
            conn.commit()
        finally:
            conn.close()

    # ================================================================
    # 接口 1：注册  POST /api/v1/user/register
    # ================================================================
    def register(self, username: str, password: str, role: str,
                 preferences: list[str]) -> dict:
        """返回 {"success": True/False, "message": "..."}"""
        self._ensure_tables()

        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                # 检查用户名是否已存在
                cur.execute("SELECT id FROM user WHERE username = %s", (username,))
                if cur.fetchone():
                    return {"success": False, "message": "用户名已存在"}

                # 插入用户
                cur.execute(
                    "INSERT INTO user (username, password, role) VALUES (%s, %s, %s)",
                    (username, password, role)
                )
                # 插入偏好（最多 3 个）
                for pref in preferences[:3]:
                    cur.execute(
                        "INSERT INTO user_preference (username, preference) VALUES (%s, %s)",
                        (username, pref)
                    )
            conn.commit()
            return {"success": True, "message": "注册成功"}
        except pymysql.err.IntegrityError:
            return {"success": False, "message": "用户名已存在"}
        finally:
            conn.close()

    # ================================================================
    # 接口 2：登录  POST /api/v1/user/login
    # ================================================================
    def login(self, username: str, password: str) -> dict | None:
        """登录成功返回用户信息，失败返回 None"""
        self._ensure_tables()

        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT username, role FROM user WHERE username = %s AND password = %s",
                    (username, password)
                )
                row = cur.fetchone()
                if not row:
                    return None

                cur.execute(
                    "SELECT preference FROM user_preference WHERE username = %s",
                    (username,)
                )
                preferences = [r[0] for r in cur.fetchall()]

            return {
                "username": row[0],
                "role": row[1],
                "preferences": preferences,
            }
        finally:
            conn.close()

    # ================================================================
    # 接口 3：智能推荐  GET /api/v1/recommend/user-feed?username=xxx
    # ================================================================
    def get_user_feed(self, username: str) -> list[dict]:
        """基于用户偏好返回推荐文章"""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT preference FROM user_preference WHERE username = %s",
                    (username,)
                )
                preferences = [r[0] for r in cur.fetchall()]

        except Exception:
            preferences = []
        finally:
            conn.close()

        # 根据偏好生成推荐（无偏好则返回空）
        articles = []
        for i, pref in enumerate(preferences):
            articles.append({
                "id": 500 + i + 1,
                "title": f"【基于您的静态偏好推荐】2026最新{pref}领域前沿研究成果综述",
                "source": "《Nature Medicine》",
                "url": "https://www.nature.com",
            })
        return articles
