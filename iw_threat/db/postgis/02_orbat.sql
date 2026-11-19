-- 전투서열(ORBAT): 부대·별칭·주둔지·보유장비. 기존 DB에도 적용되도록 IF NOT EXISTS 사용
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS orbat_units (
  unit_key    TEXT PRIMARY KEY,          -- 정규화 키 ('제'·공백 제거)
  name        TEXT NOT NULL,
  echelon     TEXT,
  parent_key  TEXT,
  source_doc  TEXT,
  source_page INT,
  updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orbat_aliases (
  alias    TEXT PRIMARY KEY,
  unit_key TEXT NOT NULL REFERENCES orbat_units(unit_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS orbat_garrisons (
  id            SERIAL PRIMARY KEY,
  unit_key      TEXT NOT NULL REFERENCES orbat_units(unit_key) ON DELETE CASCADE,
  location_type TEXT,
  mgrs          TEXT NOT NULL,
  precision_m   INT,
  radius_m      INT NOT NULL,            -- 표적을 이 부대 활동으로 볼 반경 (제대별 기본값)
  geom          GEOMETRY(Point, 4326) NOT NULL,
  as_of         TEXT,
  source_doc    TEXT,
  source_page   INT,
  UNIQUE (unit_key, mgrs)
);
CREATE INDEX IF NOT EXISTS orbat_garrisons_gix ON orbat_garrisons USING GIST (geom);

CREATE TABLE IF NOT EXISTS orbat_equipment (
  unit_key   TEXT NOT NULL REFERENCES orbat_units(unit_key) ON DELETE CASCADE,
  equipment  TEXT NOT NULL,
  count      TEXT,
  as_of      TEXT,
  source_doc TEXT,
  PRIMARY KEY (unit_key, equipment)
);
