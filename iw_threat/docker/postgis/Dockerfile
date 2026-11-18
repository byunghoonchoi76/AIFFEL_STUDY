FROM postgres:16-bookworm
# 공식 postgres 이미지에 설정된 PGDG 저장소에서 arm64용 PostGIS 설치
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-16-postgis-3 postgresql-16-postgis-3-scripts \
 && rm -rf /var/lib/apt/lists/*
