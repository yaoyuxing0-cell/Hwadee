"""Local Neo4j connection config shared by graph scripts."""

from __future__ import annotations

import json
import os
from pathlib import Path


CONFIG_PATH = Path(__file__).with_name("graph_params.json")
LOCAL_CONFIG_PATH = Path(__file__).with_name("graph_params.local.json")

DEFAULT_GRAPH_CONFIG = {
    "uri": "bolt://localhost:7687",
    "user": "neo4j",
    "password": "",
    "database": "",
    "csv_dir": r"..\nlp\output_cleaned",
    "clear": False,
    "check": False,
    "batch_size": 500,
}


def load_graph_config() -> dict:
    config = DEFAULT_GRAPH_CONFIG.copy()
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as file:
            config.update(json.load(file))
    if LOCAL_CONFIG_PATH.exists():
        with LOCAL_CONFIG_PATH.open("r", encoding="utf-8") as file:
            config.update(json.load(file))

    config["uri"] = os.getenv("NEO4J_URI", config["uri"])
    config["user"] = os.getenv("NEO4J_USER", config["user"])
    config["password"] = os.getenv("NEO4J_PASSWORD", config["password"])
    config["database"] = os.getenv("NEO4J_DATABASE", config["database"])
    return config


GRAPH_CONFIG = load_graph_config()

NEO4J_URI = GRAPH_CONFIG["uri"]
NEO4J_USER = GRAPH_CONFIG["user"]
NEO4J_PASSWORD = GRAPH_CONFIG["password"]
NEO4J_DATABASE = GRAPH_CONFIG["database"]
