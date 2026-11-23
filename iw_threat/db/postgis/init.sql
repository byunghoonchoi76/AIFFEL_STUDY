CREATE EXTENSION IF NOT EXISTS postgis;

-- 출처 (6×6 평가의 출처 신뢰도는 출처 단위)
CREATE TABLE sources (
  id           SERIAL PRIMARY KEY,
  name         TEXT NOT NULL,
  source_type  TEXT NOT NULL CHECK (source_type IN ('IMINT','HUMINT','SIGINT','OSINT','DOCTRINE')),
  reliability  CHAR(1) NOT NULL CHECK (reliability IN ('A','B','C','D','E','F'))
);

-- 문서·첩보 (정보 신빙성은 문서 단위)
CREATE TABLE documents (
  id             SERIAL PRIMARY KEY,
  source_id      INT REFERENCES sources(id),
  title          TEXT,
  path           TEXT,
  credibility    SMALLINT CHECK (credibility BETWEEN 1 AND 6),
  classification TEXT DEFAULT 'UNCLASSIFIED',
  area_id        TEXT,
  reported_at    TIMESTAMPTZ,
  ingested_at    TIMESTAMPTZ DEFAULT now()
);

-- 영상 판독 표적 이벤트 (입력)
CREATE TABLE target_events (
  id             SERIAL PRIMARY KEY,
  area_id        TEXT NOT NULL,
  observed_at    TIMESTAMPTZ NOT NULL,
  equipment_type TEXT NOT NULL,
  count          INT NOT NULL CHECK (count >= 0),
  geom           GEOMETRY(Point, 4326),
  image_ref      TEXT,
  source_id      INT REFERENCES sources(id),
  sar_verified   BOOLEAN          -- SAR 금속 반사파 일치 여부 (FALSE = 모형·기만체 의심, NULL = 미확인)
);
CREATE INDEX ON target_events (area_id, equipment_type, observed_at);
CREATE INDEX ON target_events USING GIST (geom);

-- 평가 결과
CREATE TABLE assessments (
  id           SERIAL PRIMARY KEY,
  area_id      TEXT NOT NULL,
  as_of        TIMESTAMPTZ NOT NULL,
  created_at   TIMESTAMPTZ DEFAULT now(),
  top_hypothesis TEXT,
  threat_score DOUBLE PRECISION,
  grade        TEXT,
  confidence   TEXT,
  result       JSONB NOT NULL,     -- 가설별 확률·지표별 기여도 전체
  narrative    JSONB,              -- 근거 검증을 통과한 해설 문장
  model_info   JSONB
);

-- 분석관 피드백 (보정·KPI 산출용)
CREATE TABLE feedback (
  id             SERIAL PRIMARY KEY,
  assessment_id  INT REFERENCES assessments(id),
  analyst        TEXT NOT NULL,
  decision       TEXT NOT NULL CHECK (decision IN ('approve','modify','reject')),
  grade_override TEXT,
  comment        TEXT,
  created_at     TIMESTAMPTZ DEFAULT now()
);

-- 감사 로그
CREATE TABLE audit_log (
  id         BIGSERIAL PRIMARY KEY,
  at         TIMESTAMPTZ DEFAULT now(),
  actor      TEXT,
  action     TEXT,
  detail     JSONB
);
