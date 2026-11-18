"""분석관 화면 (MVP). 지도는 2차에서 오프라인 배경지도(PMTiles)+MapLibre로 교체 예정."""
import os
from datetime import datetime

import httpx
import pandas as pd
import streamlit as st

API = os.environ.get("API_URL", "http://localhost:8080")
st.set_page_config(page_title="징후·위협도 분석", layout="wide")
st.title("징후·위협도 AI 추론 (MVP)")

tab_assess, tab_targets, tab_docs, tab_fb = st.tabs(["평가", "표적 입력", "문서 수집", "분석관 피드백"])

with tab_targets:
    st.caption("CSV 열: area_id, observed_at, equipment_type, count, lon, lat, image_ref")
    up = st.file_uploader("표적 CSV", type="csv", key="targets")
    if up and st.button("표적 등록"):
        df = pd.read_csv(up)
        df = df.astype(object).where(pd.notna(df), None)
        r = httpx.post(f"{API}/targets", json=df.to_dict(orient="records"), timeout=60)
        st.write(r.json())

with tab_docs:
    f = st.file_uploader("문서 (PDF·HWPX·TXT)", type=["pdf", "hwpx", "txt", "md"])
    c1, c2, c3 = st.columns(3)
    title = c1.text_input("제목")
    source_name = c2.text_input("출처명")
    source_type = c3.selectbox("출처 유형", ["DOCTRINE", "OSINT", "HUMINT", "SIGINT", "IMINT"])
    c4, c5, c6 = st.columns(3)
    rel = c4.selectbox("출처 신뢰도", list("ABCDEF"), index=2)
    cred = c5.selectbox("정보 신빙성", [1, 2, 3, 4, 5, 6], index=2)
    area = c6.text_input("지역 ID (교리는 공란)")
    reported = st.text_input("보고 일시 (ISO, 예: 2026-11-10T09:00:00)")
    extract = st.checkbox("LLM 개체·관계 추출(지식그래프)")
    if f and title and source_name and st.button("수집"):
        data = {"title": title, "source_name": source_name, "source_type": source_type, "reliability": rel,
                "credibility": cred, "extract_graph": extract}
        if area:
            data["area_id"] = area
        if reported:
            data["reported_at"] = reported
        r = httpx.post(f"{API}/ingest", data=data, files={"file": (f.name, f.getvalue())}, timeout=600)
        st.write(r.json())

with tab_assess:
    c1, c2 = st.columns(2)
    area_id = c1.text_input("지역 ID", "AREA-01")
    as_of = c2.text_input("기준 일시", datetime.now().replace(microsecond=0).isoformat())
    if st.button("평가 실행", type="primary"):
        with st.spinner("분석 중..."):
            r = httpx.post(f"{API}/assess", json={"area_id": area_id, "as_of": as_of}, timeout=900)
        res = r.json()
        st.session_state["last"] = res
    res = st.session_state.get("last")
    if res and "grade" in res:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("위협 등급", res["grade"])
        m2.metric("최상위 가설", res["top_hypothesis"])
        m3.metric("확률", f"{res['probability']:.0%}")
        m4.metric("분석 신뢰도", res["confidence"])
        st.subheader("가설별 결과")
        st.dataframe(pd.DataFrame([{k: h[k] for k in ("name", "prior", "probability", "impact", "threat")}
                                   for h in res["hypotheses"]]))
        st.subheader("지표별 기여도 (최상위 가설)")
        st.dataframe(pd.DataFrame(res["hypotheses"][0]["contributions"]))
        st.subheader("영상 변화")
        st.dataframe(pd.DataFrame(res["changes"]))
        st.subheader("근거 기반 해설 (보고서 초안)")
        for s in res["narrative"].get("sentences", []):
            st.markdown(f"- {s['text']}  \n  <small>근거: {', '.join(s['citations'])}</small>", unsafe_allow_html=True)
        st.caption(f"근거 미확인으로 제거된 문장 {res['narrative'].get('dropped', 0)}개 · "
                   f"원문 대조 실패 LLM 매칭 {res.get('rejected_llm_hits', 0)}건 · 평가 ID {res['assessment_id']}")
        with st.expander("근거 목록"):
            st.dataframe(pd.DataFrame(res["evidence"]))

with tab_fb:
    aid = st.number_input("평가 ID", min_value=1, step=1)
    analyst = st.text_input("분석관")
    decision = st.radio("판단", ["approve", "modify", "reject"], horizontal=True)
    override = st.selectbox("등급 수정(선택)", ["", "관심", "주의", "경계", "심각"])
    comment = st.text_area("의견")
    if analyst and st.button("피드백 저장"):
        r = httpx.post(f"{API}/assessments/{int(aid)}/feedback",
                       json={"analyst": analyst, "decision": decision,
                             "grade_override": override or None, "comment": comment}, timeout=30)
        st.write(r.json())
