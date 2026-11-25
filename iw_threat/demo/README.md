# 화면 시연용 결과 (합성 시나리오, 가상 부대·좌표)

```bash
DEMO_RESULT=demo/rus_analog_nk/result.json streamlit run ui/streamlit_app.py --server.port 8501
```

- rus_analog_nk: 러시아 사례 기반 공격 준비 (D일 평가)
- local_fire_2015: 2015.8 국지 포격 패턴
- attack_prep: 공격 준비 기본 시나리오
- deception: 기만(위장 집결) — 모형·위장 장비 등 반대 증거로 공격 가능성 하향 (판단 근거 탭에서 가설 "공격 준비" 선택 시 파란 띠)

## 읽기 전용 공유 (외부 시연)

```bash
bash scripts/share_demo.sh        # 포트 8502, Cloudflare 임시 링크 생성
NO_TUNNEL=1 bash scripts/share_demo.sh   # 이 장비에서만 확인
```

SHARE_MODE=1 이면 합성 결과만 열람(시나리오 선택), 입력·피드백 탭·파일 업로드·API 호출 없음. 화면은 127.0.0.1에만 열림.
