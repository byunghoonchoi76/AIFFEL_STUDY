# 징후·위협도 AI 추론 MVP (정보사-1)

위성영상 판독 표적과 다출처 첩보를 위협징후목록에 매칭해 위협도를 산정하고, 근거가 확인된 해설만 제시하는 시제품.
MSI EdgeXpert MS-C931(NVIDIA GB10, arm64, 128GB 통합 메모리) 한 대에서 동작하도록 구성.

## 구성

| 서비스 | 역할 | 포트 |
|---|---|---|
| llm | vLLM + gpt-oss (OpenAI 호환 API) | 8000 |
| qdrant | 문서 청크 벡터 검색 | 6333 |
| neo4j | 지식그래프 (부대·장비·지역·활동) | 7474 / 7687 |
| postgis | 표적 이력·평가·피드백·감사로그 | 5432 |
| api | FastAPI (수집·평가·피드백) | 8080 |
| ui | Streamlit 분석관 화면 | 8501 |

## 실행

```bash
cp .env.example .env              # VLLM_IMAGE 태그, 비밀번호 수정
./scripts/download_models.sh      # 모델 가중치 → ./models
docker compose up -d --build
docker compose logs -f llm        # 모델 로딩 완료까지 대기 (120b는 수 분)

python scripts/benchmark_llm.py   # TTFT·tok/s 실측 → 요약서 성능 칸
python scripts/seed_sample.py     # 가상 시나리오 표적 등록 (AREA-01)
```

브라우저에서 `http://<장비IP>:8501` 접속 → 문서 수집(교리는 DOCTRINE, `data/samples/osint_sample.txt`는 OSINT·AREA-01) → 평가 실행.

## 처리 흐름

1. **변화 분석** `app/analysis/change.py` — 최근 7일 최대 수량 vs 이전 30일 평균
2. **지표 매칭** `app/analysis/matching.py`
   - 영상: 장비 분류·증가량 임계값 규칙
   - 텍스트: LLM 분류 후 **인용문이 원문에 실제로 있는지 코드로 대조**, 없으면 폐기
3. **위협도 산정** `app/analysis/scoring.py` (LLM 미관여, 결정적 계산)
   - 첩보 가중치 = 출처신뢰도(A~F) × 정보신빙성(1~6) × exp(−경과일/τ)
   - 지표 충족 강도: 같은 출처 중복 배제, 출처 유형 간 독립 확인 결합
   - logit(P) = logit(사전확률) + Σ 강도 × ln(LR), 위협 점수 = P × 영향도
   - 분석 신뢰도(상/중/하) = 독립 출처 유형 수 + 충족 지표 수
4. **근거 해설** `app/analysis/explain.py` — 모든 문장이 허용된 근거 ID를 인용해야 통과
5. **분석관 피드백** — 승인/수정/반려와 등급 수정 저장 → 가중치·LR 보정 자료

## 설정 파일

- `config/indicators.yaml` — 위협징후목록(가설·지표·LR·τ·장비·임계값). **현재 값은 공개 교리 일반론 기반 예시**이며 정보사 목록 확보 시 교체
- `config/source_grading.yaml` — 6×6 평가 등급 → 가중치 환산표

## 전투서열 PDF 파싱 (1단계: 규칙 기반)

```bash
python scripts/parse_orbat_pdf.py 문서.pdf --out data/orbat/문서명
```

- 표: 머리행에서 부대명·상급부대·좌표·장비·수량·기준일 열 자동 인식 → 행 단위 레코드
- 서술형: MGRS가 있는 줄을 같은 줄(없으면 바로 위 2줄)의 부대명과 연결, 사령부·주둔지·예비진지 등 위치 유형 표시
- 좌표: MGRS → 위경도(정밀도 m 포함), 한반도 범위 밖·자릿수 불일치는 폐기
- 제대: 부대명 접미사(군단·사단·여단…) 또는 근처 단대호 제대 기호(XX·X·III·II)
- 단대호·사진: 이미지로 저장하고 근처 부대 레코드에 연결 (분석관 확인용)
- 검증: 제대 역전, 자기참조, 상하 순환, 같은 유형 위치 1km 이상 차이·소속 불일치 → `conflicts.csv`
- 산출물: `records.csv`, `conflicts.csv`, `images/`, `review.html`, `summary.json`
- 스캔 쪽은 Tesseract OCR로 처리 (정확도 낮음 — 검토 필수)
- **`data/orbat/`는 git에서 제외됨. 비밀 문서는 MS-C931 안에서만 처리**

정확도 실측 (정답을 아는 가상 전투서열 PDF 자동 생성 후 비교):

```bash
python scripts/orbat_benchmark.py --units 80 --seed 1          # 텍스트 PDF
python scripts/orbat_benchmark.py --units 80 --seed 1 --scan   # 스캔 PDF(OCR 경로)
```

| 경로 | 부대 식별 | 좌표 10m 이내 | 제대 | 상급부대 | 쪽당 처리 |
|---|---|---|---|---|---|
| 텍스트 PDF (3회) | 100% | 100% | 100% | 100% | 약 0.1초 |
| 스캔 PDF (200dpi, 표 글자 6.5pt, 3회 평균) | 86% | 81% | 100% | 100% | 약 3.2초 |

스캔 처리: 괘선 검출로 표 격자 인식 → 열 단위 OCR(좌표 열은 숫자·영문만 허용) → 행 배정,
좌표 칸 복원(S→8·5, D→0, H→1 오인식 보정), 문서 중심에서 150km 이상 벗어난 좌표는 '좌표 이상치'로 표시

## 전투서열 DB 적재 · 표적 → 부대 식별

1. PDF 파싱 → `records.csv` 검토 (엑셀에서 `approve` 열을 추가해 Y 표시, 없으면 문제 없는 행만 적재)
2. 적재: `curl -F file=@data/orbat/문서명/records.csv http://localhost:8080/orbat/load`
   - PostGIS: `orbat_units`(부대·제대·상급) / `orbat_aliases`(표기 변형) / `orbat_garrisons`(주둔지 좌표·반경) / `orbat_equipment`
   - Neo4j: `(:Unit)-[:예하]->(:Unit)`
3. 조회: `GET /orbat/units`, `GET /orbat/identify?lat=38.02&lon=127.03&equipment=tank`
4. 평가 시 자동 반영 (**부대별 평시 기준선**): 기준기간+최근 표적을 모두 부대별로 나눠 부대마다 최근 최대 vs 평시 평균을 비교
   → 근거 문구 `[식별 부대: 제○여단(여단) 0.3km, 평시 대비 3.1배]`, 결과 `baseline_mode: unit`, `changes[].unit_name·ratio`, `unit_attribution`
   - 지역 평균에서는 서로 다른 부대 수량이 섞여 증가가 가려질 수 있음 → 부대 단위로 분리해 비교
   - 전투서열 DB가 없으면 지역 단위 비교(`baseline_mode: area`)
5. **제대별 임계값** (`config/indicators.yaml`의 `thresholds`): 지역 기준 `min_delta`를 제대에 맞게 조정
   - 우선순위: 지표별 `min_delta_by_echelon` → `echelon_scale` 배율(군단 1.5·사단 1.0·여단 0.5·연대 0.3·대대 0.2, 올림·최소 1) → 기본값
   - 배수 기준: 평시 대비 `unit_min_ratio`(2.0)배 이상이고 증가량이 임계값의 절반 이상이면 충족
   - 근거 문구에 적용 기준 표기: `(기준 여단 +6대 또는 2배)`

식별 규칙 (`app/orbat/identify.py`)
- 제대별 활동 반경: 군단 20km · 사단 10km · 여단 5km · 연대 3km · 대대 1.5km (+좌표 정밀도)
- 점수 = 거리 ÷ 반경, 장비-병종 일치(전차↔전차·기갑·기계화, 야포↔포병, 도하장비↔공병 등) 시 가중
- 반경 밖이면 '미식별'

가상 샘플: `python scripts/seed_orbat_sample.py && python scripts/seed_sample.py --reset`

## 합성 시나리오 → 분석보고서

```bash
python scripts/run_scenario.py scenarios/attack_prep.yaml scenarios/large_exercise.yaml --step 2
```

- 시나리오 5종: 공격 준비(`attack_prep`), 대규모 훈련(`large_exercise`), 평시(`peacetime`), 기만·위장 집결(`deception`), 정보 공백(`info_gap`)
- `scenarios/*.yaml`: 가상 부대·주둔지, 부대 **활동 일정**(수량 변화·이동), 분석관이 쓰는 첩보 문장(오정보 포함), 정답(의도·D일)
  - 생성기는 위협징후목록(지표·우도비·임계값)을 참조하지 않음 → 평가 순환 방지
- 관측 잡음: 위성 관측 누락·구름일·판독 수량 오차·좌표 오차·장비 탐지 누락·오탐
- 실행: 지역 데이터 정리 → 전투서열·표적·첩보 API 적재 → D-30~D일 날짜별 평가(마지막 날만 해설 생성)
- 산출: `data/sim/<id>/분석보고서_<id>.docx`(종합 판단·위협도 추이 그래프·부대별 변화·징후별 분석·근거·분석관 검토란
  + 부록 정답 대비 평가: 경보 선행일수/오경보 일수, 최종 가설 정답 여부, 표적→부대 식별 정확도), `result.json`

## 경보·감시태세 건의 (워치콘·진돗개·두루미)

`config/readiness.yaml` (**예시 기준** — 실제 단계 구분·발령 기준으로 교체). 시스템은 **건의만**, 발령은 지휘관 권한.

| 트랙 | 보는 위협 | 단계(예시) |
|---|---|---|
| 워치콘 | 모든 위협 가설 중 최고 | 워치콘 3 → 2 → 1 |
| 진돗개 | 침투·국지도발 준비(H_INFILTRATION) | 진돗개 셋 → 둘 → 하나 |
| 두루미 | 항공 도발 준비(H_AIR) | 미발령 → 경계 → 발령 |

- 격상은 즉시, 하향은 낮은 조건이 `downgrade_hold_days` 지속될 때 한 단계씩 (관측 공백에 의한 흔들림 방지)
- 상위 단계 점수는 넘었으나 분석 신뢰도 부족 → **정보 수집 강화** 건의
- 결정적 징후 조합(예: 도하장비+야전 의무시설, 침투 흔적)은 확률과 무관하게 해당 단계 이상 건의
- 워치콘 1 + 신뢰도 상 → **데프콘 검토 필요** 신호 (데프콘 자체는 건의하지 않음)
- API: `/assess` 응답의 `readiness`, `GET /readiness?area_id=&as_of=` (DB 평가 이력 기반)
- 시나리오 YAML의 `truth.readiness`(정답 태세 일정)와 비교: 일치율·과잉/미달 일수·변동 횟수·격상 시점 차이
- 시나리오 7종: 위 5종 + 침투 도발(`infiltration`), 항공 도발(`air_provocation`)

## 테스트

```bash
pip install -r requirements.txt
python -m pytest -q tests
```

## 구성요소 출처 (중국계 모델·SW 배제)

| 구성요소 | 개발 주체 | 라이선스 |
|---|---|---|
| gpt-oss-120b / 20b | OpenAI (미국) | Apache 2.0 |
| multilingual-e5-large-instruct | Microsoft (미국) | MIT |
| vLLM | vLLM 프로젝트 (미국 UC Berkeley 기원) | Apache 2.0 |
| Qdrant | Qdrant (독일) | Apache 2.0 |
| Neo4j Community | Neo4j (스웨덴·미국) | GPLv3 |
| PostgreSQL / PostGIS | 국제 커뮤니티 | PostgreSQL / GPLv2 |
| Tesseract | 오픈소스 (Google 후원) | Apache 2.0 |
| FastAPI / Streamlit | 미국·유럽 | MIT / Apache 2.0 |

- 모델 추가 시 모델 카드의 `base_model`까지 확인 (중국 모델 파생 여부)
- Llama 계열은 사용 정책상 군사 용도 제한으로 제외
- 배포 전 `syft` 등으로 SBOM 생성 후 개발 주체 국가·라이선스 열 관리

## 남은 작업 (TODO)

- [ ] 지도 화면: 오프라인 배경지도(PMTiles) + MapLibre
- [ ] 위협 점수에 임박성(시간 근접도) 계수 반영
- [ ] 지표 간 상관(예: 집결↔보급) 처리 — 베이지안 네트워크 도입
- [ ] 피드백 기반 LR·가중치 보정 스크립트, KPI(일치율·오경보율·브라이어 점수) 산출
- [ ] 보고서 초안 docx 출력
- [ ] 공개 사례(2021–22 러시아군 국경 집결) 재현 평가 데이터셋
