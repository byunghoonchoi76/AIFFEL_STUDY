-- 합참·연합사 관리 전평시 표적 중 핵심표적·고가치표적 (실제 목록은 폐쇄망에서만 적재)
CREATE TABLE IF NOT EXISTS key_targets (
    target_id   TEXT PRIMARY KEY,
    area_id     TEXT,                       -- NULL이면 전 지역 공통
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,              -- 핵심표적 / 고가치표적
    owner       TEXT NOT NULL,              -- 합참 / 연합사 / 공동
    unit_name   TEXT,                       -- 전투서열 부대명으로 매칭 (선택)
    lat         DOUBLE PRECISION,           -- 위치 매칭 (선택)
    lon         DOUBLE PRECISION,
    radius_m    DOUBLE PRECISION DEFAULT 2000,
    equipment   JSONB DEFAULT '[]'::jsonb,  -- 활동으로 볼 장비 유형 (빈 목록 = 전체)
    pir_ids     JSONB DEFAULT '[]'::jsonb,
    hypotheses  JSONB DEFAULT '["H_ATTACK"]'::jsonb,
    note        TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
