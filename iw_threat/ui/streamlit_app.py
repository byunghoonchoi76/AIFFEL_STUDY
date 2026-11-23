"""분석관 화면 — '무엇을 근거로 어떻게 판단했는지'를 단계별로 보여주는 데 초점.
데이터 원천: (1) API 실시간 평가, (2) 결과 파일 열기(합성 시나리오 result.json — 오프라인 시연)."""
import csv
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import httpx
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.taxonomy import name_ko  # noqa: E402
from ui import viz

TRACK_KO = {'watchcon': '워치콘(정보감시태세)', 'defcon': '데프콘(방어준비태세)', 'jindogae': '진돗개(대침투)', 'durumi': '두루미(대공)'}  # noqa: E402

API = os.environ.get("API_URL", "http://localhost:8080")
st.set_page_config(page_title="징후·위협도 분석", layout="wide", page_icon="🛰")
st.markdown("""<style>
.card{border-radius:10px;padding:12px 14px;color:#fff;margin-bottom:6px}
.card .t{font-size:13px;opacity:.9}.card .v{font-size:26px;font-weight:700;line-height:1.2}.card .s{font-size:12px;opacity:.9}
.lite{background:#F4F6F7;color:#1f2937;border:1px solid #D5D8DC}
.step{border:1px solid #D5D8DC;border-radius:8px;padding:8px 10px;background:#FBFCFC;height:100%}
.step b{font-size:15px}.step .n{font-size:22px;font-weight:700;color:#1F3A68}
.why{background:#FEF9E7;border-left:4px solid #D4AC0D;padding:8px 12px;border-radius:4px;margin:6px 0}
.defcon{background:#C0392B;color:#fff;padding:8px 12px;border-radius:6px;font-weight:700}
</style>""", unsafe_allow_html=True)


# ───────────────────────── 데이터 불러오기 ─────────────────────────
def api_get(path, **params):
    r = httpx.get(f"{API}{path}", params=params, timeout=120)
    r.raise_for_status()
    return r.json()


def load_result_file(raw: bytes, base: Path | None = None) -> dict:
    """합성 시나리오 result.json → 화면 데이터 (평가 응답 + 추이 + 지도용 표적)."""
    res = json.loads(raw)
    data = {"assess": res["final"], "history": [{"as_of": s["as_of"], "probabilities": s["probabilities"]} for s in res["series"]],
            "evaluation": res.get("evaluation"), "scenario": res.get("scenario"), "points": [], "garrisons": []}
    if base and (base / "targets.json").exists():
        last = res["series"][-1]["day"]
        data["points"] = [{**t, "unit_name": None} for t in json.loads((base / "targets.json").read_text(encoding="utf-8"))
                          if last - 7 < t["day"] <= last]
    if base and (base / "orbat_records.csv").exists():
        from app.orbat.identify import radius_for
        rows = list(csv.DictReader(io.StringIO((base / "orbat_records.csv").read_text(encoding="utf-8-sig"))))
        data["garrisons"] = [{"name": r["unit_raw"], "echelon": r["echelon"], "lat": float(r["lat"]), "lon": float(r["lon"]),
                              "radius_m": radius_for(r["echelon"] or None, 1)} for r in rows if r.get("lat")]
    return data


with st.sidebar:
    st.header("🛰 징후·위협도 분석")
    mode = st.radio("데이터", ["실시간 평가(API)", "결과 파일 열기"], index=1 if os.environ.get("DEMO_RESULT") else 0)
    if mode == "실시간 평가(API)":
        area = st.text_input("지역 ID", "AREA-01")
        as_of = st.text_input("기준 일시", datetime.now().astimezone().replace(microsecond=0).isoformat())
        explain = st.checkbox("해설 생성(LLM)", True)
        if st.button("평가 실행", type="primary", use_container_width=True):
            with st.spinner("분석 중… (표적 변화 → 징후 매칭 → 위협도 → 해설 → 태세 판단 조건)"):
                a = httpx.post(f"{API}/assess", json={"area_id": area, "as_of": as_of, "explain": explain}, timeout=900).json()
                d = {"assess": a, "history": [], "points": [], "garrisons": []}
                try:
                    d["history"] = api_get("/assessments/history", area_id=area, as_of=as_of)
                    d["points"] = api_get("/targets/recent", area_id=area, as_of=as_of)
                    d["garrisons"] = api_get("/orbat/garrisons")
                except Exception as e:  # noqa: BLE001
                    st.warning(f"부가 정보 조회 실패: {e}")
                st.session_state["data"] = d
    else:
        demo = os.environ.get("DEMO_RESULT")
        up = st.file_uploader("result.json (합성 시나리오)", type="json")
        if up:
            st.session_state["data"] = load_result_file(up.getvalue())
        elif demo and "data" not in st.session_state:
            p = Path(demo)
            st.session_state["data"] = load_result_file(p.read_bytes(), p.parent)
    st.caption("위협도는 분석 보조 결과(최종 판단은 분석관). 태세 판단·건의는 합참·연합사, 결정은 국방부장관·통수권자 재가 사항 — 정보사는 태세를 건의하지 않고 조건 부합 정보만 제공")

data = st.session_state.get("data")
if not data or "hypotheses" not in data.get("assess", {}):
    st.info("왼쪽에서 평가를 실행하거나 결과 파일을 여세요.")
    st.stop()

A = data["assess"]
hyps = A["hypotheses"]
top = hyps[0]
N = A.get("narrative") or {}
evidence = A.get("evidence", [])
rd = (A.get("readiness") or {}).get("current") or {}
if not rd and data.get("evaluation", {}).get("readiness"):
    rd = {k: {"level_name": v["series"][-1]["name"], "action": v["series"][-1]["action"], "reasons": v["series"][-1]["reasons"],
              "note": v["series"][-1]["note"], "defcon_review": v["series"][-1]["defcon"]}
          for k, v in data["evaluation"]["readiness"]["tracks"].items()}

tabs = st.tabs(["🧭 판단 요약", "🔎 판단 근거", "📈 변화 분석", "🗺 상황도", "📊 추이", "📄 근거 원문", "⚙ 입력·피드백"])

# ───────────────────────── 1. 판단 요약 ─────────────────────────
with tabs[0]:
    c = st.columns(4)
    gcol = viz.GRADE_COLOR.get(A["grade"], "#555")
    c[0].markdown(f"<div class='card' style='background:{gcol}'><div class='t'>위협 등급</div><div class='v'>{A['grade']}</div>"
                  f"<div class='s'>위협 점수 {A['threat_score']:.0%} = 확률 × 영향도</div></div>", unsafe_allow_html=True)
    c[1].markdown(f"<div class='card lite'><div class='t'>가장 위험한 가설</div><div class='v'>{A['top_hypothesis']}</div>"
                  f"<div class='s'>확률 {top['probability']:.0%} (사전 {top['prior']:.0%})</div></div>", unsafe_allow_html=True)
    types = sorted({t for cc in top["contributions"] if cc["delta_logit"] > 0 for t in cc["source_types"]})
    c[2].markdown(f"<div class='card lite'><div class='t'>분석 신뢰도</div><div class='v'>{A['confidence']}</div>"
                  f"<div class='s'>독립 출처 {len(types)}종: {' · '.join(viz.SOURCE_KO.get(t, t) for t in types) or '-'}</div></div>",
                  unsafe_allow_html=True)
    n_pos = sum(cc["delta_logit"] > 0 for cc in top["contributions"])
    n_neg = sum(cc["delta_logit"] < 0 for cc in top["contributions"])
    c[3].markdown(f"<div class='card lite'><div class='t'>식별 징후</div><div class='v'>{n_pos}개</div>"
                  f"<div class='s'>반대 징후 {n_neg}개 · 출처 간 모순 {len(A.get('conflicts', []))}건</div></div>", unsafe_allow_html=True)

    if N.get("summary"):
        st.markdown(f"<div class='why'><b>종합 판단</b> — {N['summary']['text']}</div>", unsafe_allow_html=True)

    if A.get("briefing"):
        st.markdown("<div class='why'><b>지휘관 보고 (3줄)</b><br>" + "<br>".join(A["briefing"]) + "</div>", unsafe_allow_html=True)
    if A.get("patterns"):
        st.dataframe(pd.DataFrame([{"과거 사례": p_["name"], "유사도": f"{p_['similarity']:.0%}", "일치 징후": ", ".join(p_["matched"]) or "-",
                                    "미식별 징후": ", ".join(p_["missing"]) or "-"} for p_ in A["patterns"]]),
                     use_container_width=True, hide_index=True)
        st.caption("과거 사례 유사도 = 사례별 징후 조합(가중치)과 현재 징후 강도의 코사인 유사도 × 일치 비율 (LLM 미사용 계산)")

    if rd:
        st.subheader("태세 — 합참·연합사 발령 태세(참고 조건) vs 정보 상황")
        IP = A.get("issued_posture") or {}
        cols = st.columns(len(rd))
        for col, (tid, r) in zip(cols, rd.items()):
            if not r:
                continue
            color = "#C0392B" if r["action"] == "상향 조건" else "#1F3A68"
            ip = IP.get(tid) or ({"level_name": r.get("issued_name"), "issuer": r.get("issuer")} if r.get("issued_name") else None)
            issued = f"{ip['level_name']} ({ip['issuer']})" if ip else "미입력"
            col.markdown(f"<div class='card lite'><div class='t'>{TRACK_KO.get(tid, tid)}</div>"
                         f"<div class='s'>발령 태세: <b>{issued}</b></div>"
                         f"<div class='v' style='color:{color}'>{r['level_name']}</div><div class='s'>정보 상황 조건 · {r.get('vs_issued') or ''}</div>"
                         f"<div class='s'>{'; '.join(r['reasons']) or r.get('note') or '상위 조건 미충족'}</div></div>",
                         unsafe_allow_html=True)
        st.caption("발령 태세는 합참·연합사가 판단·발령한 값을 입력받아 평가의 참고 조건으로 적용(상급 판단 근거). "
                   "아래 단계는 현재 정보 상황이 부합하는 조건(예시 기준)으로, 발령 태세와의 차이를 정보 판단 자료로 제공 — 태세 건의 아님")

    CR = A.get("commander_requirements") or {}
    if CR.get("pirs"):
        st.subheader("지휘관 정보요구 — 합참의장·연합사령관 PIR / CCIR")
        for al in CR.get("ccir_alerts", []):
            st.markdown(f"<div class='defcon'>📣 CCIR 보고 조건 충족 — {al['commander']} {al['id']}: {al['text']} ({al['basis']})</div>",
                        unsafe_allow_html=True)
        scol = {"확인": "🔴", "징후 있음": "🟠", "약한 징후": "🟡", "미식별": "⚪"}
        st.dataframe(pd.DataFrame([{"지휘관": p_["commander"], "PIR": p_["id"], "질문": p_["question"],
                                    "답변 상태": f"{scol.get(p_['status'], '')} {p_['status']}", "근거 징후": ", ".join(p_["evidence"]) or "-",
                                    "관련 표적 활동": ", ".join(p_["targets"]) or "-"} for p_ in CR["pirs"]]),
                     use_container_width=True, hide_index=True)
        acts = CR.get("key_target_activity", [])
        if acts:
            st.markdown("**합참·연합사 표적목록 — 핵심표적·고가치표적 활동**")
            st.dataframe(pd.DataFrame([{"표적": a_["name"], "구분": a_["category"], "관리": a_["owner"],
                                        "활동": "; ".join(f"{h['equipment']} {h['baseline']:g}→{h['recent']} ({h['basis']})" for h in a_["hits"]),
                                        "연결 PIR": ", ".join(a_["pir_ids"])} for a_ in acts]), use_container_width=True, hide_index=True)
        st.caption("PIR·CCIR·표적목록은 예시(가상) — 실증 시 실증기관 제공 자료로 교체(폐쇄망 적재). 표적 활동은 위협도 근거(핵심표적 LR 3, 고가치표적 LR 2)로 반영")

    TR = A.get("terrain") or {}
    if TR.get("available"):
        st.subheader("지형(도심지)·아군 중요시설 취약성")
        vcol = {"상": "🔴", "중": "🟠", "하": "🟢"}
        st.dataframe(pd.DataFrame([{"시설": f_["name"], "구분": f"{f_['kind']} {f_.get('grade') or ''}급", "취약도": f"{vcol[f_['vulnerability']]} {f_['vulnerability']}",
                                    "적 화력 사거리 내": f_["fires_in_range"], "풍하": "예" if f_["downwind"] else "-",
                                    "지상 도달(직접/우회, 일)": f"{f_['advance']['direct_days']} / {f_['advance']['bypass_days']}" if f_["advance"] else "-",
                                    "도심": f_["urban"] or "-", "판단": " · ".join(f_["notes"]) or "-"} for f_ in TR["facilities"]]),
                     use_container_width=True, hide_index=True)
        r_ = TR["advance_rates"]
        st.caption(f"진출 속도 예시(km/일): 개활 {r_['open']} · 산악 {r_['mountain']} · 도심 {r_['urban']} — 북한군은 도심 직접 진입을 회피·우회한다고 가정"
                   "(도시 진입=늪). 도심 내 시설은 지상 진입이 늦은 대신 화력·화학·특작 위협을 우선 고려. 시설·지형은 가상 예시")

    WX = A.get("weather") or {}
    if WX.get("available"):
        st.subheader("기상이 작전에 미치는 영향")
        wcol = {"양호": "#1E8449", "제한": "#D68910", "불가": "#C0392B"}
        cols = st.columns(len(WX["effects"]))
        for col, e in zip(cols, WX["effects"]):
            col.markdown(f"<div class='card lite'><div class='t'>{e['name']}</div>"
                         f"<div class='v' style='color:{wcol[e['level']]}'>{e['level']}</div><div class='s'>{e['reason']}</div></div>",
                         unsafe_allow_html=True)
        for f in WX.get("findings", []):
            st.markdown(f"<div class='why'><b>적 작전 여건</b> — {f['text']}</div>", unsafe_allow_html=True)
        for c_ in WX.get("collection_limits", []):
            st.warning(f"아군 수집 제한: {c_}")
        if WX.get("outlook"):
            st.dataframe(pd.DataFrame([{"시각": o["valid_at"][:10], **{e["name"]: e["level"] for e in o["effects"]}}
                                       for o in WX["outlook"]]), use_container_width=True, hide_index=True)
        st.caption(f"기준 {WX['valid_at'][:16].replace('T', ' ')} ({'관측' if WX.get('kind') == 'obs' else '예보'}) · 판단 기준은 config/weather_effects.yaml (예시값) · 기상 근거는 여건 근거로 분석 신뢰도의 독립 출처 수에는 미산입")

    st.subheader("판단 과정")
    s = st.columns(5)
    changes = A.get("changes", [])
    steps = [("① 관측", f"{len(data.get('points', [])) or sum(len(c_['refs']) for c_ in changes)}건", "영상 판독 표적·첩보 문서"),
             ("② 변화 감지", f"{sum(c_['delta'] > 0 for c_ in changes)}건", {'unit': '부대별 평시 대비', 'area': '지역 평시 대비'}.get(A.get('baseline_mode'), '평시 대비')),
             ("③ 징후 식별", f"{n_pos}개", f"반대 {n_neg} · 원문대조 탈락 {A.get('rejected_llm_hits', 0)}"),
             ("④ 위협도", f"{top['probability']:.0%}", f"{A['top_hypothesis']} · 신뢰도 {A['confidence']}"),
             ("⑤ 태세 조건", (rd.get('watchcon') or {}).get('level_name', A['grade']), "조건 부합 단계·보고서 초안")]
    for col, (t, n, d) in zip(s, steps):
        col.markdown(f"<div class='step'><b>{t}</b><div class='n'>{n}</div><small>{d}</small></div>", unsafe_allow_html=True)
    st.plotly_chart(viz.probability_bars(hyps), use_container_width=True)

# ───────────────────────── 2. 판단 근거 ─────────────────────────
with tabs[1]:
    st.markdown("<div class='why'>확률 계산식: <b>logit(확률) = logit(사전확률) + Σ 징후 강도 × ln(우도비)</b> · "
                "징후 강도 = 1 − Π(1 − 출처 유형별 최대 가중치) · 근거 가중치 = 출처등급 가중 × 시의성 감쇠 × 신호 배율</div>",
                unsafe_allow_html=True)
    hname = st.selectbox("가설", [h["name"] for h in hyps])
    H = next(h for h in hyps if h["name"] == hname)
    st.plotly_chart(viz.evidence_flow(H), use_container_width=True)
    st.plotly_chart(viz.waterfall(H), use_container_width=True)
    if A.get("lr_matrix"):
        st.plotly_chart(viz.lr_heatmap(A["lr_matrix"], hyps), use_container_width=True)

    st.subheader("징후별 근거 사슬 — 관측 사실 → 가중치 → 징후 강도 → 확률 기여")
    for cc in H["contributions"]:
        sign = "🔺" if cc["delta_logit"] > 0 else "🔻"
        head = (f"{sign} {cc['name']} — 강도 {cc['strength']:.2f} × ln(우도비 {cc['lr']:g}) = 기여 {cc['delta_logit']:+.2f}"
                + (f"  · 반대 증거로 ×{cc['discount']:.2f}" if cc.get("discount", 1) < 1 else ""))
        with st.expander(head):
            det = cc.get("details") or []
            if det:
                df = pd.DataFrame([{"출처": viz.SOURCE_KO.get(d["source_type"], d["source_type"]), "등급": d["grade"],
                                    "등급 가중": d["grade_weight"], "경과(일)": d["age_days"], "시의성 감쇠": d["decay"],
                                    "신호 배율": d["scale"], "최종 가중치": d["weight"], "근거": d["quote"]} for d in det])
                st.dataframe(df, use_container_width=True, hide_index=True)
                st.caption(f"시의성 감쇠 = exp(−경과일 ÷ {det[0]['tau_days']:g}일) · 신호 배율 0.5 = 약한 신호 · "
                           "같은 출처는 최댓값만, 다른 출처 유형은 독립 확인으로 결합")
            else:
                st.write("근거 상세 없음 (이전 버전 결과)")

# ───────────────────────── 3. 변화 분석 ─────────────────────────
with tabs[2]:
    judged = {}
    for e in evidence:
        if e["ref"].startswith("targets:"):
            eq = e["ref"].split(":")[1]
            judged[eq + str(e.get("unit_key"))] = "약" if e.get("scale", 1) < 1 else "강"
    ch = [{**c_, "equipment_label": name_ko(c_["equipment_type"])} for c_ in changes]
    if ch:
        st.plotly_chart(viz.change_chart(ch, judged), use_container_width=True)
        st.dataframe(pd.DataFrame([{"부대": c_.get("unit_name") or "미식별", "장비": c_["equipment_label"],
                                    "평시(중앙값)": c_["baseline_level"], "판독 잡음": c_.get("baseline_sd"),
                                    "최근 최대": c_["recent_level"], "증가": c_["delta"], "배수": f"{c_['ratio']}배" if c_.get("ratio") else "신규",
                                    "z": str(c_["zscore"]) if c_.get("zscore") is not None else "-", "판정": {"강": "징후 충족", "약": "약한 신호"}.get(
                                        judged.get(c_["equipment_type"] + str(c_.get("unit_key"))), "미충족")}
                                   for c_ in ch if c_["delta"] > 0]), use_container_width=True, hide_index=True)
        st.caption("판정 기준: 제대별 임계값(또는 평시 대비 2배) 이상 + 판독 잡음의 3배 이상 → 징후, 절반 이상·2배·2회 관측 → 약한 신호")
    if A.get("unit_estimates"):
        st.subheader("전투서열로 식별되지 않은 표적 군집 — 장비 구성으로 부대 유형 추정")
        st.dataframe(pd.DataFrame([{"추정": f"{e['type_ko']}({e['echelon'] or '-'})", "구성 유사도": e["similarity"],
                                    "편제 대비": f"{e['strength_ratio']:.0%}", "장비": ", ".join(f"{name_ko(k)} {v}" for k, v in
                                                                                         sorted(e["equipment"].items(), key=lambda kv: -kv[1])[:5])}
                                   for e in A["unit_estimates"]]), use_container_width=True, hide_index=True)

# ───────────────────────── 4. 상황도 ─────────────────────────
with tabs[3]:
    if data.get("points") or data.get("garrisons"):
        st.plotly_chart(viz.situation_map(data.get("points", []), data.get("garrisons", []), A.get("unit_estimates", [])),
                        use_container_width=True)
        st.caption("배경지도 없이 좌표 평면에 표시(오프라인). 실증 시 부대 보유 지도 체계와 연동")
    else:
        st.info("표적 좌표가 없습니다.")

# ───────────────────────── 5. 추이 ─────────────────────────
with tabs[4]:
    st.plotly_chart(viz.trend_chart(data.get("history", [])), use_container_width=True)
    ev = data.get("evaluation")
    if ev and ev.get("readiness"):
        rows = []
        for tid, tr in ev["readiness"]["tracks"].items():
            for x in tr["series"]:
                if x["action"] != "유지":
                    rows.append({"태세": tr["name"], "일자": f"D{x['day']:+d}", "조건 단계": x["name"], "변동": x["action"],
                                 "근거": "; ".join(x["reasons"])})
        if rows:
            st.subheader("태세 판단 조건 변동 이력")
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ───────────────────────── 6. 근거 원문 ─────────────────────────
with tabs[5]:
    if A.get("conflicts"):
        st.subheader("⚠ 출처 간 모순 · 반대 징후")
        st.dataframe(pd.DataFrame([{"반대 징후": c_["counter_name"], "강도": c_["strength"], "출처": "/".join(c_["source_types"]),
                                    "신뢰가 깎인 징후": ", ".join(f"{k} ×{v:.2f}" for k, v in c_["discounted"].items()) or "-"}
                                   for c_ in A["conflicts"]]), use_container_width=True, hide_index=True)
    st.subheader("근거 목록")
    src = st.multiselect("출처", sorted({e["source_type"] for e in evidence}), default=sorted({e["source_type"] for e in evidence}))
    names = {cc["indicator_id"]: cc["name"] for h in hyps for cc in h["contributions"]}
    st.dataframe(pd.DataFrame([{"징후": names.get(e["indicator_id"], e["indicator_id"]), "출처": viz.SOURCE_KO.get(e["source_type"]),
                                "등급": f"{e['reliability']}{e['credibility']}", "관측": str(e["observed_at"])[:16],
                                "약한 신호": "예" if e.get("scale", 1) < 1 else "", "근거 문구": e.get("quote")}
                               for e in evidence if e["source_type"] in src]), use_container_width=True, hide_index=True)
    st.caption(f"LLM 분류 중 원문에 없는 인용으로 탈락: {A.get('rejected_llm_hits', 0)}건 · 근거 미확인 해설 제거: {N.get('dropped', 0)}건")
    if N.get("sentences"):
        st.subheader("징후별 해설 (보고서 초안)")
        for s_ in N["sentences"]:
            st.markdown(f"- {s_['text']}")

# ───────────────────────── 7. 입력·피드백 ─────────────────────────
with tabs[6]:
    st.caption("API 모드에서 사용")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**표적 CSV 등록** — area_id, observed_at, equipment_type, count, lon, lat, image_ref")
        upc = st.file_uploader("표적 CSV", type="csv", key="targets")
        if upc and st.button("표적 등록"):
            df = pd.read_csv(upc)
            df = df.astype(object).where(pd.notna(df), None)
            st.write(httpx.post(f"{API}/targets", json=df.to_dict(orient="records"), timeout=60).json())
        st.markdown("**전투서열 적재** — 검토된 records.csv")
        upo = st.file_uploader("records.csv", type="csv", key="orbat")
        if upo and st.button("전투서열 적재"):
            st.write(httpx.post(f"{API}/orbat/load", files={"file": (upo.name, upo.getvalue())}, data={"mode": "auto"}, timeout=300).json())
    with c2:
        st.markdown("**첩보 문서 수집**")
        f = st.file_uploader("문서 (PDF·HWPX·TXT)", type=["pdf", "hwpx", "txt", "md"])
        t1, t2 = st.columns(2)
        title, source_name = t1.text_input("제목"), t2.text_input("출처명")
        t3, t4, t5 = st.columns(3)
        source_type = t3.selectbox("출처 유형", ["OSINT", "HUMINT", "SIGINT", "DOCTRINE", "IMINT"])
        rel, cred = t4.selectbox("신뢰도", list("ABCDEF"), index=2), t5.selectbox("신빙성", [1, 2, 3, 4, 5, 6], index=2)
        area_doc = st.text_input("지역 ID (교리는 공란)", key="area_doc")
        reported = st.text_input("보고 일시", datetime.now().astimezone().replace(microsecond=0).isoformat())
        if f and title and source_name and st.button("수집"):
            form = {"title": title, "source_name": source_name, "source_type": source_type, "reliability": rel,
                    "credibility": cred, "reported_at": reported}
            if area_doc:
                form["area_id"] = area_doc
            st.write(httpx.post(f"{API}/ingest", data=form, files={"file": (f.name, f.getvalue())}, timeout=600).json())
        st.markdown("**분석관 피드백**")
        aid = st.number_input("평가 ID", min_value=1, step=1, value=int(A.get("assessment_id") or 1))
        analyst = st.text_input("분석관")
        decision = st.radio("판단", ["approve", "modify", "reject"], horizontal=True,
                            format_func={"approve": "동의", "modify": "수정", "reject": "반려"}.get)
        override = st.selectbox("등급 수정", ["", "관심", "주의", "경계", "심각"])
        comment = st.text_area("의견")
        if analyst and st.button("피드백 저장"):
            st.write(httpx.post(f"{API}/assessments/{int(aid)}/feedback", json={"analyst": analyst, "decision": decision,
                                "grade_override": override or None, "comment": comment}, timeout=30).json())
