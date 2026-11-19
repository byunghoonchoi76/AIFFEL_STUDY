"""시나리오 결과 → 분석보고서(docx). 본문은 분석관에게 주는 보고서, 부록은 정답 대비 평가."""
from __future__ import annotations

from pathlib import Path

GRADE_LINES = [(0.25, "주의"), (0.50, "경계"), (0.75, "심각")]
EN = {"공격 준비": "Attack prep", "대규모 훈련": "Large exercise"}


def _korean_font():
    from matplotlib import font_manager

    for name in ("NanumGothic", "Noto Sans CJK KR", "Noto Sans KR", "Malgun Gothic", "WenQuanYi Zen Hei"):
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            return name
    return None


def threat_chart(series: list[dict], path: Path, title: str) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    kr = _korean_font()
    if kr:
        plt.rcParams["font.family"] = kr
    plt.rcParams["axes.unicode_minus"] = False
    days = [s["day"] for s in series]
    fig, ax = plt.subplots(figsize=(7.2, 3.2), dpi=150)
    for name in series[0]["probabilities"]:
        ax.plot(days, [s["probabilities"][name] for s in series], marker="o", ms=3, lw=1.8,
                label=name if kr else EN.get(name, name))
    for y, g in GRADE_LINES:
        ax.axhline(y, color="#999", lw=0.6, ls="--")
        ax.text(days[0], y + 0.01, g if kr else {"주의": "Caution", "경계": "Alert", "심각": "Severe"}[g],
                fontsize=7, color="#666")
    ax.set_ylim(0, 1)
    ax.set_xlabel("D-day 기준 일자" if kr else "Day relative to D-day")
    ax.set_ylabel("가설 확률" if kr else "Hypothesis probability")
    ax.set_title(title if kr else "Threat probability trend", fontsize=10)
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def build_report(result: dict, out_path: str | Path) -> Path:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    F = "맑은 고딕"
    out_path = Path(out_path)
    sc, series, final, ev, prep = (result[k] for k in ("scenario", "series", "final", "evaluation", "prepare"))
    narrative = final.get("narrative") or {}

    d = Document()
    for s in d.sections:
        s.left_margin = s.right_margin = Cm(2)
        s.top_margin = s.bottom_margin = Cm(1.8)
    st = d.styles["Normal"]
    st.font.name, st.font.size = F, Pt(10)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), F)

    def run(p, t, size=10, bold=False, color=None):
        r = p.add_run(t)
        r.font.size, r.bold, r.font.name = Pt(size), bold, F
        r._element.rPr.rFonts.set(qn("w:eastAsia"), F)
        if color:
            r.font.color.rgb = RGBColor(*color)

    def para(t, size=10, bold=False, align=None, after=4, color=None):
        p = d.add_paragraph()
        run(p, t, size, bold, color)
        p.paragraph_format.space_after = Pt(after)
        if align is not None:
            p.alignment = align
        return p

    def h(t):
        para(t, 12.5, True, after=4, color=(0x1F, 0x3A, 0x68))

    def bullet(t):
        p = d.add_paragraph(style="List Bullet")
        run(p, t, 9.5)
        p.paragraph_format.space_after = Pt(1)

    def table(hdr, rows, widths):
        t = d.add_table(rows=1, cols=len(hdr))
        t.style, t.autofit = "Table Grid", False
        for i, x in enumerate(hdr):
            c = t.rows[0].cells[i]
            c.text = ""
            run(c.paragraphs[0], x, 9, True)
            sh = OxmlElement("w:shd")
            sh.set(qn("w:val"), "clear"); sh.set(qn("w:color"), "auto"); sh.set(qn("w:fill"), "D9E2F3")
            c._tc.get_or_add_tcPr().append(sh)
        for row in rows:
            cs = t.add_row().cells
            for i, x in enumerate(row):
                cs[i].text = ""
                run(cs[i].paragraphs[0], "" if x is None else str(x), 8.5)
        for r in t.rows:
            for i, w in enumerate(widths):
                r.cells[i].width = Cm(w)
        d.add_paragraph().paragraph_format.space_after = Pt(2)

    # ── 표지 ──
    para(f"징후·위협도 분석보고서 — {sc['area_id']}", 16, True, WD_ALIGN_PARAGRAPH.CENTER, 2)
    para(f"합성 시나리오 시험 · 평가 기간 D{series[0]['day']:+d} ~ D{series[-1]['day']:+d} · 기준일 {series[-1]['as_of'][:10]}",
         9.5, False, WD_ALIGN_PARAGRAPH.CENTER, 2, (0x66, 0x66, 0x66))
    para("※ 본 보고서는 가상 부대·가상 지역의 합성 데이터로 자동 생성된 시험 보고서임", 8.5, False,
         WD_ALIGN_PARAGRAPH.CENTER, 10, (0xC0, 0x00, 0x00))

    # ── 1. 종합 판단 ──
    h("1. 종합 판단")
    probs = series[-1]["probabilities"]
    table(["위협 등급", "최상위 위협 가설", "가설별 확률", "분석 신뢰도"],
          [[final["grade"], final["top_hypothesis"], " / ".join(f"{k} {v:.0%}" for k, v in probs.items()), final["confidence"]]],
          [2.6, 3.4, 7.5, 3.5])
    if narrative.get("summary"):
        para(narrative["summary"]["text"], 10.5, True, after=6)

    # ── 2. 위협도 추이 ──
    h("2. 위협도 추이")
    chart = threat_chart(series, out_path.with_suffix(".png"), f"{sc['area_id']} 위협 가설 확률 추이")
    d.add_picture(str(chart), width=Cm(16.5))
    table(["일자", "등급", *probs.keys(), "식별 징후 수"],
          [[f"D{s['day']:+d}", s["grade"], *[f"{s['probabilities'][k]:.0%}" for k in probs], s["n_indicators"]] for s in series],
          [2.0, 2.0] + [3.5] * len(probs) + [3.0])

    # ── 3. 부대별 징후 ──
    h("3. 부대별 활동 변화 (부대별 평시 기준선)")
    ch_rows = [[c["equipment_type"], c.get("unit_name") or "미식별", c.get("echelon") or "-",
                f"{c['baseline_level']:g} → {c['recent_level']}", (f"{c['ratio']}배" if c.get("ratio") else "신규 출현"),
                c.get("dist_km")] for c in final.get("changes", []) if c["delta"] > 0]
    if ch_rows:
        table(["장비", "식별 부대", "제대", "평시 → 최근(대)", "평시 대비", "주둔지 거리(km)"], ch_rows, [2.4, 4.6, 1.6, 3.0, 2.2, 2.6])
    else:
        para("평시 대비 증가가 식별된 부대 활동 없음", 9.5)

    # ── 4. 지표별 해설 ──
    h("4. 징후별 분석")
    for s in narrative.get("sentences", []):
        bullet(s["text"])
    if not narrative.get("sentences"):
        para("식별된 징후 없음", 9.5)

    # ── 5. 근거 목록 ──
    h("5. 근거 목록")
    table(["징후", "출처", "등급", "내용"],
          [[e["indicator_id"], e["source_type"], f"{e['reliability']}{e['credibility']}", (e.get("quote") or "")[:110]]
           for e in final.get("evidence", [])], [3.0, 1.6, 1.2, 11.2])

    # ── 6. 분석관 검토 ──
    h("6. 분석관 검토")
    table(["검토 항목", "의견"], [["판단 동의 여부", "□ 동의  □ 수정  □ 반려"], ["수정 등급", ""], ["추가 의견", "\n\n"],
                                   ["검토자 / 일시", ""]], [4.0, 13.0])

    # ── 부록: 정답 대비 평가 ──
    d.add_page_break()
    h("부록. 정답 대비 평가 (시험용, 분석관 배포본에서 제외)")
    rows = [["시나리오", sc["name"]], ["정답 의도", sc["truth"]["intent"]], ["정답 가설", ev["truth"]],
            ["최종 최고 확률 가설", f"{ev['final_most_probable']} ({'정답' if ev['final_correct'] else '오답'})"],
            ["최종 등급 / 신뢰도", f"{ev['final_grade']} / {ev['final_confidence']}"],
            ["공격 가설 최대 확률", f"{ev['max_attack_probability']:.0%}"]]
    if "warning_lead_days" in ev:
        rows.append(["경보(경계 이상) 최초 일자", f"D{ev['first_warning_day']:+d} (D일 {ev['warning_lead_days']}일 전)"
                     if ev["first_warning_day"] is not None else "경보 없음 (미탐)"])
    if "false_alarm_count" in ev:
        rows.append(["오경보(경계 이상) 일수", f"{ev['false_alarm_count']}회 " + (str([f"D{x:+d}" for x in ev["false_alarm_days"]]) if ev["false_alarm_days"] else "")])
    idn = ev.get("identification")
    if idn:
        rows.append(["표적 → 부대 식별 정확도", f"{idn['accuracy']:.1%} (정답 {idn['correct']} / 오식별 {idn['wrong']} / 미식별 {idn['unidentified']}, "
                                         f"최근 7일 실제 표적 {idn['events']}건)" + (f" · 오식별 예: {idn['errors'][0]}" if idn["errors"] else "")])
    rows += [["투입 데이터", f"표적 {prep['targets']}건, 첩보 {prep['reports']}건(오정보 {prep['false_reports']}건 포함), "
                          f"전투서열 {prep['orbat'].get('unit_rows')}개 부대"],
             ["평가 설정", f"D일 {sc['d_day'][:10]}, {sc['step']}일 간격, 난수 seed {sc['seed']}"]]
    table(["항목", "결과"], rows, [5.0, 12.0])
    d.save(out_path)
    return out_path
