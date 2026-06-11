"""
🏥 환자 맞춤형 의료 안내 서비스 v2 (영상 통합)
Mini AIFFELthon — AI-Hub 전문 의학지식 데이터 활용

실행:  python app.py
필요:  pip install gradio scikit-learn numpy anthropic pillow

⚠️ 본 서비스는 의료 정보 제공 목적의 AI 보조 도구입니다.
   진단·처방을 대체하지 않으며, 최종 의료 판단은 반드시 전문의에게 받으시기 바랍니다.
"""
import os, json, glob, pickle, random, re, base64, io
from collections import defaultdict
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize
import anthropic
from PIL import Image
import gradio as gr

# ════════════════════════════════════════════════════════════════
# 0. 설정 — API 키 · 경로 · 모델
# ════════════════════════════════════════════════════════════════
# API 키: 환경변수 우선, 없으면 아래 변수 직접 입력
API_KEY = "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"
client  = anthropic.Anthropic(api_key=API_KEY)

MODEL = "claude-haiku-4-5-20251001"

# 데이터 경로 (압축 해제/구축에 필요 — pkl이 이미 있으면 사용 안 함)
DATA_ROOT = r"G:\내 드라이브\의학관련 데이터\download (1)\08.전문_의학지식_데이터\3.개방데이터\1.데이터"
TS_DIR    = os.path.join(DATA_ROOT, 'Training',   '01.원천데이터')
TL_DIR    = os.path.join(DATA_ROOT, 'Training',   '02.라벨링데이터')
VL_DIR    = os.path.join(DATA_ROOT, 'Validation', '02.라벨링데이터')

# pkl 저장 위치
BASE_DIR  = r"G:\내 드라이브\의학관련 데이터\files (1)"

DOMAIN_MAP = {
    1:'외과', 2:'예방의학', 3:'정신건강의학과', 4:'신경과신경외과',
    5:'피부과', 6:'안과', 7:'이비인후과', 8:'비뇨의학과',
    9:'방사선종양학과', 10:'병리과', 11:'마취통증의학과', 12:'의료법규', 13:'기타'
}

# ════════════════════════════════════════════════════════════════
# 1. RAG 인덱스 · Few-shot 풀 로드 (없으면 자동 구축)
# ════════════════════════════════════════════════════════════════
INDEX_PATH   = os.path.join(BASE_DIR, 'tfidf_index.pkl')
FEWSHOT_PATH = os.path.join(BASE_DIR, 'fewshot_pool.pkl')

def build_index():
    """tfidf_index.pkl 구축 (최초 1회)"""
    import zipfile
    TS_TARGETS = ['TS_국문_의학_교과서','TS_국문_학술_논문_및_저널','TS_국문_학회_가이드라인','TS_국문_온라인_의료_정보_제공_사이트']
    TS_EXTRACT_DIR = os.path.join(BASE_DIR, 'ts_data'); os.makedirs(TS_EXTRACT_DIR, exist_ok=True)
    for name in TS_TARGETS:
        out_dir = os.path.join(TS_EXTRACT_DIR, name)
        if os.path.isdir(out_dir) and glob.glob(f'{out_dir}/**/*.json', recursive=True): continue
        os.makedirs(out_dir, exist_ok=True)
        try:
            with zipfile.ZipFile(os.path.join(TS_DIR, f'{name}.zip.part0'),'r') as z: z.extractall(out_dir)
        except Exception as e: print(f"  ❌ {name}: {e}")
    CATS = {'TS_국문_의학_교과서':159,'TS_국문_학술_논문_및_저널':970,'TS_국문_학회_가이드라인':1298,'TS_국문_온라인_의료_정보_제공_사이트':3000}
    docs = []
    for cat, limit in CATS.items():
        for fp in glob.glob(f'{os.path.join(TS_EXTRACT_DIR,cat)}/**/*.json', recursive=True)[:limit]:
            with open(fp,'r',encoding='utf-8-sig') as f:
                try:
                    d = json.load(f); content = d.get('content','').strip()
                    if len(content)<100: continue
                    for i in range(0,min(len(content),2000),400):
                        chunk = content[i:i+500]
                        if len(chunk)>80: docs.append({'text':chunk,'cat':cat,'domain':d.get('domain')})
                except: pass
    vec = TfidfVectorizer(max_features=30000, ngram_range=(1,2), min_df=2, sublinear_tf=True)
    mat = normalize(vec.fit_transform([d['text'] for d in docs]), norm='l2')
    pickle.dump({'vectorizer':vec,'matrix':mat,'docs':docs}, open(INDEX_PATH,'wb'))
    print(f"✅ 인덱스 구축 완료 | shape: {mat.shape}")

def build_fewshot():
    """fewshot_pool.pkl 구축 (최초 1회)"""
    import zipfile
    for prefix, src_dir, base in [('TL',TL_DIR,os.path.join(BASE_DIR,'tl_data'))]:
        os.makedirs(base, exist_ok=True)
        for zp in glob.glob(os.path.join(src_dir, f'{prefix}_*.zip.part0')):
            name = os.path.basename(zp).replace('.zip.part0',''); out_dir = os.path.join(base, name.replace(f'{prefix}_',''))
            if os.path.isdir(out_dir) and glob.glob(f'{out_dir}/**/*.json',recursive=True): continue
            os.makedirs(out_dir, exist_ok=True)
            try:
                with zipfile.ZipFile(zp,'r') as z: z.extractall(out_dir)
            except Exception as e: print(f"  ❌ {name}: {e}")
    pool = defaultdict(list)
    for fp in glob.glob(os.path.join(BASE_DIR,'tl_data','**','*.json'), recursive=True):
        with open(fp,'r',encoding='utf-8-sig') as f:
            try:
                d = json.load(f)
                if d.get('q_type')==3:
                    dom = d.get('domain',13)
                    pool[dom].append({'question':d['question'],'answer':d['answer'],'dept':DOMAIN_MAP.get(dom,'기타')})
            except: pass
    pickle.dump(dict(pool), open(FEWSHOT_PATH,'wb'))
    print(f"✅ Few-shot 구축 완료 | {sum(len(v) for v in pool.values())}건")

# 로드 (없으면 구축)
if not os.path.exists(INDEX_PATH):
    print("⏳ tfidf_index.pkl 없음 — 구축 시작 (최초 1회, 수 분 소요)...")
    build_index()
if not os.path.exists(FEWSHOT_PATH):
    print("⏳ fewshot_pool.pkl 없음 — 구축 시작...")
    build_fewshot()

_idx = pickle.load(open(INDEX_PATH,'rb'))
VECTORIZER, MATRIX, DOCS = _idx['vectorizer'], _idx['matrix'], _idx['docs']
FEWSHOT = pickle.load(open(FEWSHOT_PATH,'rb'))
print(f"✅ 인덱스 로드 — 청크 {len(DOCS)}개 / Few-shot {sum(len(v) for v in FEWSHOT.values())}건")


# ════════════════════════════════════════════════════════════════
# 2. 권역별 병원 DB
# ════════════════════════════════════════════════════════════════
# 셀 3: 권역별 병원 DB (매번 실행)
REGIONS = {'수도권':['서울','경기','인천'],'충청권':['대전','충남','충북','세종'],'호남권':['광주','전남','전북'],
           '대구경북권':['대구','경북'],'부산경남권':['부산','울산','경남'],'강원권':['강원'],'제주권':['제주']}
TRACK_A_DB = {
    '수도권':[{'name':'서울 열린내과의원','addr':'서울 강남구','depts':['내과','외과','신경과'],'type':'의원','rating':4.5},
            {'name':'경기중앙병원','addr':'경기 수원시','depts':['정형외과','내과','피부과'],'type':'병원','rating':4.3},
            {'name':'인천현대의원','addr':'인천 남동구','depts':['내과','이비인후과','안과'],'type':'의원','rating':4.2},
            {'name':'강남연세병원','addr':'서울 서초구','depts':['외과','정형외과','비뇨의학과'],'type':'병원','rating':4.4}],
    '충청권':[{'name':'대전중앙내과','addr':'대전 서구','depts':['내과','신경과','정신건강의학과'],'type':'의원','rating':4.3},
            {'name':'충청종합병원','addr':'충남 천안시','depts':['외과','내과','이비인후과'],'type':'병원','rating':4.1},
            {'name':'세종으뜸의원','addr':'세종시','depts':['내과','피부과','안과'],'type':'의원','rating':4.2}],
    '호남권':[{'name':'광주제일의원','addr':'광주 북구','depts':['내과','외과','이비인후과'],'type':'의원','rating':4.2},
            {'name':'전남중앙병원','addr':'전남 목포시','depts':['신경과','내과','정형외과'],'type':'병원','rating':4.0},
            {'name':'전북현대의원','addr':'전북 전주시','depts':['내과','피부과','비뇨의학과'],'type':'의원','rating':4.3}],
    '대구경북권':[{'name':'대구중앙의원','addr':'대구 중구','depts':['내과','외과','이비인후과'],'type':'의원','rating':4.4},
              {'name':'경북종합병원','addr':'경북 포항시','depts':['정형외과','신경과','내과'],'type':'병원','rating':4.1}],
    '부산경남권':[{'name':'부산해운대의원','addr':'부산 해운대구','depts':['내과','피부과','이비인후과'],'type':'의원','rating':4.3},
              {'name':'경남중앙병원','addr':'경남 창원시','depts':['외과','신경과','비뇨의학과'],'type':'병원','rating':4.2},
              {'name':'울산현대의원','addr':'울산 남구','depts':['내과','정형외과','안과'],'type':'의원','rating':4.1}],
    '강원권':[{'name':'강원중앙의원','addr':'강원 춘천시','depts':['내과','외과','이비인후과'],'type':'의원','rating':4.2},
            {'name':'원주세브란스병원분원','addr':'강원 원주시','depts':['신경과','내과','외과'],'type':'병원','rating':4.4}],
    '제주권':[{'name':'제주중앙의원','addr':'제주시','depts':['내과','피부과','외과'],'type':'의원','rating':4.3},
            {'name':'서귀포의료원','addr':'서귀포시','depts':['내과','외과','신경과'],'type':'병원','rating':4.1}],
}
TRACK_B_DB = {
    '수도권':[{'name':'서울대학교병원','addr':'서울 종로구','specialty':['신경외과','심장외과','종양내과','혈액종양과'],'level':'상급종합','prof_count':320},
            {'name':'세브란스병원','addr':'서울 서대문구','specialty':['신경과','소화기내과','심장내과','내분비내과'],'level':'상급종합','prof_count':280},
            {'name':'삼성서울병원','addr':'서울 강남구','specialty':['암센터','뇌신경센터','심혈관센터','희귀질환센터'],'level':'상급종합','prof_count':300},
            {'name':'서울아산병원','addr':'서울 송파구','specialty':['간이식','심장이식','신경외과','소아청소년과'],'level':'상급종합','prof_count':310}],
    '충청권':[{'name':'충남대학교병원','addr':'대전 중구','specialty':['신경외과','혈액종양과','심장내과'],'level':'상급종합','prof_count':120},
            {'name':'건양대학교병원','addr':'대전 서구','specialty':['안과','이비인후과','내과'],'level':'상급종합','prof_count':95}],
    '호남권':[{'name':'전남대학교병원','addr':'광주 동구','specialty':['혈액종양과','신경과','심장내과'],'level':'상급종합','prof_count':140},
            {'name':'조선대학교병원','addr':'광주 동구','specialty':['외과','이비인후과','비뇨의학과'],'level':'상급종합','prof_count':110}],
    '대구경북권':[{'name':'경북대학교병원','addr':'대구 중구','specialty':['신경외과','혈액종양과','소화기내과'],'level':'상급종합','prof_count':150},
              {'name':'계명대학교동산병원','addr':'대구 달서구','specialty':['심장내과','내분비내과','신경과'],'level':'상급종합','prof_count':130}],
    '부산경남권':[{'name':'부산대학교병원','addr':'부산 서구','specialty':['신경외과','혈액종양과','심장외과'],'level':'상급종합','prof_count':160},
              {'name':'양산부산대학교병원','addr':'경남 양산시','specialty':['소화기내과','내분비내과','신경과'],'level':'상급종합','prof_count':140}],
    '강원권':[{'name':'강원대학교병원','addr':'강원 춘천시','specialty':['신경과','외과','내과'],'level':'상급종합','prof_count':80},
            {'name':'한림대학교춘천성심병원','addr':'강원 춘천시','specialty':['심장내과','신경외과','비뇨의학과'],'level':'상급종합','prof_count':75}],
    '제주권':[{'name':'제주대학교병원','addr':'제주시','specialty':['신경과','내과','외과','혈액종양과'],'level':'상급종합','prof_count':60}],
}
def get_region(sido):
    for r,sidos in REGIONS.items():
        if any(s in sido for s in sidos): return r
    return '수도권'
def query_track_a(region, kws):
    h = TRACK_A_DB.get(region, TRACK_A_DB['수도권'])
    return sorted(h, key=lambda x:(-sum(1 for k in kws if any(k in d for d in x['depts'])),-x['rating']))[:3]
def query_track_b(region, kws):
    h = TRACK_B_DB.get(region, TRACK_B_DB['수도권'])
    return sorted(h, key=lambda x:(-sum(1 for k in kws if any(k in s for s in x['specialty'])),-x['prof_count']))[:3]


# ════════════════════════════════════════════════════════════════
# 3. 파이프라인 함수
# ════════════════════════════════════════════════════════════════
# 셀 4: 파이프라인 함수 전체 정의 (매번 실행)

# ── 이미지 → base64 인코딩 ───────────────────────────────────
def image_to_base64(pil_image):
    """PIL 이미지를 Claude API용 base64로 변환"""
    if pil_image.mode != 'RGB':
        pil_image = pil_image.convert('RGB')
    # 너무 크면 리사이즈 (최대 1568px — Claude 권장)
    max_size = 1568
    if max(pil_image.size) > max_size:
        ratio = max_size / max(pil_image.size)
        new_size = (int(pil_image.size[0]*ratio), int(pil_image.size[1]*ratio))
        pil_image = pil_image.resize(new_size, Image.LANCZOS)
    buf = io.BytesIO()
    pil_image.save(buf, format='JPEG', quality=90)
    return base64.standard_b64encode(buf.getvalue()).decode('utf-8')

# ── 영상 소견 생성 (VLM) ─────────────────────────────────────
def analyze_medical_image(pil_image):
    """의료 영상을 분석해 영상 종류 + 소견 보조 텍스트 생성 (참고용)"""
    img_b64 = image_to_base64(pil_image)
    prompt = (
        "당신은 영상의학 보조 AI입니다. 이 의료 영상을 보고 아래 JSON만 출력하세요.\n"
        "진단이 아닌 '관찰 소견'만 기술하며, 불확실하면 추정임을 명시하세요.\n\n"
        '{"modality": "영상 종류 (X-ray/CT/MRI 중 하나)", '
        '"body_part": "촬영 부위", '
        '"findings": "관찰되는 주요 소견 2~3개 (한국어, 추정 표현 사용)", '
        '"caution": "환자가 주의 깊게 봐야 할 부위 설명"}'
    )
    resp = client.messages.create(
        model=MODEL, max_tokens=600,
        messages=[{"role":"user","content":[
            {"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":img_b64}},
            {"type":"text","text":prompt}
        ]}]
    )
    m = re.search(r'\{.*\}', resp.content[0].text.strip(), re.DOTALL)
    if m:
        try: return json.loads(m.group())
        except: pass
    return {"modality":"미상","body_part":"미상","findings":"영상 분석 실패","caution":""}

# ── RAG 검색 ─────────────────────────────────────────────────
def retrieve(query, top_k=5):
    qvec = normalize(VECTORIZER.transform([query]), norm='l2')
    scores = (MATRIX @ qvec.T).toarray().flatten()
    return [{'text':DOCS[i]['text'],'score':float(scores[i]),'cat':DOCS[i]['cat']} for i in scores.argsort()[::-1][:top_k]]

def get_fewshot(domain_id, n=2):
    pool = FEWSHOT.get(domain_id, FEWSHOT.get(13, []))
    samples = random.sample(pool, min(n, len(pool)))
    return "\n\n".join(f"[전문 소견]\n{s['question'][:200]}\n[쉬운 말 설명]\n{s['answer'][:300]}" for s in samples)

def generate_easy_explanation(input_text, retrieved_docs, domain_id):
    context = "\n---\n".join([d['text'] for d in retrieved_docs[:3]])
    fewshot = get_fewshot(domain_id)
    prompt = ("당신은 환자에게 의료 정보를 쉽게 설명해주는 전문가입니다.\n"
        "초등학생도 이해할 수 있는 일상 언어로 설명하되 의학적 사실은 보존하세요.\n\n"
        f"[참고 의학 지식]\n{context}\n\n[변환 예시]\n{fewshot}\n\n"
        f"[환자 소견/진단]\n{input_text}\n\n[쉬운 말 설명] (200~300자):")
    resp = client.messages.create(model=MODEL, max_tokens=500, messages=[{"role":"user","content":prompt}])
    return resp.content[0].text.strip()

def extract_dept_and_keywords(input_text):
    dept_list = list(DOMAIN_MAP.values())
    prompt = ("다음 의료 소견에서 정보를 추출하세요. 반드시 JSON만 출력하세요.\n\n"
        f"소견: {input_text}\n\n"
        f'출력 형식:\n{{"dept":"진료과명 (다음 중 하나: {", ".join(dept_list)})",'
        '"severity":"경증 또는 중증","urgency":"일반 또는 긴급",'
        '"keywords":["키워드1","키워드2","키워드3"],"reason":"중증도 판단 근거 한 줄"}}')
    resp = client.messages.create(model=MODEL, max_tokens=300, messages=[{"role":"user","content":prompt}])
    m = re.search(r'\{.*\}', resp.content[0].text.strip(), re.DOTALL)
    if m:
        try: return json.loads(m.group())
        except: pass
    return {"dept":"기타","severity":"경증","urgency":"일반","keywords":[],"reason":"분석 실패"}

def synthesize_report(input_text, easy, triage, hospitals, track):
    hosp_lines = []
    for i,h in enumerate(hospitals,1):
        if track=='A':
            hosp_lines.append(f"{i}. {h['name']} ({h['addr']}) — {h['type']} | 주요과: {', '.join(h['depts'])} | 평점: {h['rating']}")
        else:
            hosp_lines.append(f"{i}. {h['name']} ({h['addr']}) — {h['level']} | 전문분야: {', '.join(h['specialty'])} | 전문의 {h['prof_count']}명")
    referral = ("\n⚠️ 상급종합병원 방문 시 1차 의원에서 진료의뢰서(소견서)를 먼저 발급받으시면 진료비 혜택을 받을 수 있습니다.\n") if track=='B' else ""
    prompt = ("다음 정보를 바탕으로 환자에게 전달할 최종 안내 리포트를 작성하세요.\n"
        "엄격한 작성 규칙:\n"
        "1. 원본 소견에 명시된 내용만 포함하세요. 원문에 없는 치료법·예후·추가 검사·예상 결과는 절대 언급하지 마세요.\n"
        "2. 원본에 없는 주관적 판단(예: '비교적 가벼운', '심각한')을 임의로 추가하지 마세요.\n"
        "3. 면책 고지는 반드시 완전한 문장으로 마무리하세요.\n\n"
        f"[원본 소견] {input_text}\n[쉬운 말 설명] {easy}\n"
        f"[진료 트랙] {'Track B — 상급종합 3차' if track=='B' else 'Track A — 지역 1·2차'}\n"
        f"[판단 근거] {triage.get('reason','')}\n[추천 의료기관]\n" + '\n'.join(hosp_lines) + referral +
        "\n\n구조:\n1. 📋 현재 상태 요약 (쉬운 말, 3~4문장)\n2. 🏥 추천 의료기관 목록\n3. ⚠️ 주의사항 및 면책 고지")
    resp = client.messages.create(model=MODEL, max_tokens=1200, messages=[{"role":"user","content":prompt}])
    return resp.content[0].text.strip()

def llm_judge(original_text, generated_report):
    prompt = (
        "당신은 의료 AI 출력물의 품질을 평가하는 전문 판정관입니다.\n\n"
        "[원본 환자 소견]\n" + original_text + "\n\n"
        "[AI 생성 리포트]\n" + generated_report + "\n\n"
        "아래 4가지 항목을 평가하고 JSON만 출력하세요.\n\n"
        "평가 기준:\n"
        "- correctness (사실 정확성): 원문의 의학적 사실을 왜곡·과장·축소 없이 올바르게 해석했는가\n"
        "  4=모든 사실 정확, 3=경미한 부정확 1건, 2=임상 의미에 영향 주는 부정확 1건, 1=사실 일부 왜곡, 0=핵심 사실 명백히 틀림[자동FAIL]\n"
        "- completeness (완전성): 환자에게 관련된 핵심 의학 정보가 누락 없이 포함되었는가 (덜 중요한 정보 생략은 허용)\n"
        "  4=누락 없음, 3=부차적 정보 1건 누락, 2=핵심 정보 1건 누락, 1=중요 정보 다수 누락, 0=핵심 소견 대부분 누락\n"
        "- hallucination (환각 통제): 원문에 없는 내용을 지어내거나 사실과 다른 정보를 추가하지 않았는가\n"
        "  4=추가 정보 없음, 3=일반 상식 수준 보조 설명 1건(사실 부합), 2=원문 없는 정보 추가(위해 가능성 낮음), 1=추정이 사실처럼 기술, 0=사실과 다른 정보 생성[자동FAIL]\n"
        "- readability (환자 이해도): 전문용어를 일상어로 풀고 환자가 이해·행동할 수 있게 전달했는가\n"
        "  4=초등 고학년 수준 이해 가능, 3=전문용어 1-2개 미풀이, 2=일부 문장 전문적, 1=전문용어 다수 잔존, 0=단순화 효과 없음\n\n"
        "PASS 조건: total >= 12 AND correctness >= 2 AND hallucination >= 2 AND 어떤 항목도 0점 없음\n\n"
        '출력 예시: {"correctness":{"score":4,"comment":"모든 사실 정확"},'
        '"completeness":{"score":3,"comment":"부차적 정보 1건 누락"},'
        '"hallucination":{"score":4,"comment":"추가 정보 없음"},'
        '"readability":{"score":3,"comment":"전문용어 1개 미풀이"},'
        '"total":14,"verdict":"PASS","feedback":"전반적으로 양호"}'
    )
    resp = client.messages.create(
        model=MODEL, max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )
    m = re.search(r'\{.*\}', resp.content[0].text.strip(), re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except:
            pass
    return {"total": 0, "verdict": "FAIL", "feedback": "판정 실패"}

# ── 영문 소견 → 한국어 번역 ──
# ── 영문 소견 → 한국어 번역 함수 ─────────────────────────────
def translate_findings(english_text):
    """영문 판독 소견을 한국어로 번역 — 의학 용어는 영문 병기"""
    prompt = (
        "다음 영문 영상의학 판독 소견을 한국어로 번역하세요.\n"
        "규칙: 1) 의학 전문용어는 '한국어(영문)' 형태로 병기  2) XXXX는 '[비식별]'로 표기\n"
        "3) 번역문만 출력, 설명 금지\n\n"
        f"[영문 소견]\n{english_text}\n\n[한국어 번역]:"
    )
    resp = client.messages.create(model=MODEL, max_tokens=600,
                                  messages=[{"role": "user", "content": prompt}])
    return resp.content[0].text.strip()


# ════════════════════════════════════════════════════════════════
# 4. 통합 실행 함수
# ════════════════════════════════════════════════════════════════
# 셀 5: run_pipeline — 영상/텍스트 입력 분기 (매번 실행)
def run_pipeline(image, input_text, sido, progress=gr.Progress()):
    has_image = image is not None
    has_text  = bool(input_text and input_text.strip())

    if not has_image and not has_text:
        return ("영상 또는 소견서 중 하나는 입력해주세요.", "", "", "", "", "")

    logs = []
    region = get_region(sido.split(' (')[0])
    img_info = None

    # ── 입력 분기 ─────────────────────────────────────────────
    if has_image:
        progress(0.10, desc="의료 영상 분석 중...")
        img_info = analyze_medical_image(image)
        logs.append(f"✅ 영상 분석 — {img_info.get('modality','?')} / {img_info.get('body_part','?')}")

    # 분석 기준 텍스트 결정: 소견서 우선, 없으면 영상 소견
    if has_text:
        analysis_text = input_text
        mode = "영상+소견서" if has_image else "소견서"
    else:
        analysis_text = f"[영상 분석 소견 — 참고용] {img_info.get('findings','')}"
        mode = "영상 단독"
    logs.append(f"✅ 입력 모드: {mode}")

    progress(0.25, desc="의학 지식 검색 중...")
    retrieved = retrieve(analysis_text, top_k=5)
    logs.append(f"✅ RAG 검색 — {len(retrieved)}건")

    progress(0.40, desc="진료과·중증도 분석 중...")
    triage    = extract_dept_and_keywords(analysis_text)
    dept      = triage.get('dept','기타')
    severity  = triage.get('severity','경증')
    urgency   = triage.get('urgency','일반')
    keywords  = triage.get('keywords',[])
    domain_id = next((k for k,v in DOMAIN_MAP.items() if v==dept), 13)
    track     = 'B' if severity == '중증' or urgency == '긴급' else 'A'
    logs.append(f"✅ {dept} | {severity} | {urgency} → {'Track B' if track=='B' else 'Track A'}")

    progress(0.55, desc="쉬운 말 설명 생성 중...")
    easy = generate_easy_explanation(analysis_text, retrieved, domain_id)

    progress(0.70, desc="병원 매칭 중...")
    hospitals = query_track_b(region, keywords) if track=='B' else query_track_a(region, [dept]+keywords)
    logs.append(f"✅ {region} 권역 병원 {len(hospitals)}곳")

    progress(0.82, desc="최종 리포트 생성 중...")
    report = synthesize_report(analysis_text, easy, triage, hospitals, track)

    progress(0.92, desc="품질 검증 중...")
    judge = llm_judge(analysis_text, report)
    verdict, total = judge.get('verdict','FAIL'), judge.get('total',0)
    logs.append(f"✅ Judge — {verdict} ({total}/16점)")

    # ── 출력 마크다운 구성 ────────────────────────────────────
    # 영상 정보 블록 (영상 입력 시)
    img_md = ""
    if has_image:
        img_md = (f"### 🖼️ 영상 정보\n"
                  f"- **종류:** {img_info.get('modality','미상')}\n"
                  f"- **부위:** {img_info.get('body_part','미상')}\n"
                  f"- **관찰 소견(참고용):** {img_info.get('findings','')}\n"
                  f"- **주의 깊게 볼 부위:** {img_info.get('caution','')}\n\n"
                  f"> ⚠️ 영상 소견은 AI 참고 정보이며 진단이 아닙니다.\n\n---\n\n")

    easy_full = img_md + "### 💬 쉬운 말 설명\n\n" + easy

    j = judge
    verdict = judge.get('verdict', 'FAIL')
    total   = judge.get('total', 0)

    judge_md = (
        f"### 판정 결과: {'✅ PASS' if verdict=='PASS' else '❌ FAIL'} ({total}/16점)\n\n"
        f"| 항목 | 점수 | 평가 |\n|---|---|---|\n"
        f"| ① 사실 정확성 (Correctness) | {j.get('correctness',{}).get('score','?')}/4 | {j.get('correctness',{}).get('comment','—')} |\n"
        f"| ② 완전성 (Completeness) | {j.get('completeness',{}).get('score','?')}/4 | {j.get('completeness',{}).get('comment','—')} |\n"
        f"| ③ 환각 통제 (Hallucination) | {j.get('hallucination',{}).get('score','?')}/4 | {j.get('hallucination',{}).get('comment','—')} |\n"
        f"| ④ 환자 이해도 (Readability) | {j.get('readability',{}).get('score','?')}/4 | {j.get('readability',{}).get('comment','—')} |\n\n"
        f"**PASS 기준**: 총점 12/16점(75%) + 정확성·환각 각 2점 이상 + 어떤 항목도 0점 없음\n\n"
        f"**개선 의견:** {j.get('feedback','')}"
    )

    rag_md = "### 📚 참고 의학 문서 (Top 5)\n\n" + "\n\n".join(
        f"**[{i+1}]** `{d['cat']}` (유사도 {d['score']:.3f})\n\n{d['text'][:200]}..." for i,d in enumerate(retrieved))

    triage_md = (f"### 🔍 분석 결과\n- **입력 모드:** {mode}\n- **진료과:** {dept}\n"
        f"- **중증도:** {severity} | **긴급도:** {urgency}\n- **질병 키워드:** {', '.join(keywords)}\n"
        f"- **판단 근거:** {triage.get('reason','')}\n"
        f"- **배정 트랙:** {'Track B — 상급종합 3차' if track=='B' else 'Track A — 지역 1·2차'}\n- **권역:** {region}")

    return easy_full, triage_md, report, judge_md, rag_md, "\n".join(logs)


# ════════════════════════════════════════════════════════════════
# 5. Gradio UI
# ════════════════════════════════════════════════════════════════
SIDO_LIST = [f"{s} ({r})" for r,sidos in REGIONS.items() for s in sidos]

CSS = """
.medical-header {text-align:center; padding:24px 0 12px;}
.medical-header h1 {font-size:28px; margin-bottom:6px;}
.disclaimer-box {font-size:13px; color:#666; background:#f8f9fa;
    border-left:4px solid #0d9488; padding:12px 16px; border-radius:4px; margin-bottom:16px;}
.gr-image {border-radius:8px !important;}
"""

with gr.Blocks(title="환자 맞춤형 의료 안내 서비스", theme=gr.themes.Soft(primary_hue="teal"), css=CSS) as demo:

    gr.HTML("""
    <div class="medical-header">
      <h1>🏥 환자 맞춤형 의료 안내 서비스</h1>
      <p style="color:#666;">의료 영상과 소견서를 입력하면, 영상을 함께 보며 쉬운 설명과 맞춤형 병원을 안내해드립니다.</p>
    </div>
    <div class="disclaimer-box">
      ⚠️ 본 서비스는 의료 정보 제공 목적의 AI 보조 도구입니다.
      진단·처방을 대체하지 않으며, 최종 의료 판단은 반드시 전문의에게 받으시기 바랍니다.
    </div>""")

    with gr.Row():
        # ── 좌측: 입력 + 영상 표시 ────────────────────────────
        with gr.Column(scale=5):
            gr.Markdown("#### 📥 입력")
            inp_image = gr.Image(label="의료 영상 (X-ray / CT / MRI)", type="pil", height=360)
            inp_text  = gr.Textbox(label="소견서 / 진단 텍스트 (선택)",
                placeholder="소견서나 진단 내용이 있으면 입력하세요. 영상만으로도 분석 가능합니다.", lines=4)
            inp_sido  = gr.Dropdown(choices=SIDO_LIST, value="서울 (수도권)", label="거주 지역 (시·도)")
            with gr.Row():
                btn_run   = gr.Button("🔍 분석 시작", variant="primary", scale=3)
                btn_clear = gr.Button("🗑️ 초기화", scale=1)

        # ── 우측: 분석 결과 ───────────────────────────────────
        with gr.Column(scale=7):
            gr.Markdown("#### 📤 분석 결과")
            with gr.Tabs():
                with gr.Tab("💬 영상 + 쉬운 말 설명"): out_easy   = gr.Markdown()
                with gr.Tab("🏥 최종 리포트"):        out_report = gr.Markdown()
                with gr.Tab("🔍 중증도 분석"):        out_triage = gr.Markdown()
                with gr.Tab("⚖️ 품질 검증"):          out_judge  = gr.Markdown()
                with gr.Tab("📚 RAG 근거"):           out_rag    = gr.Markdown()
                with gr.Tab("📋 처리 로그"):          out_log    = gr.Markdown()

    btn_run.click(fn=run_pipeline, inputs=[inp_image, inp_text, inp_sido],
                  outputs=[out_easy, out_triage, out_report, out_judge, out_rag, out_log])
    btn_clear.click(fn=lambda: (None,"","서울 (수도권)"), outputs=[inp_image, inp_text, inp_sido])

# ════════════════════════════════════════════════════════════════
# 6. 실행
# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    if "여기에_API_키_입력" in API_KEY:
        print("⚠️  API 키를 설정하세요: 환경변수 ANTHROPIC_API_KEY 또는 파일 상단 API_KEY 변수")
    print("\n🚀 Gradio 실행 중... 잠시 후 브라우저에서 열립니다.")
    demo.launch(server_name="0.0.0.0", share=True, inbrowser=True)
