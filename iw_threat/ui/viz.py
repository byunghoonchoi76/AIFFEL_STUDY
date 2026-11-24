"""판단 근거 가시화 (plotly) — 화면용 그림 생성 함수. 입력은 /assess 응답(dict)."""
from __future__ import annotations

import math

import plotly.graph_objects as go

GRADE_COLOR = {"관심": "#2E86C1", "주의": "#D4AC0D", "경계": "#E67E22", "심각": "#C0392B"}
GRADE_LINES = [(0.25, "주의"), (0.50, "경계"), (0.75, "심각")]
SOURCE_KO = {"IMINT": "영상", "OSINT": "공개첩보", "HUMINT": "인간정보", "SIGINT": "신호정보", "DOCTRINE": "교리", "METOC": "기상"}
FONT = dict(family="Malgun Gothic, NanumGothic, Noto Sans CJK KR, sans-serif", size=13)


def _sig(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def _logit(p: float) -> float:
    return math.log(p / (1 - p))


def waterfall(hyp: dict) -> go.Figure:
    """사전확률에서 출발해 징후 하나하나가 확률을 얼마나 올리고(빨강) 내렸는지(파랑) 보여주는 폭포 그래프.
    막대 길이는 로그오즈 기여(강도 × ln 우도비), 막대 위 숫자는 그 징후까지 반영한 누적 확률."""
    contribs = sorted(hyp["contributions"], key=lambda c: -c["delta_logit"])
    x = ["사전 확률"] + [c["name"] for c in contribs] + ["최종 확률"]
    lo = _logit(hyp["prior"])
    y = [lo] + [c["delta_logit"] for c in contribs] + [0]
    cum, texts = lo, [f"{hyp['prior']:.0%}"]
    for c in contribs:
        cum += c["delta_logit"]
        texts.append(f"{c['delta_logit']:+.2f} → {_sig(cum):.0%}")
    texts.append(f"{hyp['probability']:.0%}")
    fig = go.Figure(go.Waterfall(
        x=x, y=y, measure=["absolute"] + ["relative"] * len(contribs) + ["total"], text=texts, textposition="outside",
        increasing={"marker": {"color": "#C0392B"}}, decreasing={"marker": {"color": "#2E86C1"}},
        totals={"marker": {"color": "#34495E"}}, connector={"line": {"color": "#bbb", "width": 1}}))
    lv = [lo]
    for c in contribs:
        lv.append(lv[-1] + c["delta_logit"])
    pad = (max(lv + [0]) - min(lv + [0])) * 0.12 + 0.3
    fig.update_yaxes(range=[min(lv + [0]) - pad, max(lv + [0]) + pad])
    fig.update_layout(font=FONT, height=440, margin=dict(l=10, r=10, t=50, b=10), showlegend=False,
                      title=f"‘{hyp['name']}’ 확률이 만들어진 과정 (빨강: 높임 · 파랑: 낮춤, 세로축: 로그오즈)",
                      yaxis_title="로그오즈")
    return fig


def lr_heatmap(lr_matrix: dict, hypotheses: list[dict]) -> go.Figure:
    """경쟁가설 비교: 식별된 징후가 각 가설을 지지(빨강)하는지 반박(파랑)하는지 — 값은 우도비(LR)."""
    names = [h["name"] for h in hypotheses]
    rows = list(lr_matrix.values())
    z = [[math.log(r["lr"].get(n, 1.0)) for n in names] for r in rows]
    text = [[f"{r['lr'].get(n, 1.0):g}" for n in names] for r in rows]
    fig = go.Figure(go.Heatmap(z=z, x=names, y=[r["name"] for r in rows], text=text, texttemplate="%{text}",
                               colorscale=[[0, "#2E86C1"], [0.5, "#FFFFFF"], [1, "#C0392B"]], zmid=0, showscale=False))
    fig.update_layout(font=FONT, height=max(260, 34 * len(rows) + 90), margin=dict(l=10, r=10, t=40, b=10),
                      title="경쟁가설 비교표 — 칸의 숫자는 우도비(1보다 크면 지지, 작으면 반박, 1이면 무관)")
    fig.update_yaxes(autorange="reversed")
    return fig


def probability_bars(hypotheses: list[dict]) -> go.Figure:
    hs = sorted(hypotheses, key=lambda h: h["probability"])
    fig = go.Figure(go.Bar(x=[h["probability"] for h in hs], y=[h["name"] for h in hs], orientation="h",
                           text=[f"{h['probability']:.0%} (사전 {h['prior']:.0%})" for h in hs], textposition="auto",
                           marker_color=["#C0392B" if h["probability"] >= 0.5 else "#95A5A6" for h in hs]))
    fig.add_vline(x=0.5, line_dash="dot", line_color="#888")
    fig.update_layout(font=FONT, height=60 + 42 * len(hs), margin=dict(l=10, r=10, t=30, b=10),
                      xaxis=dict(range=[0, 1], tickformat=".0%"), title="가설별 확률")
    return fig


def change_chart(changes: list[dict], judged: dict[str, str]) -> go.Figure:
    """부대·장비별 평시(중앙값 ± 판독 잡음) vs 최근 최대. judged: 'ref' → 강/약 판정."""
    rows = [c for c in changes if c["delta"] > 0][:20]
    labels = [f"{c.get('unit_name') or '미식별'} · {c['equipment_label']}" for c in rows]
    fig = go.Figure()
    fig.add_bar(name="평시 수준", x=[c["baseline_level"] for c in rows], y=labels, orientation="h", marker_color="#AAB7B8",
                error_x=dict(type="data", array=[c.get("baseline_sd") or 0 for c in rows], color="#555"))
    colors = [{"강": "#C0392B", "약": "#E67E22"}.get(judged.get(c["equipment_type"] + str(c.get("unit_key")), ""), "#5D6D7E")
              for c in rows]
    fig.add_bar(name="최근 최대", x=[c["recent_level"] for c in rows], y=labels, orientation="h", marker_color=colors,
                text=[(f"{c['ratio']}배" if c.get("ratio") else "신규") + (f" · z {c['zscore']}" if c.get("zscore") is not None else "")
                      for c in rows], textposition="outside")
    fig.update_layout(font=FONT, barmode="group", height=80 + 46 * len(rows), margin=dict(l=10, r=40, t=40, b=10),
                      title="부대별 평시 대비 변화 (빨강: 징후 충족 · 주황: 약한 신호 · 회색: 미충족, 오차막대: 판독 잡음)",
                      legend=dict(orientation="h", y=1.02, x=0))
    fig.update_yaxes(autorange="reversed")
    return fig


def trend_chart(history: list[dict]) -> go.Figure:
    fig = go.Figure()
    if not history:
        return fig
    xs = [h["as_of"][:16].replace("T", " ") for h in history]
    for name in history[-1]["probabilities"]:
        fig.add_scatter(x=xs, y=[h["probabilities"].get(name, 0) for h in history], mode="lines+markers", name=name)
    for y, g in GRADE_LINES:
        fig.add_hline(y=y, line_dash="dot", line_color=GRADE_COLOR[g], annotation_text=g, annotation_position="left")
    fig.update_layout(font=FONT, height=380, margin=dict(l=10, r=10, t=40, b=10), yaxis=dict(range=[0, 1], tickformat=".0%"),
                      title="가설 확률 추이 (점선: 등급 경계 — 위협 점수 = 확률 × 영향도)", legend=dict(orientation="h", y=-0.2))
    return fig


def situation_map(points: list[dict], garrisons: list[dict], estimates: list[dict]) -> go.Figure:
    """배경지도 없이(오프라인) 좌표 평면에 주둔지 반경·표적·미식별 군집 추정을 표시."""
    fig = go.Figure()
    for g in garrisons:
        r_lat = g["radius_m"] / 111320
        r_lon = r_lat / max(math.cos(math.radians(g["lat"])), 0.1)
        fig.add_shape(type="circle", x0=g["lon"] - r_lon, x1=g["lon"] + r_lon, y0=g["lat"] - r_lat, y1=g["lat"] + r_lat,
                      line=dict(color="#7D3C98", width=1, dash="dot"), fillcolor="rgba(125,60,152,0.04)")
    if garrisons:
        fig.add_scatter(x=[g["lon"] for g in garrisons], y=[g["lat"] for g in garrisons], mode="markers+text",
                        marker=dict(symbol="square", size=9, color="#7D3C98"), text=[g["name"] for g in garrisons],
                        textposition="top center", name="주둔지(점선: 활동 반경)",
                        hovertext=[f"{g['name']} ({g.get('echelon') or '-'}) 반경 {g['radius_m'] / 1000:.1f}km" for g in garrisons])
    units = sorted({p.get("unit_name") or "미식별" for p in points})
    for u in units:
        ps = [p for p in points if (p.get("unit_name") or "미식별") == u]
        fig.add_scatter(x=[p["lon"] for p in ps], y=[p["lat"] for p in ps], mode="markers", name=f"표적: {u}",
                        marker=dict(size=[min(22, 5 + p["count"] ** 0.5) for p in ps], opacity=0.55,
                                    color="#95A5A6" if u == "미식별" else None),
                        hovertext=[f"{p['equipment_type']} {p['count']}대 · {p['observed_at'][:10]} · {u}" for p in ps])
    if estimates:
        fig.add_scatter(x=[e["lon"] for e in estimates], y=[e["lat"] for e in estimates], mode="markers+text",
                        marker=dict(symbol="star", size=18, color="#E67E22"), name="미식별 군집 추정",
                        text=[f"{e['type_ko']} 추정" for e in estimates], textposition="bottom center")
    fig.update_layout(font=FONT, height=560, margin=dict(l=10, r=10, t=40, b=10), title="상황도 — 표적·주둔지·부대 식별",
                      xaxis_title="경도", yaxis_title="위도", legend=dict(orientation="h", y=-0.12))
    fig.update_yaxes(scaleanchor="x", scaleratio=1.27)
    return fig


def evidence_flow(hyp: dict) -> go.Figure:
    """근거 흐름도: 출처(등급) → 징후 → 가설. 띠 굵기 = 근거 가중치(왼쪽) / 확률 기여 로그오즈(오른쪽).
    반대 증거(확률을 낮춘 징후)는 파란 띠로 '확률 낮춤' 노드에 연결."""
    labels: list[str] = []
    color: list[str] = []

    def node(name: str, c: str) -> int:
        if name not in labels:
            labels.append(name)
            color.append(c)
        return labels.index(name)

    src, dst, val, lc, hover = [], [], [], [], []
    h_up = node(f"{hyp['name']} {hyp['probability']:.0%}", "#34495E")
    h_dn = None
    for c in sorted(hyp["contributions"], key=lambda c: -abs(c["delta_logit"])):
        up = c["delta_logit"] > 0
        ind = node(c["name"], "#C0392B" if up else "#2E86C1")
        for d in c.get("details") or []:
            s = node(f"{SOURCE_KO.get(d['source_type'], d['source_type'])} {d['grade']}", "#7F8C8D")
            src.append(s); dst.append(ind); val.append(max(d["weight"], 0.02))
            lc.append("rgba(127,140,141,0.35)")
            hover.append(d["quote"][:90])
        if up:
            src.append(ind); dst.append(h_up)
            lc.append("rgba(192,57,43,0.35)")
        else:
            h_dn = h_dn if h_dn is not None else node("확률 낮춤(반대 증거)", "#2E86C1")
            src.append(ind); dst.append(h_dn)
            lc.append("rgba(46,134,193,0.35)")
        val.append(abs(c["delta_logit"]))
        hover.append(f"강도 {c['strength']:.2f} × ln(LR {c['lr']:g}) = {c['delta_logit']:+.2f}")
    fig = go.Figure(go.Sankey(
        arrangement="snap", textfont=dict(size=13, color="#222", shadow="none"),
        node=dict(label=labels, color=color, pad=14, thickness=16, line=dict(width=0)),
        link=dict(source=src, target=dst, value=val, color=lc, customdata=hover,
                  hovertemplate="%{customdata}<extra></extra>")))
    fig.update_layout(font=FONT, height=max(360, 30 * len(labels)), margin=dict(l=10, r=10, t=40, b=10),
                      title="근거 흐름 — 출처(등급) → 징후 → 가설 (띠 굵기: 근거 가중치 / 확률 기여)")
    return fig


def sar_activity(phases: list[dict]) -> go.Figure:
    """위성영상 단계별 SAR 강반사 비율(집결지 활성도) 막대."""
    xs = [p["phase"] for p in phases]
    ys = [p["sar_bright"] * 100 for p in phases]
    fig = go.Figure(go.Bar(x=xs, y=ys, text=[f"{int(y+0.5)}%" for y in ys], textposition="outside",
                           marker_color=["#C0392B" if y >= 10 else "#7F8C8D" for y in ys]))
    fig.update_layout(font=FONT, height=260, margin=dict(l=10, r=10, t=40, b=10), title="집결지 SAR 강반사 비율 (차량·구조물 군집 지표)",
                      yaxis=dict(title="%", range=[0, max(ys) * 1.3 if ys else 1]), plot_bgcolor="white")
    return fig
