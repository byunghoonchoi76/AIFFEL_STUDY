-- 기상 관측·예보 (전장정보분석: 기상이 작전에 미치는 영향)
CREATE TABLE IF NOT EXISTS weather_obs (
    id          BIGSERIAL PRIMARY KEY,
    area_id     TEXT NOT NULL,
    valid_at    TIMESTAMPTZ NOT NULL,          -- 관측 또는 예보 대상 시각
    kind        TEXT NOT NULL DEFAULT 'obs',   -- obs(관측) / fcst(예보)
    data        JSONB NOT NULL,                -- cloud_pct, precip_mm, temp_c, visibility_km, ceiling_m, wind_ms, ground, river, snow_cm, sea_state
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS weather_obs_area_time ON weather_obs (area_id, valid_at);
