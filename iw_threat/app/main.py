"""FastAPI 진입점."""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator

from .analysis.assess import run_assessment
from .config import to_aware
from .stores import pg

app = FastAPI(title="징후·위협도 AI 추론 MVP")


@app.exception_handler(Exception)
async def _unhandled(request, exc: Exception):
    """시제품 단계: 원인 파악을 위해 오류 유형·메시지를 응답에 포함 (운용 단계에서는 제거)."""
    from fastapi.responses import JSONResponse
    import logging

    logging.getLogger("uvicorn.error").exception("unhandled error at %s", request.url.path)
    return JSONResponse(status_code=500, content={"error": type(exc).__name__, "detail": str(exc)[:1000]})
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

    _tz = field_validator("observed_at")(lambda v: to_aware(v))


class AssessRequest(BaseModel):
    area_id: str
    as_of: datetime
    explain: bool = True          # False: 해설(LLM) 생략 — 날짜별 반복 평가 시 시간 단축

    _tz = field_validator("as_of")(lambda v: to_aware(v))


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
            reliability=reliability, credibility=credibility, area_id=area_id, reported_at=to_aware(reported_at),
            vector=d["vector"], graph=d["graph"] if extract_graph else None, llm=d["llm"] if extract_graph else None,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.delete("/targets")
def delete_targets(area_id: str):
    n = pg.delete_targets(area_id)
    pg.audit("system", "delete_targets", {"area_id": area_id, "deleted": n})
    return {"deleted": n}


@app.delete("/documents")
def delete_documents(area_id: str):
    """지역의 첩보 문서 삭제 (벡터 DB 청크 + 문서 메타). 시나리오 재실행 전 정리용."""
    n_vec = deps()["vector"].delete_area(area_id)
    n_doc = pg.delete_documents(area_id)
    pg.audit("system", "delete_documents", {"area_id": area_id, "documents": n_doc})
    return {"documents": n_doc, "vector_deleted": n_vec}


@app.post("/orbat/load")
def orbat_load(file: UploadFile = File(...), mode: Literal["auto", "all"] = Form("auto"),
               to_graph: bool = Form(True)):
    """검토된 records.csv 적재. auto: approve 열이 있으면 승인 행만, 없으면 문제 없는 행만."""
    from .orbat import store

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOAD_DIR / Path(file.filename).name
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    rows = store.read_records_csv(dest, mode)
    res = store.load_records(rows)
    if to_graph:
        res["graph_units"] = deps()["graph"].load_orbat(rows)
    return res


@app.get("/orbat/units")
def orbat_units():
    from .orbat import store
    return store.list_units()


@app.get("/orbat/identify")
def orbat_identify(lat: float, lon: float, equipment: str | None = None):
    from .orbat import store
    res = store.identify(lat, lon, equipment)
    if res["best"]:
        res["parent_chain"] = store.parent_chain(res["best"]["unit_key"])
    return res


@app.post("/assess")
def assess(req: AssessRequest):
    d = deps()
    res = run_assessment(req.area_id, req.as_of, vector=d["vector"], llm=d["llm"], explain=req.explain)
    from .analysis.readiness_service import current_readiness
    res["readiness"] = current_readiness(req.area_id, req.as_of)   # 직전 평가 이력 포함 태세 건의
    return res


@app.get("/readiness")
def readiness(area_id: str, as_of: datetime):
    from .analysis.readiness_service import current_readiness
    return current_readiness(area_id, as_of)


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
