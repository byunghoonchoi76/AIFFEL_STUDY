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
    sar_verified: bool | None = None     # SAR 금속 반사파 일치 여부 (False = 모형·기만체 의심)

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


class WeatherObs(BaseModel):
    area_id: str
    valid_at: datetime
    kind: Literal["obs", "fcst"] = "obs"
    data: dict      # cloud_pct, precip_mm, temp_c, visibility_km, ceiling_m, wind_ms, ground, river, snow_cm, sea_state

    _tz = field_validator("valid_at")(lambda v: to_aware(v))


@app.post("/weather")
def add_weather(rows: list[WeatherObs]):
    """기상 관측·예보 입력 (기상 부대 자료 또는 공개 기상자료)."""
    return {"inserted": pg.insert_weather([r.model_dump() for r in rows])}


@app.get("/weather/effects")
def weather_effects(area_id: str, as_of: datetime):
    """기상이 작전에 미치는 영향 (평가 없이 조회)."""
    from .analysis.assess import weather_assessment
    from .analysis.catalog import load_catalog, load_grading
    from .config import get_settings
    s = get_settings()
    res = weather_assessment(area_id, to_aware(as_of), load_catalog(s.indicators_path), load_grading(s.grading_path))
    res["evidence"] = [e.quote for e in res.get("evidence", [])]
    return res


@app.delete("/weather")
def delete_weather(area_id: str):
    return {"deleted": pg.delete_weather(area_id)}


class KeyTargetIn(BaseModel):
    target_id: str
    area_id: str | None = None
    name: str
    category: Literal["핵심표적", "고가치표적"]
    owner: Literal["합참", "연합사", "공동"]
    unit_name: str | None = None
    lat: float | None = None
    lon: float | None = None
    radius_m: float = 2000.0
    equipment: list[str] = []
    pir_ids: list[str] = []
    hypotheses: list[str] = ["H_ATTACK"]
    note: str | None = None


class PostureIn(BaseModel):
    area_id: str | None = None
    track: Literal["watchcon", "defcon", "jindogae", "durumi"]
    level: int
    valid_from: datetime
    issuer: str = "합참·연합사"
    note: str | None = None

    _tz = field_validator("valid_from")(lambda v: to_aware(v))


@app.post("/posture")
def add_posture(rows: list[PostureIn]):
    """합참·연합사가 판단·발령한 현 태세 입력 — 평가의 참고 조건으로 적용."""
    return {"inserted": pg.insert_posture([r.model_dump() for r in rows])}


@app.get("/posture")
def get_posture(area_id: str, as_of: datetime):
    return pg.fetch_posture(area_id, to_aware(as_of))


@app.delete("/posture")
def delete_posture(area_id: str):
    return {"deleted": pg.delete_posture(area_id)}


@app.post("/keytargets")
def add_keytargets(rows: list[KeyTargetIn]):
    """합참·연합사 표적목록(핵심표적·고가치표적) 적재 — 실제 목록은 폐쇄망에서만."""
    n = pg.upsert_keytargets([r.model_dump() for r in rows])
    pg.audit("system", "keytargets", {"count": n})
    return {"upserted": n}


@app.get("/keytargets")
def list_keytargets(area_id: str):
    return pg.fetch_keytargets(area_id)


@app.delete("/keytargets")
def delete_keytargets(area_id: str):
    return {"deleted": pg.delete_keytargets(area_id)}


@app.post("/assess")
def assess(req: AssessRequest):
    d = deps()
    res = run_assessment(req.area_id, req.as_of, vector=d["vector"], llm=d["llm"], explain=req.explain)
    from .analysis.readiness_service import current_readiness
    res["readiness"] = current_readiness(req.area_id, req.as_of)   # 직전 평가 이력 포함 태세 판단 조건(상황 판단 참고)
    return res


@app.get("/targets/recent")
def targets_recent(area_id: str, as_of: datetime, days: int = 7):
    """지도용: 최근 표적 + 식별 부대."""
    from datetime import timedelta
    from .orbat import store as orbat_store
    as_of = to_aware(as_of)
    pts = pg.fetch_target_points(area_id, as_of - timedelta(days=days), as_of)
    unit_of = orbat_store.identify_events(pts) if orbat_store.has_data() else {}
    return [{**p, "observed_at": p["observed_at"].isoformat(),
             "unit_name": (unit_of.get(p["id"]) or {}).get("name"),
             "dist_m": (unit_of.get(p["id"]) or {}).get("dist_m")} for p in pts[-2000:]]


@app.get("/orbat/garrisons")
def orbat_garrisons():
    from .orbat import store as orbat_store
    if not orbat_store.has_data():
        return []
    with pg.conn() as c:
        return c.execute("""SELECT u.name, u.echelon, u.parent_key, g.location_type, g.radius_m,
                                   ST_Y(g.geom) AS lat, ST_X(g.geom) AS lon
                            FROM orbat_garrisons g JOIN orbat_units u USING (unit_key)""").fetchall()


@app.get("/assessments/history")
def assessment_history(area_id: str, as_of: datetime, days: int = 30):
    """추이 차트용: 기준 시각별 최신 평가의 가설 확률·등급·신뢰도."""
    from datetime import timedelta
    as_of = to_aware(as_of)
    rows = pg.fetch_assessment_history(area_id, as_of - timedelta(days=days), as_of)
    return [{"as_of": r["as_of"].isoformat(), "threat_score": r["threat_score"], "confidence": r["confidence"],
             "probabilities": {h["name"]: h["probability"] for h in r["result"].get("hypotheses", [])},
             "indicators": sorted({c["indicator_id"] for h in r["result"].get("hypotheses", []) for c in h["contributions"]})}
            for r in rows]


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
