"""PostgreSQL/PostGIS 접근."""
from __future__ import annotations

from datetime import datetime

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..analysis.change import TargetRow
from ..config import get_settings


def conn():
    s = get_settings()
    if s.pg_dsn:
        return psycopg.connect(s.pg_dsn, row_factory=dict_row, autocommit=True)
    return psycopg.connect(host=s.pg_host, port=s.pg_port, dbname=s.pg_db, user=s.pg_user,
                           password=s.postgres_password, row_factory=dict_row, autocommit=True)


def upsert_source(name: str, source_type: str, reliability: str) -> int:
    with conn() as c:
        row = c.execute("SELECT id FROM sources WHERE name=%s AND source_type=%s", (name, source_type)).fetchone()
        if row:
            c.execute("UPDATE sources SET reliability=%s WHERE id=%s", (reliability, row["id"]))
            return row["id"]
        return c.execute(
            "INSERT INTO sources(name, source_type, reliability) VALUES (%s,%s,%s) RETURNING id",
            (name, source_type, reliability),
        ).fetchone()["id"]


def insert_document(source_id: int, title: str, path: str, credibility: int,
                    area_id: str | None, reported_at: datetime | None, classification: str) -> int:
    with conn() as c:
        return c.execute(
            """INSERT INTO documents(source_id, title, path, credibility, area_id, reported_at, classification)
               VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (source_id, title, path, credibility, area_id, reported_at, classification),
        ).fetchone()["id"]


def insert_target_events(events: list[dict]) -> int:
    with conn() as c, c.cursor() as cur:
        cur.executemany(
            """INSERT INTO target_events(area_id, observed_at, equipment_type, count, geom, image_ref, source_id)
               VALUES (%(area_id)s, %(observed_at)s, %(equipment_type)s, %(count)s,
                       CASE WHEN %(lon)s::double precision IS NULL OR %(lat)s::double precision IS NULL THEN NULL
                            ELSE ST_SetSRID(ST_MakePoint(%(lon)s::double precision, %(lat)s::double precision), 4326) END,
                       %(image_ref)s, %(source_id)s)""",
            events,
        )
        return len(events)


def fetch_target_rows(area_id: str, since: datetime, until: datetime) -> list[TargetRow]:
    with conn() as c:
        rows = c.execute(
            """SELECT id, equipment_type, observed_at, count, source_id FROM target_events
               WHERE area_id=%s AND observed_at > %s AND observed_at <= %s ORDER BY observed_at""",
            (area_id, since, until),
        ).fetchall()
    return [TargetRow(**r) for r in rows]


def fetch_target_points(area_id: str, since: datetime, until: datetime) -> list[dict]:
    """좌표가 있는 표적 이벤트 (부대 식별용)."""
    with conn() as c:
        return c.execute(
            """SELECT id, equipment_type, observed_at, count, ST_Y(geom) AS lat, ST_X(geom) AS lon
               FROM target_events WHERE area_id=%s AND observed_at > %s AND observed_at <= %s
               ORDER BY observed_at""", (area_id, since, until)).fetchall()


def delete_targets(area_id: str) -> int:
    with conn() as c:
        return c.execute("DELETE FROM target_events WHERE area_id=%s", (area_id,)).rowcount


def delete_documents(area_id: str) -> int:
    with conn() as c:
        return c.execute("DELETE FROM documents WHERE area_id=%s", (area_id,)).rowcount


def insert_assessment(area_id: str, as_of: datetime, top: str, score: float, grade: str,
                      confidence: str, result: dict, narrative: dict, model_info: dict) -> int:
    with conn() as c:
        return c.execute(
            """INSERT INTO assessments(area_id, as_of, top_hypothesis, threat_score, grade, confidence,
                                       result, narrative, model_info)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (area_id, as_of, top, score, grade, confidence, Jsonb(result), Jsonb(narrative), Jsonb(model_info)),
        ).fetchone()["id"]


def fetch_assessment_history(area_id: str, since: datetime, until: datetime) -> list[dict]:
    """기준 시각별 최신 평가 1건씩 (재평가 시 마지막 결과 사용)."""
    with conn() as c:
        return c.execute(
            """SELECT DISTINCT ON (as_of) as_of, threat_score, confidence, result
               FROM assessments WHERE area_id=%s AND as_of > %s AND as_of <= %s
               ORDER BY as_of, created_at DESC""", (area_id, since, until)).fetchall()


def get_assessment(aid: int) -> dict | None:
    with conn() as c:
        return c.execute("SELECT * FROM assessments WHERE id=%s", (aid,)).fetchone()


def insert_feedback(aid: int, analyst: str, decision: str, grade_override: str | None, comment: str | None) -> int:
    with conn() as c:
        return c.execute(
            """INSERT INTO feedback(assessment_id, analyst, decision, grade_override, comment)
               VALUES (%s,%s,%s,%s,%s) RETURNING id""",
            (aid, analyst, decision, grade_override, comment),
        ).fetchone()["id"]


def audit(actor: str, action: str, detail: dict) -> None:
    with conn() as c:
        c.execute("INSERT INTO audit_log(actor, action, detail) VALUES (%s,%s,%s)", (actor, action, Jsonb(detail)))


# ── 기상 관측·예보 ──
_WX_READY = False


def ensure_weather() -> None:
    global _WX_READY
    if _WX_READY:
        return
    from pathlib import Path
    sql = Path(__file__).resolve().parents[2] / "db/postgis/03_weather.sql"
    with conn() as c:
        c.execute(sql.read_text(encoding="utf-8"))
    _WX_READY = True


def insert_weather(rows: list[dict]) -> int:
    ensure_weather()
    with conn() as c, c.cursor() as cur:
        cur.executemany("INSERT INTO weather_obs (area_id, valid_at, kind, data) VALUES (%s, %s, %s, %s)",
                        [(r["area_id"], r["valid_at"], r.get("kind", "obs"), Jsonb(r["data"])) for r in rows])
    return len(rows)


def fetch_weather(area_id: str, since: datetime, until: datetime) -> list[dict]:
    ensure_weather()
    with conn() as c:
        return c.execute("SELECT valid_at, kind, data FROM weather_obs WHERE area_id=%s AND valid_at > %s AND valid_at <= %s "
                         "ORDER BY valid_at", (area_id, since, until)).fetchall()


def delete_weather(area_id: str) -> int:
    ensure_weather()
    with conn() as c:
        return c.execute("DELETE FROM weather_obs WHERE area_id=%s", (area_id,)).rowcount


# ── 합참·연합사 핵심표적·고가치표적 목록 ──
_KT_READY = False


def ensure_keytargets() -> None:
    global _KT_READY
    if _KT_READY:
        return
    from pathlib import Path
    sql = Path(__file__).resolve().parents[2] / "db/postgis/04_keytargets.sql"
    with conn() as c:
        c.execute(sql.read_text(encoding="utf-8"))
    _KT_READY = True


def upsert_keytargets(rows: list[dict]) -> int:
    ensure_keytargets()
    cols = ["target_id", "area_id", "name", "category", "owner", "unit_name", "lat", "lon", "radius_m",
            "equipment", "pir_ids", "hypotheses", "note"]
    with conn() as c, c.cursor() as cur:
        for r in rows:
            vals = [Jsonb(r.get(k) or ([] if k != "hypotheses" else ["H_ATTACK"])) if k in ("equipment", "pir_ids", "hypotheses")
                    else r.get(k) for k in cols]
            cur.execute(f"INSERT INTO key_targets ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))}) "
                        "ON CONFLICT (target_id) DO UPDATE SET " + ", ".join(f"{k}=EXCLUDED.{k}" for k in cols[1:]) +
                        ", updated_at=now()", vals)
    return len(rows)


def fetch_keytargets(area_id: str) -> list[dict]:
    ensure_keytargets()
    with conn() as c:
        return c.execute("SELECT * FROM key_targets WHERE area_id IS NULL OR area_id=%s ORDER BY target_id", (area_id,)).fetchall()


def delete_keytargets(area_id: str) -> int:
    ensure_keytargets()
    with conn() as c:
        return c.execute("DELETE FROM key_targets WHERE area_id=%s", (area_id,)).rowcount
