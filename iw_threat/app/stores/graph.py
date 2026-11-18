"""Neo4j 지식그래프 — 문서에서 추출한 개체·관계 저장."""
from __future__ import annotations

from pathlib import Path

from neo4j import GraphDatabase

from ..config import get_settings

LABELS = {"Unit", "Equipment", "Location", "Activity"}


class Graph:
    def __init__(self):
        s = get_settings()
        self.driver = GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password))

    def apply_schema(self, path: str = "db/neo4j/constraints.cypher") -> None:
        p = Path(path)
        if not p.exists():
            return
        stmts = [s.strip() for s in p.read_text(encoding="utf-8").split(";")]
        with self.driver.session() as sess:
            for st in stmts:
                lines = [ln for ln in st.splitlines() if not ln.strip().startswith("//")]
                if "\n".join(lines).strip():
                    sess.run("\n".join(lines))

    def save_extraction(self, doc_id: str, chunk_id: str, entities: list[dict], relations: list[dict]) -> None:
        """entities: [{name, label}], relations: [{head, rel, tail}] — 라벨은 허용 목록만."""
        with self.driver.session() as sess:
            sess.run("MERGE (d:Document {id:$doc}) MERGE (c:Chunk {id:$chunk}) MERGE (d)-[:HAS_CHUNK]->(c)",
                     doc=doc_id, chunk=chunk_id)
            for e in entities:
                if e.get("label") not in LABELS or not e.get("name"):
                    continue
                sess.run(f"MERGE (n:{e['label']} {{name:$name}}) WITH n MATCH (c:Chunk {{id:$chunk}}) "
                         "MERGE (c)-[:MENTIONS]->(n)", name=e["name"], chunk=chunk_id)
            for r in relations:
                rel = "".join(ch for ch in str(r.get("rel", "RELATED")).upper() if ch.isalnum() or ch == "_") or "RELATED"
                sess.run(f"MATCH (a {{name:$h}}), (b {{name:$t}}) MERGE (a)-[x:`{rel}`]->(b) "
                         "SET x.chunk_id=$chunk", h=r.get("head"), t=r.get("tail"), chunk=chunk_id)
