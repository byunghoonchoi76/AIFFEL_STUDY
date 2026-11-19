"""전투서열 DB 적재·조회 (PostGIS) 및 표적 → 부대 식별."""
from __future__ import annotations

import csv
from pathlib import Path

from ..stores import pg
from .identify import best_match, radius_for, rank_candidates

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "db/postgis/02_orbat.sql"


def ensure_schema() -> None:
    with pg.conn() as c:
        c.execute(SCHEMA_SQL.read_text(encoding="utf-8"))


def read_records_csv(path: str | Path, mode: str = "auto") -> list[dict]:
    """검토된 records.csv 읽기.
    mode=auto: 'approve' 열이 있으면 Y/1/O 행만, 없으면 문제(issues) 없는 행만
    mode=all : 전부 (시험용)"""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if mode == "all" or not rows:
        return rows
    if "approve" in rows[0]:
        return [r for r in rows if str(r.get("approve", "")).strip().upper() in {"Y", "1", "O", "승인"}]
    return [r for r in rows if not (r.get("issues") or "").strip()]


def load_records(rows: list[dict]) -> dict:
    """부대·별칭·주둔지·장비 upsert. 같은 부대가 여러 행이면 정보를 합침(빈 값은 덮어쓰지 않음)."""
    ensure_schema()
    n_units = n_gar = n_eq = 0
    with pg.conn() as c:
        for r in rows:
            key = (r.get("unit_key") or "").strip()
            if not key:
                continue
            page = int(r["page"]) if str(r.get("page", "")).isdigit() else None
            c.execute(
                """INSERT INTO orbat_units(unit_key, name, echelon, parent_key, source_doc, source_page)
                   VALUES (%(k)s, %(n)s, %(e)s, %(p)s, %(d)s, %(pg)s)
                   ON CONFLICT (unit_key) DO UPDATE SET
                     name = EXCLUDED.name,
                     echelon = COALESCE(EXCLUDED.echelon, orbat_units.echelon),
                     parent_key = COALESCE(EXCLUDED.parent_key, orbat_units.parent_key),
                     updated_at = now()""",
                {"k": key, "n": r.get("unit_raw") or key, "e": r.get("echelon") or None,
                 "p": r.get("parent_key") or None, "d": r.get("doc"), "pg": page},
            )
            n_units += 1
            if r.get("unit_raw"):
                c.execute("""INSERT INTO orbat_aliases(alias, unit_key) VALUES (%s, %s)
                             ON CONFLICT (alias) DO UPDATE SET unit_key = EXCLUDED.unit_key""",
                          (r["unit_raw"].strip(), key))
            if r.get("mgrs") and r.get("lat") and r.get("lon"):
                prec = int(float(r["mgrs_precision_m"])) if r.get("mgrs_precision_m") else None
                c.execute(
                    """INSERT INTO orbat_garrisons(unit_key, location_type, mgrs, precision_m, radius_m, geom,
                                                   as_of, source_doc, source_page)
                       VALUES (%s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s)
                       ON CONFLICT (unit_key, mgrs) DO UPDATE SET location_type = EXCLUDED.location_type,
                         radius_m = EXCLUDED.radius_m, as_of = EXCLUDED.as_of""",
                    (key, r.get("location_type") or None, r["mgrs"], prec, radius_for(r.get("echelon"), prec),
                     float(r["lon"]), float(r["lat"]), r.get("as_of") or None, r.get("doc"), page),
                )
                n_gar += 1
            if r.get("equipment"):
                c.execute("""INSERT INTO orbat_equipment(unit_key, equipment, count, as_of, source_doc)
                             VALUES (%s, %s, %s, %s, %s)
                             ON CONFLICT (unit_key, equipment) DO UPDATE SET count = EXCLUDED.count, as_of = EXCLUDED.as_of""",
                          (key, r["equipment"], r.get("count") or None, r.get("as_of") or None, r.get("doc")))
                n_eq += 1
    pg.audit("system", "orbat_load", {"rows": len(rows), "units": n_units, "garrisons": n_gar, "equipment": n_eq})
    return {"rows": len(rows), "unit_rows": n_units, "garrisons": n_gar, "equipment": n_eq}


def candidates(lat: float, lon: float, max_m: int = 30000, limit: int = 10) -> list[dict]:
    with pg.conn() as c:
        return c.execute(
            """WITH p AS (SELECT ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography AS g)
               SELECT g.unit_key, u.name, u.echelon, u.parent_key, g.location_type, g.mgrs, g.radius_m,
                      ST_Distance(g.geom::geography, p.g) AS dist_m
               FROM orbat_garrisons g JOIN orbat_units u USING (unit_key), p
               WHERE ST_DWithin(g.geom::geography, p.g, %(max_m)s)
               ORDER BY dist_m LIMIT %(limit)s""",
            {"lat": lat, "lon": lon, "max_m": max_m, "limit": limit},
        ).fetchall()


def identify(lat: float, lon: float, equipment: str | None = None) -> dict:
    cands = candidates(lat, lon)
    return {"best": best_match(cands, equipment), "candidates": rank_candidates(cands, equipment)[:5]}


def parent_chain(unit_key: str, max_depth: int = 6) -> list[str]:
    chain, cur = [], unit_key
    with pg.conn() as c:
        for _ in range(max_depth):
            row = c.execute("SELECT parent_key FROM orbat_units WHERE unit_key=%s", (cur,)).fetchone()
            if not row or not row["parent_key"] or row["parent_key"] in chain:
                break
            cur = row["parent_key"]
            chain.append(cur)
    return chain


def list_units(limit: int = 500) -> list[dict]:
    ensure_schema()
    with pg.conn() as c:
        return c.execute(
            """SELECT u.unit_key, u.name, u.echelon, u.parent_key, count(g.id) AS garrisons
               FROM orbat_units u LEFT JOIN orbat_garrisons g USING (unit_key)
               GROUP BY u.unit_key ORDER BY u.unit_key LIMIT %s""", (limit,)).fetchall()


def has_data() -> bool:
    try:
        with pg.conn() as c:
            row = c.execute("SELECT to_regclass('orbat_garrisons') AS t").fetchone()
            if not row["t"]:
                return False
            return c.execute("SELECT EXISTS (SELECT 1 FROM orbat_garrisons) AS e").fetchone()["e"]
    except Exception:  # noqa: BLE001
        return False


def attribute_targets(points: list[dict]) -> list[dict]:
    """표적 이벤트들 → (장비, 부대)별 집계. 반경 밖이면 unit_key=None(미식별)."""
    agg: dict[tuple, dict] = {}
    for p in points:
        if p.get("lat") is None:
            continue
        best = identify(p["lat"], p["lon"], p["equipment_type"])["best"]
        key = (p["equipment_type"], best["unit_key"] if best else None)
        a = agg.setdefault(key, {"equipment": p["equipment_type"], "unit_key": key[1],
                                 "unit_name": best["name"] if best else None,
                                 "echelon": best["echelon"] if best else None,
                                 "parent_key": best["parent_key"] if best else None,
                                 "location_type": best["location_type"] if best else None,
                                 "dist_km": round(best["dist_m"] / 1000, 2) if best else None,
                                 "affinity": best["affinity"] if best else False,
                                 "max_count": 0, "event_ids": []})
        a["max_count"] = max(a["max_count"], p["count"])
        a["event_ids"].append(p["id"])
    return sorted(agg.values(), key=lambda a: (a["equipment"], a["unit_key"] is None, -a["max_count"]))
