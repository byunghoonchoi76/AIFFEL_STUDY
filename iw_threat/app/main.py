"""FastAPI 진입점."""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .analysis.assess import run_assessment
from .stores import pg

app = FastAPI(title="징후·위협도 AI 추론 MVP")
UPLOAD_DIR = Path("data/uploads")
_deps: dict = {}


def deps() -> dict:
    """외부 서비스 클라이언트 지연 생성 (기동 순서 문제 회피)."""
    if not _deps:
        from .llm import LLM
        from .stores.graph import Graph
        from .stores.vector import VectorStore

        _deps["vector"] = VectorStore()
        _deps["llm"] = LLM()
        _deps["graph"] = Graph()
        _deps["graph"].apply_schema("db/neo4j/constraints.cypher")
    return _deps


class TargetEvent(BaseModel):
    area_id: str
    observed_at: datetime
    equipment_type: str
    count: int = Field(ge=0)
    lon: float | None = None
    lat: float | None = None
    image_ref: str | None = None
    source_id: int | None = None


class AssessRequest(BaseModel):
    area_id: str
    as_of: datetime


class Feedback(BaseModel):
    analyst: str
    decision: Literal["approve", "modify", "reject"]
    grade_override: str | None = None
    comment: str | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/targets")
def add_targets(events: list[TargetEvent]):
    n = pg.insert_target_events([e.model_dump() for e in events])
    return {"inserted": n}


@app.post("/ingest")
def ingest(
    file: UploadFile = File(...),
    title: str = Form(...),
    source_name: str = Form(...),
    source_type: Literal["IMINT", "HUMINT", "SIGINT", "OSINT", "DOCTRINE"] = Form(...),
    reliability: Literal["A", "B", "C", "D", "E", "F"] = Form(...),
    credibility: int = Form(..., ge=1, le=6),
    area_id: str | None = Form(None),
    reported_at: datetime | None = Form(None),
    extract_graph: bool = Form(False),
):
    from .ingest.pipeline import ingest_document

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOAD_DIR / Path(file.filename).name
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    d = deps()
    try:
        return ingest_document(
            str(dest), title=title, source_name=source_name, source_type=source_type,
            reliability=reliability, credibility=credibility, area_id=area_id, reported_at=reported_at,
            vector=d["vector"], graph=d["graph"] if extract_graph else None, llm=d["llm"] if extract_graph else None,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/assess")
def assess(req: AssessRequest):
    d = deps()
    return run_assessment(req.area_id, req.as_of, vector=d["vector"], llm=d["llm"])


@app.get("/assessments/{aid}")
def get_assessment(aid: int):
    row = pg.get_assessment(aid)
    if not row:
        raise HTTPException(404, "not found")
    return row


@app.post("/assessments/{aid}/feedback")
def feedback(aid: int, fb: Feedback):
    fid = pg.insert_feedback(aid, fb.analyst, fb.decision, fb.grade_override, fb.comment)
    pg.audit(fb.analyst, "feedback", {"assessment_id": aid, **fb.model_dump()})
    return {"feedback_id": fid}
