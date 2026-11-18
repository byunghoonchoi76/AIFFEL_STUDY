"""PostgreSQL/PostGIS 접근."""
from __future__ import annotations

from datetime import datetime

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..analysis.change import TargetRow
from ..config import get_settings


def conn():
    return psycopg.connect(get_settings().pg_dsn, row_factory=dict_row, autocommit=True)


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
                       CASE WHEN %(lon)s IS NULL THEN NULL ELSE ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326) END,
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


def insert_assessment(area_id: str, as_of: datetime, top: str, score: float, grade: str,
                      confidence: str, result: dict, narrative: dict, model_info: dict) -> int:
    with conn() as c:
        return c.execute(
            """INSERT INTO assessments(area_id, as_of, top_hypothesis, threat_score, grade, confidence,
                                       result, narrative, model_info)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (area_id, as_of, top, score, grade, confidence, Jsonb(result), Jsonb(narrative), Jsonb(model_info)),
        ).fetchone()["id"]


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
