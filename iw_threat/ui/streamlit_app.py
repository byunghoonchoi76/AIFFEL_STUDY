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

TRACK_KO = {'watchcon': '워치콘(정보감시태세)', 'defcon': '데프콘(방어준비태세)', 'jindogae': '진돗개(대침투)', 'durumi': '두루미(대공)', 'infocon': '인포콘(정보작전방호)'}  # noqa: E402

API = os.environ.get("API_URL", "http://localhost:8080")
# 읽기 전용 공유 모드: 외부 시연용. 합성 결과 파일만 열람, 업로드·API 호출·피드백 저장 없음
SHARE = os.environ.get("SHARE_MODE", "").strip().lower() in ("1", "true", "yes", "on")
DEMO_DIR = Path(os.environ.get("DEMO_DIR", Path(__file__).resolve().parents[1] / "demo"))
st.set_page_config(page_title="징후·위협도 분석", layout="wide", page_icon="🛰")
st.markdown("""<style>
:root{--ink:#18202c;--sub:#5b6575;--line:#e3e6eb;--soft:#f6f7f9;--navy:#1f3a68;--up:#c0392b;--down:#2e86c1}
.block-container{padding-top:3.2rem;max-width:1280px}
h3{font-size:1.05rem!important;font-weight:700!important;margin:1.4rem 0 .5rem!important;color:var(--ink)}
.sec{font-size:.78rem;letter-spacing:.06em;color:var(--sub);font-weight:600;margin:1.6rem 0 .5rem;text-transform:uppercase}
/* BLUF 판단 띠 */
.bluf{margin-bottom:12px;display:grid;grid-template-columns:auto 1fr;gap:18px;align-items:stretch;border:1px solid var(--line);border-radius:12px;overflow:hidden;background:#fff}
.bluf .g{color:#fff;padding:16px 22px;display:flex;flex-direction:column;justify-content:center;min-width:150px}
.bluf .g .k{font-size:12px;opacity:.85}.bluf .g .v{font-size:30px;font-weight:800;line-height:1.1}.bluf .g .m{font-size:12px;opacity:.9;margin-top:2px}
.bluf .b{padding:14px 18px 14px 0;display:flex;flex-direction:column;gap:6px;color:var(--ink)}
.bluf .h{font-size:20px;font-weight:700}.bluf .h span{color:var(--sub);font-weight:500;font-size:15px}
.bluf ol{margin:0;padding-left:1.1rem;font-size:14.5px;line-height:1.6;color:var(--ink)}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{font-size:12px;padding:3px 9px;border-radius:999px;background:var(--soft);border:1px solid var(--line);color:var(--ink);white-space:nowrap}
.chip.alert{background:#fdecea;border-color:#f5c6c0;color:#8e2a1f;font-weight:600}
/* 핵심 지표 */
.kpi{border:1px solid var(--line);border-radius:10px;padding:10px 14px;background:#fff;color:var(--ink);height:100%}
.kpi .t{font-size:12px;color:var(--sub)}.kpi .v{font-size:22px;font-weight:700;line-height:1.3}.kpi .s{font-size:12px;color:var(--sub)}
/* 징후 기여 막대 */
.drv{display:grid;grid-template-columns:minmax(0,1fr) 46%;gap:10px;align-items:center;padding:5px 0;border-bottom:1px solid var(--soft);font-size:13.5px;color:var(--ink)}
.drv .n{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.drv .n small{color:var(--sub)}
.drv .bar{height:10px;border-radius:5px;background:var(--soft);position:relative}
.drv .bar i{position:absolute;top:0;bottom:0;border-radius:5px}
/* 태세 */
.card{border-radius:10px;padding:10px 12px;margin-bottom:4px;container-type:inline-size;border:1px solid var(--line);background:#fff;color:var(--ink);height:100%}
.card .t{font-size:12px;color:var(--sub);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.card .v{font-weight:800;line-height:1.25;white-space:nowrap}
.card .s,.card .s1{font-size:12px;color:var(--sub);line-height:1.35}
.card.up{border-color:#f0b5ad;background:#fdf3f2}
/* 판단 과정 */
.flow{display:flex;gap:0;align-items:stretch;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:#fff}
.flow>div{flex:1;padding:8px 12px;border-right:1px solid var(--line);color:var(--ink);min-width:0}
.flow>div:last-child{border-right:0}.flow b{font-size:12px;color:var(--sub);font-weight:600}.flow .n{font-size:19px;font-weight:700;color:var(--navy)}
.flow small{color:var(--sub);display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.why{background:var(--soft);color:var(--ink);border-left:3px solid var(--navy);padding:8px 12px;border-radius:4px;margin:6px 0;font-size:14px}
.why *{color:inherit}
.defcon{background:#fdecea;color:#8e2a1f;border:1px solid #f5c6c0;padding:8px 12px;border-radius:8px;font-weight:600;margin:4px 0}
@media (max-width:760px){.bluf{grid-template-columns:1fr}.bluf .b{padding:0 16px 14px}.flow{flex-wrap:wrap}.flow>div{flex:1 1 45%}}
</style>""", unsafe_allow_html=True)



@st.cache_data
def _ind_names() -> dict:
    try:
        import yaml
        cat = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "indicators.yaml").read_text(encoding="utf-8"))
        return {i["id"]: i["name"] for h in cat["hypotheses"] for i in h["indicators"]}
    except Exception:  # noqa: BLE001
        return {}


def _ind_ko(i: str) -> str:
    return _ind_names().get(i, i)

def _fit(text: str, max_px: float, min_px: float = 9.5) -> str:
    """카드 폭(cqi)과 글자 수로 글자 크기를 정해 한 줄에 들어가게 함 (한글 1em, 영문·숫자 약 0.6em으로 추정)."""
    w = sum(1.0 if ord(ch) >= 0x2E80 else 0.6 for ch in str(text)) or 1
    return f"font-size:clamp({min_px}px,{88 / w:.2f}cqi,{max_px}px);white-space:nowrap;overflow:hidden;text-overflow:ellipsis"


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


# 공유 모드 비밀번호 (SHARE_PASSWORD 설정 시): 링크를 알아도 비밀번호 없이는 내용이 보이지 않음
if SHARE and os.environ.get("SHARE_PASSWORD"):
    import hmac
    if not st.session_state.get("share_ok"):
        st.title("🛰 징후·위협도 분석 — 공유 화면")
        pw = st.text_input("비밀번호", type="password", key="share_pw")
        if pw:
            if hmac.compare_digest(pw.encode(), os.environ["SHARE_PASSWORD"].encode()):
                st.session_state["share_ok"] = True
                st.rerun()
            else:
                st.error("비밀번호가 맞지 않습니다.")
        st.stop()


with st.sidebar:
    st.markdown("### 징후·위협도 분석")
    if SHARE:
        st.caption("읽기 전용 공유 화면 · 합성 시나리오(가상 부대·좌표)")
        files = sorted(DEMO_DIR.glob("*/result.json"), key=lambda f: (f.parent.name != "rus_analog_nk", f.parent.name))
        if os.environ.get("DEMO_RESULT"):
            files = [Path(os.environ["DEMO_RESULT"])] + [f for f in files if f.resolve() != Path(os.environ["DEMO_RESULT"]).resolve()]
        names = {f: (json.loads(f.read_text(encoding="utf-8")).get("scenario") or {}).get("name", f.parent.name) for f in files}
        if files:
            pick = st.selectbox("시나리오", files, format_func=names.get)
            if st.session_state.get("share_pick") != str(pick):
                st.session_state["data"] = load_result_file(pick.read_bytes(), pick.parent)
                st.session_state["share_pick"] = str(pick)
        else:
            st.warning(f"시연 결과 파일이 없습니다: {DEMO_DIR}/*/result.json")
        mode = None
    else:
        mode = st.radio("데이터", ["실시간 평가(API)", "결과 파일 열기"], index=1 if os.environ.get("DEMO_RESULT") else 0)
    if mode is None:
        pass
    elif mode == "실시간 평가(API)":
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
    st.divider()
    st.caption("위협도는 분석 보조 결과이며 최종 판단은 분석관. 태세 판단·건의는 합참·연합사 소관 — 정보사는 조건 부합 정보만 제공")

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

# 위성영상 판독 예시 (시나리오에 연결된 공개 영상이 있으면 탭 추가)
IMG_DIR = Path(os.environ.get("IMAGERY_DIR", Path(__file__).resolve().parents[1] / "demo" / "imagery"))
IMG = None
try:
    _img = json.loads((IMG_DIR / "imagery.json").read_text(encoding="utf-8"))
    if (data.get("scenario") or {}).get("id") in _img.get("scenarios", []):
        IMG = _img
except Exception:  # noqa: BLE001
    IMG = None
_names = ["종합 판단", "위성영상", "판단 근거", "상황·변화", "추이", "정보요구·작전여건", "근거 원문"]
if not IMG:
    _names.remove("위성영상")
_names += [] if SHARE else ["입력·피드백"]
TAB = dict(zip(_names, st.tabs(_names)))
tabs = [TAB[n] for n in _names if n != "위성영상"]

# ───────────────────────── 공통 값 ─────────────────────────
changes = A.get("changes", [])
n_pos = sum(cc["delta_logit"] > 0 for cc in top["contributions"])
n_neg = sum(cc["delta_logit"] < 0 for cc in top["contributions"])
counters = sorted({cc["name"] for h_ in hyps for cc in h_["contributions"] if cc["delta_logit"] < 0})
types = sorted({t for cc in top["contributions"] if cc["delta_logit"] > 0 for t in cc["source_types"]})
CR = A.get("commander_requirements") or {}
gcol = viz.GRADE_COLOR.get(A["grade"], "#555")


def drivers_html(contribs: list[dict], limit: int = 6) -> str:
    """징후별 확률 기여를 가로 막대로 (빨강: 높임 · 파랑: 낮춤). 0을 가운데 두고 좌우로 그림."""
    cs = sorted(contribs, key=lambda c: -abs(c["delta_logit"]))[:limit]
    if not cs:
        return "<div class='why'>식별된 징후 없음</div>"
    m = max(abs(c["delta_logit"]) for c in cs) or 1
    rows = []
    for c in cs:
        w = abs(c["delta_logit"]) / m * 50
        up = c["delta_logit"] > 0
        pos = f"left:50%;width:{w:.1f}%;background:var(--up)" if up else f"right:50%;width:{w:.1f}%;background:var(--down)"
        src = "·".join(viz.SOURCE_KO.get(t, t) for t in c["source_types"])
        rows.append(f"<div class='drv' title='{c['name']} · 기여 {c['delta_logit']:+.2f}'><div class='n'>{'▲' if up else '▼'} {c['name']} <small>{src} · 강도 {c['strength']:.2f}</small></div>"
                    f"<div class='bar'><i style='{pos}'></i><i style='left:50%;width:1px;background:#c9ced6'></i></div></div>")
    return "".join(rows)


# ───────────────────────── 1. 종합 판단 ─────────────────────────
with tabs[0]:
    brief = A.get("briefing") or ([N["summary"]["text"]] if N.get("summary") else [])
    brief_items = "".join(f"<li>{b.lstrip('①②③④⑤ ').strip()}</li>" for b in brief)
    alerts = CR.get("ccir_alerts", [])
    chips = "".join(f"<span class='chip alert'>CCIR {a['commander']} {a['id']}</span>" for a in alerts)
    chips += "".join(f"<span class='chip'>반대 징후 · {c}</span>" for c in counters[:3])
    st.markdown(
        f"<div class='bluf'><div class='g' style='background:{gcol}'><div class='k'>위협 등급</div><div class='v'>{A['grade']}</div>"
        f"<div class='m'>위협 점수 {A['threat_score']:.0%}</div></div>"
        f"<div class='b'><div class='h'>{A['top_hypothesis']} <span>가능성 {top['probability']:.0%} · 분석 신뢰도 {A['confidence']}</span></div>"
        f"<ol>{brief_items}</ol>" + (f"<div class='chips'>{chips}</div>" if chips else "") + "</div></div>",
        unsafe_allow_html=True)

    k = st.columns(4)
    kpis = [("가장 위험한 가설", A["top_hypothesis"], f"사전 {top['prior']:.0%} → 현재 {top['probability']:.0%}"),
            ("식별 징후", f"{n_pos}개", f"반대 징후 {len(counters)}개 · 출처 간 모순 {len(A.get('conflicts', []))}건"),
            ("독립 출처", f"{len(types)}종", " · ".join(viz.SOURCE_KO.get(t, t) for t in types) or "-"),
            ("과거 사례 최대 유사도", f"{A['patterns'][0]['similarity']:.0%}" if A.get("patterns") else "-",
             A["patterns"][0]["name"] if A.get("patterns") else "")]
    for col, (t, v, s_) in zip(k, kpis):
        col.markdown(f"<div class='kpi'><div class='t'>{t}</div><div class='v' style='{_fit(v, 22, 13)}'>{v}</div>"
                     f"<div class='s' style='{_fit(s_, 12, 9.5)}'>{s_}</div></div>", unsafe_allow_html=True)

    left, right = st.columns([1.05, 1])
    with left:
        st.markdown("<div class='sec'>판단에 크게 작용한 징후</div>", unsafe_allow_html=True)
        st.markdown(drivers_html(top["contributions"]), unsafe_allow_html=True)
        st.caption("막대 길이 = 그 징후가 확률을 올린(빨강)·내린(파랑) 정도. 자세한 계산은 '판단 근거' 탭")
    with right:
        st.markdown("<div class='sec'>가설별 가능성</div>", unsafe_allow_html=True)
        st.plotly_chart(viz.probability_bars(hyps), use_container_width=True, config={"displayModeBar": False})

    if rd:
        st.markdown("<div class='sec'>태세 — 정보 상황이 부합하는 조건 단계 (발령 태세는 합참·연합사 입력값)</div>", unsafe_allow_html=True)
        IP = A.get("issued_posture") or {}
        cols = st.columns(len(rd))
        for col, (tid, r) in zip(cols, rd.items()):
            if not r:
                continue
            up = r["action"] == "상향 조건"
            ip = IP.get(tid) or ({"level_name": r.get("issued_name"), "issuer": r.get("issuer")} if r.get("issued_name") else None)
            issued = f"발령 {ip['level_name']}" if ip else "발령 미입력"
            reason = "; ".join(r["reasons"]) or r.get("note") or "상위 조건 미충족"
            col.markdown(f"<div class='card{' up' if up else ''}' title='{reason}'>"
                         f"<div class='t'>{TRACK_KO.get(tid, tid)}</div>"
                         f"<div class='v' style='color:{'var(--up)' if up else 'var(--navy)'};{_fit(r['level_name'], 24, 12)}'>{r['level_name']}</div>"
                         f"<div class='s1' style='{_fit(issued, 12)}'>{issued}</div>"
                         f"<div class='s' style='{_fit(reason, 11.5)}'>{reason}</div></div>", unsafe_allow_html=True)
        st.caption("정보사는 태세를 건의하지 않음 — 현재 정보 상황이 부합하는 조건과 발령 태세의 차이를 판단 자료로 제공. 카드에 마우스를 올리면 근거 전체 표시")

    st.markdown("<div class='sec'>판단 과정</div>", unsafe_allow_html=True)
    steps = [("① 관측", f"{len(data.get('points', [])) or sum(len(c_['refs']) for c_ in changes)}건", "영상 판독 표적·첩보 문서"),
             ("② 변화 감지", f"{sum(c_['delta'] > 0 for c_ in changes)}건", {'unit': '부대별 평시 대비', 'area': '지역 평시 대비'}.get(A.get('baseline_mode'), '평시 대비')),
             ("③ 징후 식별", f"{n_pos}개", f"반대 {n_neg} · 원문대조 탈락 {A.get('rejected_llm_hits', 0)}"),
             ("④ 위협도", f"{top['probability']:.0%}", f"{A['top_hypothesis']}"),
             ("⑤ 태세 조건", (rd.get('watchcon') or {}).get('level_name', A['grade']), "조건 부합 단계")]
    st.markdown("<div class='flow'>" + "".join(f"<div><b>{t}</b><div class='n'>{n}</div><small>{d}</small></div>" for t, n, d in steps)
                + "</div>", unsafe_allow_html=True)
    if N.get("summary"):
        with st.expander("종합 판단 문장 (보고서 초안)"):
            st.write(N["summary"]["text"])
    st.caption("위협도는 분석 보조 결과이며 최종 판단은 분석관")

# ───────────────────────── 2. 판단 근거 ─────────────────────────
with tabs[1]:
    _default = max(range(len(hyps)), key=lambda i: (len(hyps[i]["contributions"]), hyps[i]["probability"]))  # 근거가 가장 많은 가설
    hname = st.selectbox("가설 선택", [h["name"] for h in hyps], index=_default,
                         help="반대 증거(파란색)는 그 증거가 낮춘 가설을 고르면 보입니다")
    H = next(h for h in hyps if h["name"] == hname)
    st.markdown(f"<div class='why'><b>{H['name']}</b> — 사전 {H['prior']:.0%}에서 시작해 징후 {sum(c['delta_logit'] > 0 for c in H['contributions'])}개가 높이고 "
                f"{sum(c['delta_logit'] < 0 for c in H['contributions'])}개가 낮춰 <b>{H['probability']:.0%}</b></div>", unsafe_allow_html=True)
    st.markdown("<div class='sec'>근거 흐름 — 첩보 출처 → 징후 → 판단 (띠가 굵을수록 크게 작용)</div>", unsafe_allow_html=True)
    st.plotly_chart(viz.evidence_flow(H), use_container_width=True, config={"displayModeBar": False})
    st.markdown("<div class='sec'>확률이 쌓인 과정</div>", unsafe_allow_html=True)
    st.plotly_chart(viz.waterfall(H), use_container_width=True, config={"displayModeBar": False})

    st.markdown("<div class='sec'>징후별 근거 — 관측 사실 → 가중치 → 강도 → 기여</div>", unsafe_allow_html=True)
    cs_sorted = sorted(H["contributions"], key=lambda c: -abs(c["delta_logit"]))
    st.dataframe(pd.DataFrame([{"": "▲" if c["delta_logit"] > 0 else "▼", "징후": c["name"],
                                "출처": " · ".join(viz.SOURCE_KO.get(t, t) for t in c["source_types"]),
                                "강도": round(c["strength"], 2), "우도비": c["lr"], "기여": round(c["delta_logit"], 2),
                                "반대 증거 할인": f"×{c['discount']:.2f}" if c.get("discount", 1) < 1 else ""} for c in cs_sorted]),
                 use_container_width=True, hide_index=True,
                 column_config={"기여": st.column_config.ProgressColumn("기여", min_value=0.0,
                                                                        max_value=max([abs(c["delta_logit"]) for c in cs_sorted] + [1]),
                                                                        format="%+.2f")} if all(c["delta_logit"] >= 0 for c in cs_sorted) else None)
    if cs_sorted:
        pick_i = st.selectbox("원문 근거 보기", [c["name"] for c in cs_sorted], key="ev_pick")
        cc = next(c for c in cs_sorted if c["name"] == pick_i)
        det = cc.get("details") or []
        if det:
            st.dataframe(pd.DataFrame([{"출처": viz.SOURCE_KO.get(d["source_type"], d["source_type"]), "등급": d["grade"],
                                        "경과(일)": d["age_days"], "등급 가중": d["grade_weight"], "시의성 감쇠": d["decay"],
                                        "신호 배율": d["scale"], "최종 가중치": d["weight"], "근거 문장": d["quote"]} for d in det]),
                         use_container_width=True, hide_index=True)
            st.caption(f"강도 {cc['strength']:.2f} × ln(우도비 {cc['lr']:g}) = 기여 {cc['delta_logit']:+.2f} · "
                       f"시의성 감쇠 = exp(−경과일 ÷ {det[0]['tau_days']:g}일) · 같은 출처는 최댓값만, 다른 출처 유형은 독립 확인으로 결합")
    with st.expander("계산 방법 · 경쟁가설 비교표"):
        st.markdown("- **확률**: logit(확률) = logit(사전확률) + Σ 징후 강도 × ln(우도비)\n"
                    "- **징후 강도**(0~1, 그 징후를 얼마나 믿을 수 있는지) = 1 − Π(1 − 출처 유형별 최대 가중치)\n"
                    "- **근거 가중치** = 출처등급 가중 × 시의성 감쇠 × 신호 배율\n"
                    "- **우도비**: 그 징후가 '가설이 참일 때' 나타날 가능성이 '거짓일 때'보다 몇 배 높은지")
        if A.get("lr_matrix"):
            st.plotly_chart(viz.lr_heatmap(A["lr_matrix"], hyps), use_container_width=True, config={"displayModeBar": False})

# ───────────────────────── 3. 상황·변화 ─────────────────────────
with tabs[2]:
    if data.get("points") or data.get("garrisons"):
        st.markdown("<div class='sec'>상황도 — 표적·주둔지·부대 식별</div>", unsafe_allow_html=True)
        st.plotly_chart(viz.situation_map(data.get("points", []), data.get("garrisons", []), A.get("unit_estimates", [])),
                        use_container_width=True, config={"displayModeBar": False})
        st.caption("배경지도 없이 좌표 평면에 표시(오프라인). 실증 시 부대 보유 지도 체계와 연동")
    judged = {}
    for e in evidence:
        if e["ref"].startswith("targets:"):
            eq = e["ref"].split(":")[1]
            judged[eq + str(e.get("unit_key"))] = "약" if e.get("scale", 1) < 1 else "강"
    ch = [{**c_, "equipment_label": name_ko(c_["equipment_type"])} for c_ in changes]
    if ch:
        st.markdown("<div class='sec'>부대별 평시 대비 변화</div>", unsafe_allow_html=True)
        st.plotly_chart(viz.change_chart(ch, judged), use_container_width=True, config={"displayModeBar": False})
        with st.expander("변화 상세 표 · 판정 기준"):
            st.dataframe(pd.DataFrame([{"부대": c_.get("unit_name") or "미식별", "장비": c_["equipment_label"],
                                        "평시(중앙값)": c_["baseline_level"], "판독 잡음": c_.get("baseline_sd"),
                                        "최근 최대": c_["recent_level"], "증가": c_["delta"], "배수": f"{c_['ratio']}배" if c_.get("ratio") else "신규",
                                        "z": str(c_["zscore"]) if c_.get("zscore") is not None else "-", "판정": {"강": "징후 충족", "약": "약한 신호"}.get(
                                            judged.get(c_["equipment_type"] + str(c_.get("unit_key"))), "미충족")}
                                       for c_ in ch if c_["delta"] > 0]), use_container_width=True, hide_index=True)
            st.caption("판정 기준: 제대별 임계값(또는 평시 대비 2배) 이상 + 판독 잡음의 3배 이상 → 징후, 절반 이상·2배·2회 관측 → 약한 신호")
    if A.get("unit_estimates"):
        st.markdown("<div class='sec'>전투서열로 식별되지 않은 표적 군집 — 장비 구성으로 부대 유형 추정</div>", unsafe_allow_html=True)
        st.dataframe(pd.DataFrame([{"추정": f"{e['type_ko']}({e['echelon'] or '-'})", "구성 유사도": e["similarity"],
                                    "편제 대비": f"{e['strength_ratio']:.0%}", "장비": ", ".join(f"{name_ko(k)} {v}" for k, v in
                                                                                         sorted(e["equipment"].items(), key=lambda kv: -kv[1])[:5])}
                                   for e in A["unit_estimates"]]), use_container_width=True, hide_index=True)

# ───────────────────────── 4. 추이 ─────────────────────────
with tabs[3]:
    st.markdown("<div class='sec'>가설별 가능성 추이</div>", unsafe_allow_html=True)
    st.plotly_chart(viz.trend_chart(data.get("history", [])), use_container_width=True, config={"displayModeBar": False})
    ev = data.get("evaluation")
    if ev and ev.get("readiness"):
        rows = []
        for tid, tr in ev["readiness"]["tracks"].items():
            for x in tr["series"]:
                if x["action"] != "유지":
                    rows.append({"일자": f"D{x['day']:+d}", "태세": tr["name"], "조건 단계": x["name"], "변동": x["action"],
                                 "근거": "; ".join(x["reasons"])})
        if rows:
            st.markdown("<div class='sec'>태세 조건 변동 이력</div>", unsafe_allow_html=True)
            st.dataframe(pd.DataFrame(sorted(rows, key=lambda r: int(r["일자"][1:]))), use_container_width=True, hide_index=True)

# ───────────────────────── 5. 정보요구·작전여건 ─────────────────────────
with tabs[4]:
    if CR.get("pirs"):
        st.markdown("<div class='sec'>지휘관 정보요구 — 합참의장·연합사령관 PIR / CCIR</div>", unsafe_allow_html=True)
        for al in alerts:
            st.markdown(f"<div class='defcon'>CCIR 보고 조건 충족 — {al['commander']} {al['id']}: {al['text']} ({al['basis']})</div>",
                        unsafe_allow_html=True)
        scol = {"확인": "🔴", "징후 있음": "🟠", "약한 징후": "🟡", "미식별": "⚪"}
        st.dataframe(pd.DataFrame([{"지휘관": p_["commander"], "PIR": p_["id"], "질문": p_["question"],
                                    "답변 상태": f"{scol.get(p_['status'], '')} {p_['status']}", "근거 징후": ", ".join(p_["evidence"]) or "-"}
                                   for p_ in CR["pirs"]]), use_container_width=True, hide_index=True)
        acts = CR.get("key_target_activity", [])
        if acts:
            with st.expander(f"핵심표적·고가치표적 활동 {len(acts)}건"):
                st.dataframe(pd.DataFrame([{"표적": a_["name"], "구분": a_["category"], "관리": a_["owner"],
                                            "활동": "; ".join(f"{h['equipment']} {h['baseline']:g}→{h['recent']} ({h['basis']})" for h in a_["hits"]),
                                            "연결 PIR": ", ".join(a_["pir_ids"])} for a_ in acts]), use_container_width=True, hide_index=True)
        st.caption("PIR·CCIR·표적목록은 예시(가상) — 실증 시 실증기관 제공 자료로 교체(폐쇄망 적재)")
    if A.get("patterns"):
        st.markdown("<div class='sec'>과거 사례 비교</div>", unsafe_allow_html=True)
        st.dataframe(pd.DataFrame([{"과거 사례": p_["name"], "유사도": f"{p_['similarity']:.0%}", "일치 징후": ", ".join(_ind_ko(i) for i in p_["matched"]) or "-",
                                    "미식별 징후": ", ".join(_ind_ko(i) for i in p_["missing"]) or "-"} for p_ in A["patterns"]]),
                     use_container_width=True, hide_index=True)
        st.caption("유사도 = 사례별 징후 조합과 현재 징후 강도의 코사인 유사도 × 일치 비율 · '미식별 징후'는 추가 확인이 필요한 항목")
    WX = A.get("weather") or {}
    if WX.get("available"):
        st.markdown("<div class='sec'>기상이 작전에 미치는 영향</div>", unsafe_allow_html=True)
        wcol = {"양호": "#1E8449", "제한": "#D68910", "불가": "#C0392B"}
        effs = WX["effects"]
        cols = [c_ for i in range(0, len(effs), 4) for c_ in st.columns(4)]
        for col, e in zip(cols, effs):
            col.markdown(f"<div class='card'><div class='t'>{e['name']}</div>"
                         f"<div class='v' style='color:{wcol[e['level']]};font-size:20px'>{e['level']}</div><div class='s'>{e['reason']}</div></div>",
                         unsafe_allow_html=True)
        for f in WX.get("findings", []):
            st.markdown(f"<div class='why'><b>적 작전 여건</b> — {f['text']}</div>", unsafe_allow_html=True)
        for c_ in WX.get("collection_limits", []):
            st.markdown(f"<div class='why'><b>아군 관측 제한(분석 반영)</b> — {c_}</div>", unsafe_allow_html=True)
        if WX.get("outlook"):
            with st.expander("기상 전망"):
                st.dataframe(pd.DataFrame([{"시각": o["valid_at"][:10], **{e["name"]: e["level"] for e in o["effects"]}}
                                           for o in WX["outlook"]]), use_container_width=True, hide_index=True)
    TR = A.get("terrain") or {}
    if TR.get("available"):
        st.markdown("<div class='sec'>지형(도심지)·아군 중요시설 취약성</div>", unsafe_allow_html=True)
        vcol = {"상": "🔴", "중": "🟠", "하": "🟢"}
        st.dataframe(pd.DataFrame([{"시설": f_["name"], "구분": f"{f_['kind']} {f_.get('grade') or ''}급", "취약도": f"{vcol[f_['vulnerability']]} {f_['vulnerability']}",
                                    "적 화력 사거리 내": f_["fires_in_range"], "풍하": "예" if f_["downwind"] else "-",
                                    "지상 도달(직접/우회, 일)": f"{f_['advance']['direct_days']} / {f_['advance']['bypass_days']}" if f_["advance"] else "-",
                                    "판단": " · ".join(f_["notes"]) or "-"} for f_ in TR["facilities"]]),
                     use_container_width=True, hide_index=True)
        r_ = TR["advance_rates"]
        st.caption(f"진출 속도 예시(km/일): 개활 {r_['open']} · 산악 {r_['mountain']} · 도심 {r_['urban']} — 도심 직접 진입 회피·우회 가정. 시설·지형은 가상 예시")

# ───────────────────────── 6. 근거 원문 ─────────────────────────
with tabs[5]:
    if A.get("conflicts"):
        st.markdown("<div class='sec'>출처 간 모순 · 반대 징후</div>", unsafe_allow_html=True)
        st.dataframe(pd.DataFrame([{"반대 징후": c_["counter_name"], "강도": c_["strength"], "출처": "/".join(c_["source_types"]),
                                    "신뢰가 깎인 징후": ", ".join(f"{k} ×{v:.2f}" for k, v in c_["discounted"].items()) or "-"}
                                   for c_ in A["conflicts"]]), use_container_width=True, hide_index=True)
    st.markdown("<div class='sec'>근거 목록</div>", unsafe_allow_html=True)
    src = st.multiselect("출처", sorted({e["source_type"] for e in evidence}), default=sorted({e["source_type"] for e in evidence}),
                         format_func=lambda t: viz.SOURCE_KO.get(t, t))
    names = {cc["indicator_id"]: cc["name"] for h in hyps for cc in h["contributions"]}
    st.dataframe(pd.DataFrame([{"징후": names.get(e["indicator_id"], e["indicator_id"]), "출처": viz.SOURCE_KO.get(e["source_type"]),
                                "등급": f"{e['reliability']}{e['credibility']}", "관측": str(e["observed_at"])[:16],
                                "약한 신호": "예" if e.get("scale", 1) < 1 else "", "근거 문구": e.get("quote")}
                               for e in evidence if e["source_type"] in src]), use_container_width=True, hide_index=True)
    st.caption(f"LLM 분류 중 원문에 없는 인용으로 탈락: {A.get('rejected_llm_hits', 0)}건 · 근거 미확인 해설 제거: {N.get('dropped', 0)}건")
    if N.get("sentences"):
        with st.expander("징후별 해설 (보고서 초안)"):
            for s_ in N["sentences"]:
                st.markdown(f"- {s_['text']}")


# ───────────────────────── 7. 입력·피드백 ─────────────────────────
def _input_tab():
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



# ───────────────────────── 위성영상 (공개 영상 판독 예시) ─────────────────────────
if IMG:
    with TAB["위성영상"]:
        st.subheader(IMG.get("title", "위성영상 판독 예시"))
        st.caption("영상 → 판독(표적 종류·수량·위치) → 징후 → 위협도: 시스템은 판독 결과를 입력받아 판단하며, 아래는 각 단계를 실제 공개 영상으로 보여주는 예시")
        ev_ids = {e["indicator_id"] for e in evidence}
        try:
            import yaml
            _cat = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "indicators.yaml").read_text(encoding="utf-8"))
            names = {i["id"]: i["name"] for h in _cat["hypotheses"] for i in h["indicators"]}
        except Exception:  # noqa: BLE001
            names = {}
        rows = [p for p in IMG["phases"] if p.get("sar_bright") is not None]
        if rows:
            st.plotly_chart(viz.sar_activity(rows), use_container_width=True)
        for ph in IMG["phases"]:
            st.markdown(f"#### {ph['phase']}  ·  시나리오 단계: {ph['scenario_phase']}")
            c1, c2, c3 = st.columns([1, 1, 1.1])
            for col, key, label in [(c1, "optical", "광학 (Sentinel-2)"), (c2, "sar", "SAR (Sentinel-1)")]:
                with col:
                    if ph.get(key) and (IMG_DIR / ph[key]).exists():
                        st.image(str(IMG_DIR / ph[key]), use_container_width=True)
                    else:
                        st.info(f"{label}: 해당 시기 영상 없음" + (" (구름)" if key == "optical" else ""))
            with c3:
                st.markdown(f"**판독** — {ph['reading']}")
                if ph.get("sar_bright") is not None:
                    st.markdown(f"**SAR 강반사 비율** — {int(ph['sar_bright']*100+0.5)}% (집결지 내 차량·구조물 군집 지표)")
                if ph.get("indicators"):
                    st.markdown("**연결 징후**")
                    for iid in ph["indicators"]:
                        mark = "✔ 현재 평가 근거에 반영" if iid in ev_ids else "— 현재(최종일) 평가에는 미반영: 시간 경과·해당 단계 아님"
                        st.markdown(f"- {names.get(iid, iid)} ({mark})")
            st.divider()
        st.caption(IMG.get("note", ""))

if not SHARE:
    with tabs[6]:
        _input_tab()
