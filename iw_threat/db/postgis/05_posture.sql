-- 합참·연합사가 판단·발령한 현 태세 (워치콘·데프콘·진돗개·두루미) — 평가 입력 조건
CREATE TABLE IF NOT EXISTS issued_posture (
    id          BIGSERIAL PRIMARY KEY,
    area_id     TEXT,                    -- NULL이면 전 지역 공통
    track       TEXT NOT NULL,           -- watchcon / defcon / jindogae / durumi
    level       INT NOT NULL,
    valid_from  TIMESTAMPTZ NOT NULL,
    issuer      TEXT NOT NULL,           -- 합참 / 연합사 / 합참·연합사
    note        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS issued_posture_idx ON issued_posture (track, valid_from);
