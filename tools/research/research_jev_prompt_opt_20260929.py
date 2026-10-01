#!/usr/bin/env python3
"""Jev 프롬프트·형식 최적화 — 정답이 객관적인 라벨셋에서 정확도로 잰다 (2026-09-29, 사전등록).

목적: 수익이 아니라 **정답 정확도**. Jev(TypeSafe, jev-latest)를 싼 추출기/분류기로 쓸 수 있는가,
어떤 state 형식·질문 설계가 정확도를 얼마나 올리는가. Claude/Anthropic API 호출 없음.

=== 사전등록 (호출 전 고정. 이탈은 보고서에 '이탈'로 기록) ===
[라벨셋 — 정답 출처는 모델 입력 본문과 독립이거나, 정형 서식 필드의 엄격한 정규식]
  E8K  : US 8-K 단일 실질 item(9.01 제외) 13종 {1.01,1.02,2.01,2.02,2.03,2.05,3.01,4.01,5.02,5.03,5.07,7.01,8.01}.
         정답 = SEC submissions `items` 메타데이터(본문과 독립). 2019+ 8-K 본문(primary 8-K 문서) 클래스당 40건(seed 0).
         본문의 "Item N.NN" 토큰과 공식 item 제목 문구는 가린다(안 가리면 과제가 자명).
  GUID : US 실적 보도자료(EX-99.1, 기존 캐시) 가이던스 방향 4종 {raised, lowered, maintained, not_provided}.
         정답 = 명시 문구 정규식(raise/lower/reaffirm … guidance|outlook)이 **문서 전체에서 한 종류만** 맞은 문서,
         not_provided = guidance|outlook|forecast 류 단어가 전혀 없는 문서. 클래스당 100건.
  KKIND: KR DART 공시 유형 7종(supply·rights·buyback·cancel·bonus·split·major_holder). 정답 = 공시 보고서명(classify),
         본문의 제목 문구는 가린다. 클래스당 최대 60건(split 23·bonus 58은 전수).
  KMETH: KR 유상증자 증자방식 4종 {third_party, shareholder, shareholder_then_public, general_public}.
         정답 = "증자방식" 서식 필드 정규식. 제3자 120건 + 나머지 전수.
  KREG : KR 공급계약 판매·공급지역 국내/해외 2종. 정답 = "판매ㆍ공급지역" 필드 값을 자체 사전(국내 시도/시군구 vs 국가·권역)
         으로 분류, 양쪽 다 맞거나 둘 다 안 맞으면 제외. 클래스당 150건.
  KRAT : KR 공급계약 매출액대비(%) 5구간 {<5, 5–10, 10–20, 20–50, ≥50}. 정답 = "매출액대비(%)" 필드 수치. 구간당 60건.
[변형 — 셋별 고정 목록은 VARIANTS 참조. 비교 축]
  state 형식: 문자열(prose) vs JSON 이름 필드 vs 번호 섹션 key-value(JSON).
  언어(KR): 원문 한국어 vs 서식 라벨만 영어로 바꾼 글로서리 번역(결정론적 사전, 기계번역 아님) vs 이중(원문+글로서리).
  길이: 전문(상한 6,000/8,000자) vs 절단(600/1,500/2,000자) vs 관련 단락 추출.
  질문: 단일 Choice vs 원자 Noul 여러 개(argmax, GUID는 any-guidance<0.5 → not_provided).
  선택지: 설명 없음(null) vs 한 줄 설명 vs 구조화 설명(what/not_for). 질문 문구 2종(E8K).
  KRAT 추출: 구간 직접 Choice vs 문서 숫자 후보를 정규식으로 뽑아 Choice로 고르게 한 뒤 코드가 구간화(문서 권장 패턴).
  GUID 누설 통제: 정답을 만든 동사 문구를 가린 변형(문맥만으로 추론)을 별도로 둔다.
[지표] 정확도, macro-F1, ECE(10구간, Choice 최대확률 기준), 확신도 ≥0.8/≥0.5 커버리지·그 안의 정확도,
  응답 지연 중앙, 비용(usage.input_tokens × $42/1B). 원자 Noul 변형은 confidence가 없어 ECE는 정규화 noul로만 참고.
[판정] 셋별 최고 변형이 정확도 ≥ 90%이면 '추출기 사용 가능(해당 과제)'. 단, 규칙이 이미 100% 푸는 명시 필드 과제
  (KMETH·KRAT 필드 스니펫)와 규칙이 못 푸는 과제(E8K·KKIND 제목 가림·GUID 문구 가림)를 분리해 보고한다.
  삽입 지점 판단은 후자 + 실측 파서 결손 규모로만 한다.
[예산] 누적 $2.5에서 수집 중단(총 한도 $3). 모든 응답 캐시(append-only jsonl, (set,variant,id) 키, 재개 가능).
[한계] 정답 정규식 자체의 오류 가능성 → 셋별 20건 수동 대조(spot). 소수 클래스(split 23, general_public 23)는 macro-F1 변동 큼.
"""
from __future__ import annotations

import concurrent.futures as cf
import html
import json
import random
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))
OUT = ROOT / "data" / "analysis" / "jev_prompt_opt_20260929"
SETS = OUT / "sets"
CACHE = OUT / "cache"
SEC = OUT / "sec8k"
REPORT = ROOT / "docs" / "reports" / "jev_prompt_opt_20260929.md"
J8K = ROOT / "data" / "analysis" / "jev_8k_t1_20260929"
T2 = ROOT / "data" / "analysis" / "model_T2_dart_20260927"
URL = "https://api.typesafe.ai/v1/systemone"
PRICE = 42e-9
BUDGET = 2.5
UA = {"User-Agent": "claudetrade research smyang03@gmail.com", "Accept-Encoding": "gzip, deflate"}
_lock = threading.Lock(); _last = [0.0]; _spent = [0.0]


def jl(p):
    p = Path(p)
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def wjl(p, rows):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def strip_html(h):
    h = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", html.unescape(h)).strip()


def sec_get(url):
    for att in range(4):
        with _lock:
            w = 0.25 - (time.time() - _last[0])
            if w > 0:
                time.sleep(w)
            _last[0] = time.time()
        try:
            r = requests.get(url, headers=UA, timeout=30)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return None
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2 + 3 * att)
    return None


# ======================================================================== sets
ITEMS = {
    "1.01": ("material_agreement_entry", "Entry into a Material Definitive Agreement",
             "the company signed or entered into a material definitive agreement (credit agreement, merger agreement, supply, license, lease, etc.)"),
    "1.02": ("material_agreement_termination", "Termination of a Material Definitive Agreement",
             "a material definitive agreement was terminated or ended early"),
    "2.01": ("acquisition_or_disposition_completed", "Completion of Acquisition or Disposition of Assets",
             "the company completed (closed) an acquisition or sale of a business or significant assets"),
    "2.02": ("results_of_operations", "Results of Operations and Financial Condition",
             "the company announces or furnishes its quarterly or annual earnings / financial results"),
    "2.03": ("direct_financial_obligation", "Creation of a Direct Financial Obligation or an Obligation under an Off-Balance Sheet Arrangement",
             "the company incurred a new debt obligation: notes issued, loan or credit facility drawn, bonds"),
    "2.05": ("exit_or_disposal_costs", "Costs Associated with Exit or Disposal Activities",
             "the company commits to a restructuring, layoffs, facility closure or exit plan with associated costs"),
    "3.01": ("delisting_or_listing_deficiency", "Notice of Delisting or Failure to Satisfy a Continued Listing Rule or Standard; Transfer of Listing",
             "the company received an exchange deficiency/delisting notice or is transferring its stock listing"),
    "4.01": ("auditor_change", "Changes in Registrant's Certifying Accountant",
             "the company dismissed, replaced or engaged its independent registered public accounting firm (auditor)"),
    "5.02": ("officer_or_director_change", "Departure of Directors or Certain Officers; Election of Directors; Appointment of Certain Officers; Compensatory Arrangements of Certain Officers",
             "a director or executive officer resigned, retired, was appointed or elected, or executive compensation arrangements changed"),
    "5.03": ("charter_or_bylaw_amendment", "Amendments to Articles of Incorporation or Bylaws; Change in Fiscal Year",
             "the company amended its certificate/articles of incorporation or bylaws, or changed its fiscal year"),
    "5.07": ("shareholder_vote_results", "Submission of Matters to a Vote of Security Holders",
             "the company reports the voting results of a shareholder meeting"),
    "7.01": ("regulation_fd_disclosure", "Regulation FD Disclosure",
             "the company furnishes information under Regulation FD, e.g. an investor presentation or press release not tied to another item"),
    "8.01": ("other_events", "Other Events",
             "the company voluntarily reports some other event it considers important that fits none of the specific items"),
}
TITLE_EXTRA = ["Financial Statements and Exhibits", "Change in Fiscal Year", "Election of Directors", "Appointment of Certain Officers",
               "Compensatory Arrangements of Certain Officers", "Transfer of Listing",
               "Obligation under an Off-Balance Sheet Arrangement of a Registrant", "Other Events"]


def mask_8k(t):
    t = re.sub(r"(?i)\bitems?\s*\d{1,2}\s*\.\s*\d{2}\b", "Item [X]", t)
    t = re.sub(r"(?i)\b(entry\s+into|termination\s+of)\s+(an?\s+)?material\s+definitive\s+agreements?", "[ITEM TITLE]", t)
    t = re.sub(r"(?i)\bsection\s+\d\s*[–—-]\s*[^.]{0,80}?(?=Item \[X\])", "", t)
    t = re.sub(r"(?i)Item \[X\]\.?\s*Regulation\s+FD\.?", "Item [X] [ITEM TITLE].", t)
    titles = [v[1] for v in ITEMS.values()] + TITLE_EXTRA
    parts = set()
    for ti in titles:
        parts.add(ti)
        parts.update(p.strip() for p in ti.split(";") if len(p.strip()) > 12)
    for ti in sorted(parts, key=len, reverse=True):
        pat = r"\s*".join(re.escape(w) for w in ti.split())
        pat = pat.replace("Registrant\\'s", "Registrant.s").replace("Registrant's", "Registrant.s")
        t = re.sub(r"(?i)" + pat.replace("'", "."), "[ITEM TITLE]", t)
    return t


def body_8k(t):
    """커버 페이지 뒤 본문(첫 Item [X]) ~ SIGNATURE 앞."""
    i = t.find("Item [X]")
    j = t.upper().find("SIGNATURE", i + 1 if i >= 0 else 0)
    s = t[i if i >= 0 else 0: j if j > 0 else len(t)]
    return s if len(s) > 200 else t


def build_e8k(per=40):
    fp = SETS / "E8K.jsonl"
    if fp.exists():
        return jl(fp)
    rows = []
    for f in sorted((J8K / "submissions_full").glob("*.json")):
        d = pd.read_json(f, dtype=False)
        if not len(d):
            continue
        d = d[(d.form == "8-K") & (d.filingDate >= "2019-01-01")]
        for r in d.itertuples():
            its = [x for x in str(r.items).split(",") if x and x != "9.01"]
            if len(its) == 1 and its[0] in ITEMS:
                rows.append({"cik": int(f.stem), "acc": r.accessionNumber, "item": its[0], "date": r.filingDate})
    df = pd.DataFrame(rows).sample(frac=1, random_state=0)
    SEC.mkdir(parents=True, exist_ok=True)
    pick = []
    for it in ITEMS:
        sub = df[df["item"] == it].drop_duplicates("cik").head(per + 8)   # 회사 중복 억제, 실패분 예비
        pick += sub.to_dict("records")

    def fetch(r):
        fp2 = SEC / f"{r['acc']}.txt"
        if fp2.exists():
            return fp2.read_text(encoding="utf-8")
        nd = r["acc"].replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{r['cik']}/{nd}/"
        ix = sec_get(base + f"{r['acc']}-index.htm")
        if ix is None:
            return None
        name = None
        for row in re.findall(r'(?is)<tr[^>]*>(.*?)</tr>', ix.text):
            m = re.search(r'href="([^"]+)"', row)
            tds = [strip_html(x) for x in re.findall(r'(?is)<td[^>]*>(.*?)</td>', row)]
            if m and len(tds) >= 4 and tds[3].upper() in ("8-K", "8-K12B", "8-K12G3") and not m.group(1).lower().endswith(".pdf"):
                name = m.group(1).split("/")[-1]; break
        if not name:
            return None
        d = sec_get(base + name)
        if d is None:
            return None
        txt = strip_html(d.text)
        fp2.write_text(txt, encoding="utf-8")
        return txt

    with cf.ThreadPoolExecutor(4) as ex:
        txts = list(ex.map(fetch, pick))
    out = []
    cnt = Counter()
    for r, t in zip(pick, txts):
        if not t or len(t) < 300 or cnt[r["item"]] >= per:
            continue
        cnt[r["item"]] += 1
        out.append({"id": r["acc"], "truth": ITEMS[r["item"]][0], "item": r["item"], "date": r["date"], "cik": r["cik"]})
    wjl(fp, out)
    print("E8K", dict(cnt))
    return out


RAISE = re.compile(r"(?i)\b(rais(?:e|es|ed|ing)|increas(?:e|es|ed|ing)|boost(?:s|ed|ing)?|lift(?:s|ed|ing)?)\b[^.;]{0,60}?\b(guidance|outlook)\b")
LOWER = re.compile(r"(?i)\b(lower(?:s|ed|ing)?|reduc(?:e|es|ed|ing)|cut(?:s|ting)?|trim(?:s|med|ming)?)\b[^.;]{0,60}?\b(guidance|outlook)\b")
MAINT = re.compile(r"(?i)\b(reaffirm(?:s|ed|ing)?|reiterat(?:e|es|ed|ing)|maintain(?:s|ed|ing)?)\b[^.;]{0,60}?\b(guidance|outlook)\b")
ANYG = re.compile(r"(?i)guidance|outlook|forecast|full[- ]year\s+(?:20\d\d\s+)?(?:revenue|sales|eps|earnings)\s+(?:is\s+)?expect|we\s+expect|anticipat")


def build_guid(per=100):
    fp = SETS / "GUID.jsonl"
    if fp.exists():
        return jl(fp)
    docs = sorted((J8K / "docs").glob("*.txt"))
    by = defaultdict(list)
    for p in docs:
        t = p.read_text(encoding="utf-8")
        hits = {"raised": bool(RAISE.search(t)), "lowered": bool(LOWER.search(t)), "maintained": bool(MAINT.search(t))}
        k = [c for c, v in hits.items() if v]
        if len(k) == 1:
            by[k[0]].append(p.stem)
        elif not k and not ANYG.search(t):
            by["not_provided"].append(p.stem)
    rng = random.Random(0)
    out = []
    for c in ("raised", "lowered", "maintained", "not_provided"):
        ids = sorted(by[c]); rng.shuffle(ids)
        out += [{"id": i, "truth": c} for i in ids[:per]]
    print("GUID pools", {c: len(v) for c, v in by.items()})
    wjl(fp, out)
    return out


# [이탈·GUID 결과 확인 후 추가] 느슨한 정답 정규식이 "lower than our annual guidance", "reduced visibility into outlook",
# "lower end of guidance" 같은 비-방향 문구를 잡는 것을 오답 검토에서 확인 → 엄격 정답셋 GUIDS 를 별도로 만든다(GUID 결과는 보존).
_GQUAL = r"(?:its|the|our|their)?\s*(?:(?:full[- ]year|fiscal(?:\s+year)?|annual|FY\s?\d{2,4}|20\d\d|first[- ]half|second[- ]half|quarterly|\w+)\s+){0,4}"
S_RAISE = re.compile(r"(?i)\b(rais(?:es|ed|ing)|increas(?:es|ed|ing)|boost(?:s|ed|ing))\s+" + _GQUAL + r"(guidance|outlook)\b")
S_LOWER = re.compile(r"(?i)\b(lower(?:s|ed|ing)|reduc(?:es|ed|ing)|cut(?:s|ting)?)\s+" + _GQUAL + r"(guidance|outlook)\b")
S_MAINT = re.compile(r"(?i)\b(reaffirm(?:s|ed|ing)?|reiterat(?:es|ed|ing)|maintain(?:s|ed|ing)?)\s+" + _GQUAL + r"(guidance|outlook)\b")
S_MIXED = re.compile(r"(?i)\b(narrow(?:s|ed|ing)?|withdr[ae]w\w*|suspend\w*)\s+" + _GQUAL + r"(guidance|outlook)\b"
                     r"|lower\s+than\s+\S*\s*(?:guidance|outlook)|lower\s+end|reduced\s+visibility")


def build_guids(per=100):
    fp = SETS / "GUIDS.jsonl"
    if fp.exists():
        return jl(fp)
    by = defaultdict(list)
    for p in sorted((J8K / "docs").glob("*.txt")):
        t = p.read_text(encoding="utf-8")
        hits = {"raised": bool(S_RAISE.search(t)), "lowered": bool(S_LOWER.search(t)), "maintained": bool(S_MAINT.search(t))}
        k = [c for c, v in hits.items() if v]
        if len(k) == 1 and not S_MIXED.search(t):
            by[k[0]].append(p.stem)
        elif not any(hits.values()) and not ANYG.search(t):
            by["not_provided"].append(p.stem)
    rng = random.Random(0)
    out = []
    for c in ("raised", "lowered", "maintained", "not_provided"):
        ids = sorted(by[c]); rng.shuffle(ids)
        out += [{"id": i, "truth": c} for i in ids[:per]]
    print("GUIDS pools", {c: len(v) for c, v in by.items()})
    wjl(fp, out)
    return out


KINDS = ["supply", "rights", "buyback", "cancel", "bonus", "split", "major_holder"]
KIND_EN = {"supply": "single sale or supply contract award",
           "rights": "paid-in capital increase (new share issuance for cash / rights offering / private placement)",
           "buyback": "decision to acquire treasury shares (share buyback)",
           "cancel": "decision to cancel (retire) shares",
           "bonus": "bonus share issue (free capital increase, stock dividend from capital surplus)",
           "split": "stock split or par-value split",
           "major_holder": "change of the largest shareholder"}
KR_TITLE = re.compile(r"주요사항보고서\s*\([^)]*\)|단일판매\s*ㆍ?\s*공급계약\s*체결|유\s*상\s*증\s*자\s*결\s*정|무\s*상\s*증\s*자\s*결\s*정|"
                      r"유무상증자\s*결정|자기주식\s*취득\s*결정|주식\s*소각\s*결정|주식\s*분할\s*결정|최대주주\s*변경|\[기재정정\]|\[첨부정정\]")


def kr_text(e):
    import research_model_T2_dart_20260927 as t2
    return t2.mask_text((T2 / "docs" / f"{e['rcept_no']}.txt").read_text(encoding="utf-8"), e["name"], e["stock"])


def build_kkind(per=60):
    fp = SETS / "KKIND.jsonl"
    if fp.exists():
        return jl(fp)
    ev = [e for e in jl(T2 / "events_features.jsonl") if e.get("has_doc")]
    rng = random.Random(0); rng.shuffle(ev)
    cnt = Counter(); out = []
    for e in ev:
        if cnt[e["kind"]] >= per:
            continue
        cnt[e["kind"]] += 1
        out.append({"id": e["rcept_no"], "truth": e["kind"], "name": e["name"], "stock": e["stock"]})
    wjl(fp, out); print("KKIND", dict(cnt))
    return out


def meth_of(t):
    m = re.search(r"증자방식\s*(.{2,30})", t)
    if not m:
        return None
    s = re.sub(r"\s", "", m.group(1))
    if s.startswith("제3자배정"):
        return "third_party"
    if s.startswith("주주배정후") or "실권주일반공모" in s:
        return "shareholder_then_public"
    if s.startswith("주주배정"):
        return "shareholder"
    if s.startswith("일반공모"):
        return "general_public"
    return None


def build_kmeth(n3=120):
    fp = SETS / "KMETH.jsonl"
    if fp.exists():
        return jl(fp)
    ev = [e for e in jl(T2 / "events_features.jsonl") if e.get("has_doc") and e["kind"] == "rights"]
    rng = random.Random(0); rng.shuffle(ev)
    out = []; cnt = Counter()
    for e in ev:
        m = meth_of((T2 / "docs" / f"{e['rcept_no']}.txt").read_text(encoding="utf-8"))
        if not m or (m == "third_party" and cnt[m] >= n3):
            continue
        cnt[m] += 1
        out.append({"id": e["rcept_no"], "truth": m, "name": e["name"], "stock": e["stock"]})
    wjl(fp, out); print("KMETH", dict(cnt))
    return out


DOM = re.compile(r"대한민국|국내|한국|서울|경기|인천|부산|대구|광주|대전|울산|세종|강원|충청|충북|충남|전라|전북|전남|경상|경북|경남|제주|"
                 r"[가-힣]{1,4}(?:특별시|광역시|특별자치시|특별자치도|시|군|구)\b")
FOR = re.compile(r"해외|국외|미국|중국|유럽|아시아|일본|대만|인도|오세아니아|캐나다|필리핀|베트남|폴란드|아프리카|중동|북미|남미|미주|영국|독일|프랑스|"
                 r"싱가포르|카타르|말레이시아|바하마|그리스|버뮤다|호주|멕시코|브라질|인도네시아|태국|사우디|아랍|UAE|튀르키예|터키|스페인|이탈리아|"
                 r"네덜란드|스웨덴|노르웨이|헝가리|체코|러시아|홍콩|마카오|몽골|이스라엘|이집트|칠레|페루|콜롬비아|파나마|라이베리아|마셜|글로벌|전세계|세계")


def region_of(t):
    m = re.search(r"판매\s*ㆍ?\s*공급지역\s*(.{1,60}?)\s+5\.", t)
    if not m:
        return None, None
    s = m.group(1)
    d, f = bool(DOM.search(s)), bool(FOR.search(s))
    if "국내외" in s:
        return None, s
    if d and not f:
        return "domestic", s
    if f and not d:
        return "foreign", s
    return None, s


def build_kreg(per=150):
    fp = SETS / "KREG.jsonl"
    if fp.exists():
        return jl(fp)
    ev = [e for e in jl(T2 / "events_features.jsonl") if e.get("has_doc") and e["kind"] == "supply"]
    rng = random.Random(0); rng.shuffle(ev)
    out = []; cnt = Counter(); allc = Counter(); parser_vs = Counter()
    for e in ev:
        lab, s = region_of((T2 / "docs" / f"{e['rcept_no']}.txt").read_text(encoding="utf-8"))
        allc[lab] += 1
        if lab and e.get("sup_region_foreign") is not None:
            parser_vs[(lab, int(e["sup_region_foreign"]))] += 1
        if not lab or cnt[lab] >= per:
            continue
        cnt[lab] += 1
        out.append({"id": e["rcept_no"], "truth": lab, "name": e["name"], "stock": e["stock"], "region_raw": s,
                    "parser_foreign": e.get("sup_region_foreign")})
    wjl(fp, out)
    (OUT / "kreg_truth_vs_parser.json").write_text(json.dumps({"truth_dist_all": {str(k): v for k, v in allc.items()},
                                                              "truth_x_parser": {f"{a}|parser_foreign={b}": v for (a, b), v in parser_vs.items()}},
                                                             ensure_ascii=False, indent=1), encoding="utf-8")
    print("KREG", dict(cnt), dict(allc), dict(parser_vs))
    return out


RBUCK = ["lt5", "5to10", "10to20", "20to50", "ge50"]
RBUCK_EN = {"lt5": "less than 5%", "5to10": "5% to under 10%", "10to20": "10% to under 20%", "20to50": "20% to under 50%", "ge50": "50% or more"}


def ratio_of(t):
    m = re.search(r"매출액\s*대비\s*\(%\)\s*([\d,]+(?:\.\d+)?)", t)
    return float(m.group(1).replace(",", "")) if m else None


def bucket(x):
    return None if x is None else "lt5" if x < 5 else "5to10" if x < 10 else "10to20" if x < 20 else "20to50" if x < 50 else "ge50"


def build_krat(per=60):
    fp = SETS / "KRAT.jsonl"
    if fp.exists():
        return jl(fp)
    ev = [e for e in jl(T2 / "events_features.jsonl") if e.get("has_doc") and e["kind"] == "supply"]
    rng = random.Random(1); rng.shuffle(ev)
    out = []; cnt = Counter()
    for e in ev:
        r = ratio_of((T2 / "docs" / f"{e['rcept_no']}.txt").read_text(encoding="utf-8"))
        b = bucket(r)
        # 구간 경계 ±2% 이내는 정답 모호성이 커서 제외하지 않는다(사전등록: 필드값 그대로). 대신 경계 근접 여부 기록.
        if not b or cnt[b] >= per:
            continue
        cnt[b] += 1
        out.append({"id": e["rcept_no"], "truth": b, "ratio": r, "name": e["name"], "stock": e["stock"]})
    wjl(fp, out); print("KRAT", dict(cnt))
    return out


# ================================================================ state helpers
GLOSS = [("판매ㆍ공급계약 구분", "contract type"), ("체결계약명", "contract name"), ("계약금액(원)", "contract amount (KRW)"),
         ("계약금액", "contract amount"), ("최근매출액(원)", "most recent annual revenue (KRW)"), ("매출액대비(%)", "as % of revenue"),
         ("대규모법인여부", "large corporation"), ("계약상대", "counterparty"), ("회사와의 관계", "relationship to company"),
         ("판매ㆍ공급지역", "sales/supply region"), ("계약기간", "contract period"), ("시작일", "start date"), ("종료일", "end date"),
         ("주요 계약조건", "key terms"), ("계약금ㆍ선급금 유무", "down payment/advance"), ("대금지급 조건", "payment terms"),
         ("계약(수주)일자", "contract (order) date"), ("공시유보 관련내용", "disclosure deferral"), ("유보사유", "deferral reason"),
         ("유보기한", "deferral deadline"), ("기타 투자판단과 관련한 중요사항", "other material information"),
         ("기타 투자판단에 참고할 사항", "other information for investors"), ("공사수주", "construction order"), ("상품공급", "product supply"),
         ("신주의 종류와 수", "type and number of new shares"), ("보통주식", "common shares"), ("기타주식", "other shares"), ("종류주식", "class shares"),
         ("1주당 액면가액", "par value per share"), ("증자전 발행주식총수", "shares outstanding before issuance"),
         ("자금조달의 목적", "purpose of funds"), ("시설자금", "facilities"), ("영업양수자금", "business acquisition"), ("운영자금", "working capital"),
         ("채무상환자금", "debt repayment"), ("타법인 증권취득자금", "acquisition of other companies' securities"), ("기타자금", "other"),
         ("증자방식", "offering method"), ("제3자배정증자", "third-party allotment"), ("주주배정후 실권주 일반공모", "shareholder allotment then public offering of unsubscribed shares"),
         ("주주배정증자", "shareholder allotment"), ("일반공모증자", "general public offering"), ("주주우선공모증자", "shareholder-priority public offering"),
         ("신주 발행가액", "new share issue price"), ("확정발행가", "final issue price"), ("예정발행가", "expected issue price"),
         ("할인율 또는 할증율", "discount or premium rate"), ("납입일", "payment date"), ("신주의 배당기산일", "dividend start date of new shares"),
         ("신주권교부예정일", "share certificate delivery date"), ("신주의 상장 예정일", "listing date of new shares"), ("이사회결의일", "board resolution date"),
         ("사외이사 참석여부", "outside director attendance"), ("참석", "present"), ("불참", "absent"), ("감사", "auditor"),
         ("취득예정주식", "shares to be acquired"), ("취득예정금액", "planned acquisition amount"), ("취득예상기간", "expected acquisition period"),
         ("보유예상기간", "expected holding period"), ("취득목적", "purpose of acquisition"), ("취득방법", "acquisition method"),
         ("위탁투자중개업자", "brokerage"), ("취득 전 자기주식 보유현황", "treasury shares held before acquisition"),
         ("배당가능이익범위 내 취득", "acquired within distributable profits"), ("기타취득", "other acquisitions"), ("비율", "ratio"),
         ("취득결정일", "decision date"), ("1일 매수 주문수량 한도", "daily purchase order limit"), ("장내매수", "open-market purchase"),
         ("소각할 주식의 종류와 수", "type and number of shares to cancel"), ("발행주식총수", "total shares outstanding"), ("1주당 가액", "value per share"),
         ("소각예정금액", "planned cancellation amount"), ("소각할 주식의 취득방법", "method of acquiring shares to cancel"), ("기취득 자기주식", "treasury shares already held"),
         ("소각 예정일", "planned cancellation date"), ("신주배정기준일", "record date for new shares"), ("1주당 신주배정 주식수", "new shares allotted per share"),
         ("주식분할 내용", "stock split details"), ("분할 전", "before split"), ("분할 후", "after split"), ("주주총회예정일", "shareholder meeting date"),
         ("신주의 효력발생일", "effective date of new shares"), ("매매거래정지기간", "trading suspension period"), ("신주권상장예정일", "new share listing date"),
         ("주식분할목적", "purpose of split"), ("유통주식수 확대", "increase share liquidity"), ("변경내용", "change details"),
         ("변경전 최대주주등", "largest shareholder before"), ("변경후 최대주주등", "largest shareholder after"), ("소유주식수", "shares owned"),
         ("소유비율", "ownership %"), ("변경사유", "reason for change"), ("지분인수목적", "purpose of stake acquisition"), ("변경일자", "change date"),
         ("변경확인일자", "change confirmation date"), ("관련공시", "related disclosure"), ("해당사항없음", "not applicable"), ("미해당", "not applicable"),
         ("해당", "applicable"), ("대한민국", "Republic of Korea"), ("국내", "domestic (Korea)"), ("해외", "overseas"), ("(원)", "(KRW)"), ("(주)", "(shares)"),
         ("A사", "Company A"), ("[헤더 생략]", "[header omitted]"), ("주요사항보고서", "major matters report"), ("거래소 신고의무 사항", "exchange filing")]
GLOSS.sort(key=lambda x: -len(x[0]))


def gloss(t):
    for k, v in GLOSS:
        t = t.replace(k, f" {v} ")
    return re.sub(r"\s+", " ", t).strip()


def kv(t):
    """'N. 라벨 값' 번호 섹션을 JSON으로."""
    parts = re.split(r"(?:(?<=\s)|^)(\d{1,2})\.\s+(?=[^\d\s])", t)
    d = {"preamble": parts[0].strip()[:400]}
    for i in range(1, len(parts) - 1, 2):
        k = f"section_{parts[i]}"
        v = parts[i + 1].strip()
        d[k] = (d[k] + " | " + v) if k in d else v
    return d


def snippet(t, pat, a=150, b=250):
    m = re.search(pat, t)
    if not m:
        return t[:600]
    return t[max(0, m.start() - a): m.start() + b]


_TXT: dict = {}


def text_of(set_name, r):
    key = (set_name, r["id"])
    if key not in _TXT:
        if set_name == "E8K":
            _TXT[key] = mask_8k((SEC / f"{r['id']}.txt").read_text(encoding="utf-8"))
        elif set_name in ("GUID", "GUIDS"):
            _TXT[key] = (J8K / "docs" / f"{r['id']}.txt").read_text(encoding="utf-8")
        else:
            t = kr_text({"rcept_no": r["id"], "name": r["name"], "stock": r["stock"]})
            _TXT[key] = KR_TITLE.sub(" [제목] ", t) if set_name == "KKIND" else t
    return _TXT[key]


# ================================================================ questions
def q_choice(instr, labels, desc):
    return {"type": "choice", "instructions": instr, "criteria": {k: desc.get(k) if desc else None for k in labels}}


E8K_LABELS = [v[0] for v in ITEMS.values()]
E8K_DESC = {v[0]: v[2] for v in ITEMS.values()}
E8K_RUBRIC = {v[0]: {"what": v[2], "official_item": f"Form 8-K Item {k}"} for k, v in ITEMS.items()}
E8K_RUBRIC["regulation_fd_disclosure"]["not_for"] = "events the company formally reports as an event (use other_events); earnings results (use results_of_operations)"
E8K_RUBRIC["other_events"]["not_for"] = "information merely furnished for Regulation FD such as investor presentations (use regulation_fd_disclosure)"
E8K_RUBRIC["direct_financial_obligation"]["not_for"] = "signing a credit agreement without describing the obligation incurred (use material_agreement_entry)"
E8K_RUBRIC["material_agreement_entry"]["not_for"] = "borrowings described as a newly created obligation (use direct_financial_obligation)"
E8K_Q1 = "Which Form 8-K event item is this current report filed under?"
E8K_Q2 = "What kind of corporate event does this SEC Form 8-K report?"

G_LABELS = ["raised", "lowered", "maintained", "not_provided"]
G_DESC = {"raised": "the company raises (increases) its forward financial guidance or outlook versus prior guidance",
          "lowered": "the company lowers (cuts, reduces) its forward financial guidance or outlook versus prior guidance",
          "maintained": "the company reaffirms, reiterates or maintains its existing guidance or outlook unchanged",
          "not_provided": "the release gives no forward financial guidance or outlook at all"}
G_Q = "How does this earnings press release change the company's forward financial guidance (revenue, EPS or similar)?"

K_Q = "What type of Korean corporate disclosure (DART filing) is this?"
M_LABELS = ["third_party", "shareholder", "shareholder_then_public", "general_public"]
M_DESC = {"third_party": "third-party allotment: new shares are allotted to specific designated investors (private placement)",
          "shareholder": "shareholder allotment: new shares offered to existing shareholders pro rata (rights offering), with no public offering stage",
          "shareholder_then_public": "shareholder allotment first, then unsubscribed (forfeited) shares are offered to the general public",
          "general_public": "general public offering: new shares offered to the public at large"}
M_Q = "What offering method does this Korean paid-in capital increase (new share issuance) use?"
R_LABELS = ["domestic", "foreign"]
R_DESC = {"domestic": "the sales/supply region is inside South Korea (Korea, a Korean province, city or district)",
          "foreign": "the sales/supply region is outside South Korea (another country, overseas region, or global)"}
R_Q = "Is the sales/supply region of this Korean supply contract inside South Korea or outside?"
A_Q = "The contract amount as a percentage of the company's most recent annual revenue falls in which range?"


def num_candidates(t, maxn=40):
    out = []
    for m in re.finditer(r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d+\.\d+|\d+)(?![\d])", t):
        v = m.group(1)
        if len(out) >= maxn:
            break
        ctx = t[max(0, m.start() - 40): m.end() + 5].replace("\n", " ")
        out.append((v, ctx))
    return out


# ------------------------------------------------------------ variants
# 각 변형: (state, questions, decode) 생성 함수. decode(answers) -> (pred, probs dict | None, conf | None)
def dec_choice(qid):
    def f(a, r=None):
        x = a[qid]
        return x["choice"], x.get("probabilities"), x.get("confidence")
    return f


def dec_nouls(labels):
    def f(a, r=None):
        p = {k: a[f"n_{k}"]["noul"] for k in labels}
        s = sum(p.values()) or 1.0
        return max(p, key=p.get), {k: v / s for k, v in p.items()}, None
    return f


def v_e8k(name):
    def mk(r):
        t = text_of("E8K", r)
        body = body_8k(t)
        if name == "str6k_nodesc":
            return t[:6000], {"q": q_choice(E8K_Q1, E8K_LABELS, None)}, dec_choice("q")
        if name == "json6k_desc":
            return {"form_8k_text": t[:6000]}, {"q": q_choice(E8K_Q1, E8K_LABELS, E8K_DESC)}, dec_choice("q")
        if name == "json1500_desc":
            return {"form_8k_text": t[:1500]}, {"q": q_choice(E8K_Q1, E8K_LABELS, E8K_DESC)}, dec_choice("q")
        if name == "body_desc":
            return {"form_8k_body": body[:6000]}, {"q": q_choice(E8K_Q1, E8K_LABELS, E8K_DESC)}, dec_choice("q")
        if name == "body_desc_q2":
            return {"form_8k_body": body[:6000]}, {"q": q_choice(E8K_Q2, E8K_LABELS, E8K_DESC)}, dec_choice("q")
        if name == "body_rubric":
            return {"form_8k_body": body[:6000]}, {"q": q_choice(E8K_Q1, E8K_LABELS, E8K_RUBRIC)}, dec_choice("q")
        if name == "body_nouls":
            qs = {f"n_{k}": {"type": "noul", "instructions": f"This Form 8-K reports that {E8K_DESC[k]}."} for k in E8K_LABELS}
            return {"form_8k_body": body[:6000]}, qs, dec_nouls(E8K_LABELS)
        raise KeyError(name)
    return mk


GMASK = re.compile(r"(?i)\b(rais(?:e|es|ed|ing)|increas(?:e|es|ed|ing)|boost(?:s|ed|ing)?|lift(?:s|ed|ing)?|lower(?:s|ed|ing)?|"
                   r"reduc(?:e|es|ed|ing)|cut(?:s|ting)?|trim(?:s|med|ming)?|reaffirm(?:s|ed|ing)?|reiterat(?:e|es|ed|ing)|"
                   r"maintain(?:s|ed|ing)?|updat(?:e|es|ed|ing)|narrow(?:s|ed|ing)?|revis(?:e|es|ed|ing))\b")


def v_guid(name):
    import research_jev_8k_t1_20260929 as j8
    def mk(r):
        t = text_of("GUID", r)
        q1 = {"q": q_choice(G_Q, G_LABELS, G_DESC)}
        if name == "str6k":
            return t[:6000], q1, dec_choice("q")
        if name == "outlook":
            return {"press_release_headline_and_outlook": j8.outlook_text(t)}, q1, dec_choice("q")
        if name == "outlook_nodesc":
            return {"press_release_headline_and_outlook": j8.outlook_text(t)}, {"q": q_choice(G_Q, G_LABELS, None)}, dec_choice("q")
        if name == "outlook_json":
            o = j8.outlook_text(t)
            head, _, rest = o.partition("\nOUTLOOK PASSAGES: ")
            return {"headline": head.replace("HEADLINE: ", ""), "outlook_passages": rest.split(" ... ")}, q1, dec_choice("q")
        if name == "outlook_verbmask":
            return {"press_release_headline_and_outlook": GMASK.sub("[verb]", j8.outlook_text(t))}, q1, dec_choice("q")
        if name == "outlook_nouls":
            qs = {"n_raised": {"type": "noul", "instructions": "The company raises its forward financial guidance or outlook in this release."},
                  "n_lowered": {"type": "noul", "instructions": "The company lowers or cuts its forward financial guidance or outlook in this release."},
                  "n_maintained": {"type": "noul", "instructions": "The company reaffirms or maintains its existing guidance or outlook unchanged in this release."},
                  "n_any": {"type": "noul", "instructions": "This release contains forward financial guidance or an outlook for future results."}}

            def dec(a, r=None):
                p = {k: a[f"n_{k}"]["noul"] for k in ("raised", "lowered", "maintained")}
                if a["n_any"]["noul"] < 0.5:
                    return "not_provided", None, None
                return max(p, key=p.get), None, None
            return {"press_release_headline_and_outlook": j8.outlook_text(t)}, qs, dec
        raise KeyError(name)
    return mk


def v_kkind(name):
    def mk(r):
        t = text_of("KKIND", r)
        qd = {"q": q_choice(K_Q, KINDS, KIND_EN)}
        if name == "raw600":
            return {"disclosure_text_korean": t[:600]}, qd, dec_choice("q")
        if name == "raw2000":
            return {"disclosure_text_korean": t[:2000]}, qd, dec_choice("q")
        if name == "raw8000":
            return {"disclosure_text_korean": t[:8000]}, qd, dec_choice("q")
        if name == "raw2000_nodesc":
            return {"disclosure_text_korean": t[:2000]}, {"q": q_choice(K_Q, KINDS, None)}, dec_choice("q")
        if name == "str2000":
            return t[:2000], qd, dec_choice("q")
        if name == "kv2000":
            return {"disclosure_sections": kv(t[:2000])}, qd, dec_choice("q")
        if name == "gloss2000":
            return {"disclosure_text_labels_in_english": gloss(t[:2000])}, qd, dec_choice("q")
        if name == "bilingual2000":
            return {"disclosure_text_korean": t[:2000], "labels_in_english": gloss(t[:2000])}, qd, dec_choice("q")
        if name == "nouls2000":
            qs = {f"n_{k}": {"type": "noul", "instructions": f"This Korean disclosure is a {KIND_EN[k]}."} for k in KINDS}
            return {"disclosure_text_korean": t[:2000]}, qs, dec_nouls(KINDS)
        raise KeyError(name)
    return mk


def v_kmeth(name):
    def mk(r):
        t = text_of("KMETH", r)
        qd = {"q": q_choice(M_Q, M_LABELS, M_DESC)}
        if name == "raw8000":
            return {"disclosure_text_korean": t[:8000]}, qd, dec_choice("q")
        if name == "raw2000":
            return {"disclosure_text_korean": t[:2000]}, qd, dec_choice("q")
        if name == "gloss2000":
            return {"disclosure_text_labels_in_english": gloss(t[:2000])}, qd, dec_choice("q")
        if name == "kv2000":
            return {"disclosure_sections": kv(t[:2000])}, qd, dec_choice("q")
        if name == "field_snippet":
            return {"offering_method_field_korean": snippet(t, r"증자방식", 20, 80)}, qd, dec_choice("q")
        if name == "raw2000_nodesc":
            return {"disclosure_text_korean": t[:2000]}, {"q": q_choice(M_Q, M_LABELS, None)}, dec_choice("q")
        raise KeyError(name)
    return mk


def v_kreg(name):
    def mk(r):
        t = text_of("KREG", r)
        qd = {"q": q_choice(R_Q, R_LABELS, R_DESC)}
        if name == "raw":
            return {"disclosure_text_korean": t[:4000]}, qd, dec_choice("q")
        if name == "raw_nodesc":
            return {"disclosure_text_korean": t[:4000]}, {"q": q_choice(R_Q, R_LABELS, None)}, dec_choice("q")
        if name == "gloss":
            return {"disclosure_text_labels_in_english": gloss(t[:4000])}, qd, dec_choice("q")
        if name == "kv":
            return {"disclosure_sections": kv(t[:4000])}, qd, dec_choice("q")
        if name == "field_snippet":
            return {"sales_supply_region_field_korean": snippet(t, r"공급지역", 10, 60)}, qd, dec_choice("q")
        if name == "noul":
            qs = {"n": {"type": "noul", "instructions": "The sales/supply region of this contract is outside South Korea."}}

            def dec(a, r=None):
                p = a["n"]["noul"]
                return ("foreign" if p >= 0.5 else "domestic"), {"foreign": p, "domestic": 1 - p}, None
            return {"disclosure_text_korean": t[:4000]}, qs, dec
        raise KeyError(name)
    return mk


def v_krat(name):
    def mk(r):
        t = text_of("KRAT", r)
        qd = {"q": q_choice(A_Q, RBUCK, RBUCK_EN)}
        if name == "raw_bucket":
            return {"disclosure_text_korean": t[:4000]}, qd, dec_choice("q")
        if name == "raw_ratio_masked":   # [이탈·추가] 비율 값을 가려 금액/매출액 나눗셈을 요구 — 문서가 경고한 수치 계산 한계 측정
            tm = re.sub(r"(매출액\s*대비\s*\(%\)\s*)[\d,]+(?:\.\d+)?", r"\1[가림]", t)
            return {"disclosure_text_korean": tm[:4000]}, qd, dec_choice("q")
        if name == "gloss_bucket":
            return {"disclosure_text_labels_in_english": gloss(t[:4000])}, qd, dec_choice("q")
        if name == "kv_bucket":
            return {"disclosure_sections": kv(t[:4000])}, qd, dec_choice("q")
        if name in ("preparsed", "preparsed_gloss"):
            src = gloss(t[:4000]) if name == "preparsed_gloss" else t[:4000]
            c = num_candidates(src)
            opts = {f"c{i}": f"the number {v} (in context: ...{ctx}...)" for i, (v, ctx) in enumerate(c)}
            q = {"q": {"type": "choice",
                       "instructions": "Which candidate number is the contract amount expressed as a percentage of the company's most recent annual revenue (매출액대비 %)?",
                       "criteria": opts}}

            def dec(a, r=None, c=c):
                x = a["q"]; ch = x["choice"]
                try:
                    val = float(c[int(ch[1:])][0].replace(",", ""))
                except Exception:  # noqa: BLE001
                    return None, None, x.get("confidence")
                # 후보 확률을 구간 확률로 합산(ECE용)
                pb = Counter()
                for k, p in (x.get("probabilities") or {}).items():
                    try:
                        pb[bucket(float(c[int(k[1:])][0].replace(",", "")))] += p
                    except Exception:  # noqa: BLE001
                        pass
                return bucket(val), dict(pb), x.get("confidence")
            return {"disclosure_text": src}, q, dec
        raise KeyError(name)
    return mk


VARIANTS = {
    "E8K": (v_e8k, ["str6k_nodesc", "json6k_desc", "json1500_desc", "body_desc", "body_desc_q2", "body_rubric", "body_nouls"]),
    "GUID": (v_guid, ["str6k", "outlook", "outlook_nodesc", "outlook_json", "outlook_verbmask", "outlook_nouls"]),
    "GUIDS": (v_guid, ["str6k", "outlook", "outlook_nodesc", "outlook_json", "outlook_verbmask", "outlook_nouls"]),
    "KKIND": (v_kkind, ["raw600", "raw2000", "raw8000", "raw2000_nodesc", "str2000", "kv2000", "gloss2000", "bilingual2000", "nouls2000"]),
    "KMETH": (v_kmeth, ["raw8000", "raw2000", "raw2000_nodesc", "gloss2000", "kv2000", "field_snippet"]),
    "KREG": (v_kreg, ["raw", "raw_nodesc", "gloss", "kv", "field_snippet", "noul"]),
    "KRAT": (v_krat, ["raw_bucket", "raw_ratio_masked", "gloss_bucket", "kv_bucket", "preparsed", "preparsed_gloss"]),
}
BUILD = {"E8K": build_e8k, "GUID": build_guid, "GUIDS": build_guids, "KKIND": build_kkind, "KMETH": build_kmeth, "KREG": build_kreg, "KRAT": build_krat}


# ================================================================ collect
def key():
    from dotenv import dotenv_values
    return (dotenv_values(ROOT / ".env.live").get("JEV_API_KEY") or "").strip()


def spent_so_far():
    tot = 0
    for f in CACHE.glob("*.jsonl"):
        for r in jl(f):
            tot += (r.get("in_tok") or 0)
    return tot * PRICE


def call(state, qs, k):
    body = {"state": state, "model": "jev-latest", "questions": qs}
    err = ""
    for att in range(4):
        t0 = time.time()
        try:
            r = requests.post(URL, json=body, headers={"Authorization": f"Bearer {k}"}, timeout=120)
            if r.status_code == 200:
                j = r.json()
                return j, round(time.time() - t0, 3), None
            err = f"HTTP {r.status_code}"
            if r.status_code == 429:
                time.sleep(5 * (att + 1)); continue
        except Exception as exc:  # noqa: BLE001
            err = type(exc).__name__
        time.sleep(2 * (att + 1))
    return None, None, err


def collect(set_name, variants=None, limit=None):
    CACHE.mkdir(parents=True, exist_ok=True)
    items = BUILD[set_name]()
    fn, vs = VARIANTS[set_name]
    k = key()
    _spent[0] = spent_so_far()
    for v in (variants or vs):
        cache = CACHE / f"{set_name}__{v}.jsonl"
        done = {r["id"] for r in jl(cache) if "pred" in r or "ans" in r}
        todo = [r for r in items if r["id"] not in done][:limit]
        if not todo:
            continue
        if _spent[0] >= BUDGET:
            print(f"예산 도달 ${_spent[0]:.3f} — 중단"); return
        mk = fn(v)

        def one(r):
            state, qs, dec = mk(r)
            j, sec, err = call(state, qs, k)
            if j is None:
                return {"id": r["id"], "error": err}
            pred, probs, conf = dec(j["answers"], r)
            it = (j.get("usage") or {}).get("input_tokens")
            return {"id": r["id"], "truth": r["truth"], "pred": pred, "probs": probs, "conf": conf, "sec": sec,
                    "in_tok": it, "model": j.get("model"), "ans": j["answers"] if "noul" in v else None}
        t0 = time.time()
        with cf.ThreadPoolExecutor(6) as ex, cache.open("a", encoding="utf-8") as fh:
            for res in ex.map(one, todo):
                fh.write(json.dumps(res, ensure_ascii=False) + "\n"); fh.flush()
                _spent[0] += (res.get("in_tok") or 0) * PRICE
        print(f"{set_name}/{v}: {len(todo)} calls {time.time()-t0:.0f}s  누적 ${_spent[0]:.3f}", flush=True)


# ================================================================ evaluate
def metrics(rows, labels):
    rows = [r for r in rows if "pred" in r]
    n = len(rows)
    if not n:
        return {}
    y = [r["truth"] for r in rows]; p = [r["pred"] for r in rows]
    acc = float(np.mean([a == b for a, b in zip(y, p)]))
    f1s = []
    for c in labels:
        tp = sum(a == c and b == c for a, b in zip(y, p)); fp = sum(a != c and b == c for a, b in zip(y, p))
        fn = sum(a == c and b != c for a, b in zip(y, p))
        if tp + fn == 0:
            continue
        pr = tp / (tp + fp) if tp + fp else 0.0; rc = tp / (tp + fn)
        f1s.append(2 * pr * rc / (pr + rc) if pr + rc else 0.0)
    out = {"n": n, "acc": acc, "macro_f1": float(np.mean(f1s)) if f1s else None}
    # ECE on max prob
    mp = [(max(r["probs"].values()) if r.get("probs") else None, r["truth"] == r["pred"]) for r in rows]
    mp = [(a, b) for a, b in mp if a is not None]
    if mp:
        a = np.array([x[0] for x in mp]); c = np.array([x[1] for x in mp], float)
        bins = np.clip((a * 10).astype(int), 0, 9)
        out["ece"] = float(sum(abs(c[bins == b].mean() - a[bins == b].mean()) * (bins == b).mean() for b in range(10) if (bins == b).any()))
    cf_ = [(r.get("conf"), r["truth"] == r["pred"]) for r in rows if r.get("conf") is not None]
    for th in (0.5, 0.8, 0.95):
        sel = [ok for c_, ok in cf_ if c_ >= th]
        if cf_:
            out[f"cov{th}"] = len(sel) / len(cf_)
            out[f"acc{th}"] = float(np.mean(sel)) if sel else None
    lat = [r["sec"] for r in rows if r.get("sec")]
    out["lat_med"] = float(np.median(lat)) if lat else None
    tok = [r["in_tok"] for r in rows if r.get("in_tok")]
    out["tok_mean"] = float(np.mean(tok)) if tok else None
    out["usd_per_1k"] = float(np.mean(tok)) * PRICE * 1000 if tok else None
    out["usd_total"] = float(np.sum(tok)) * PRICE if tok else None
    out["models"] = dict(Counter(r.get("model") for r in rows))
    out["errors"] = 0
    return out


LABELS = {"E8K": E8K_LABELS, "GUID": G_LABELS, "GUIDS": G_LABELS, "KKIND": KINDS, "KMETH": M_LABELS, "KREG": R_LABELS, "KRAT": RBUCK}


def confusion(rows, labels):
    df = pd.DataFrame([r for r in rows if "pred" in r])
    if df.empty:
        return pd.DataFrame()
    return pd.crosstab(df.truth, df.pred).reindex(index=labels, columns=labels + [c for c in df.pred.unique() if c not in labels], fill_value=0)


def evaluate():
    res = {}
    for s, (_, vs) in VARIANTS.items():
        for v in vs:
            rows = jl(CACHE / f"{s}__{v}.jsonl")
            if not rows:
                continue
            rows = list({r["id"]: r for r in rows}.values())
            m = metrics(rows, LABELS[s])
            m["errors"] = sum(1 for r in rows if "error" in r)
            res[f"{s}/{v}"] = m
    (OUT / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, m in res.items():
        print(f"{k:32s} n={m.get('n')} acc={m.get('acc', 0):.3f} F1={m.get('macro_f1') or 0:.3f} ece={m.get('ece', float('nan')):.3f} "
              f"cov.8={m.get('cov0.8', float('nan')):.2f} acc.8={m.get('acc0.8') or float('nan'):.3f} lat={m.get('lat_med') or 0:.2f} "
              f"tok={m.get('tok_mean') or 0:.0f} $={m.get('usd_total') or 0:.3f} err={m['errors']}")
    return res


def spot(set_name, n=20):
    items = BUILD[set_name]()
    rng = random.Random(3)
    for r in rng.sample(items, min(n, len(items))):
        t = text_of(set_name, r)
        if set_name in ("GUID", "GUIDS"):
            m = (S_RAISE.search(t) or S_LOWER.search(t) or S_MAINT.search(t)) if set_name == "GUIDS" else (RAISE.search(t) or LOWER.search(t) or MAINT.search(t))
            print(r["truth"], "|", t[max(0, m.start() - 120): m.end() + 80] if m else t[:200]); print("-")
        elif set_name == "KMETH":
            print(r["truth"], "|", snippet(t, r"증자방식", 0, 40))
        elif set_name == "KREG":
            print(r["truth"], "|", r.get("region_raw"), "| parser_foreign=", r.get("parser_foreign"))
        elif set_name == "KRAT":
            print(r["truth"], r["ratio"], "|", snippet(t, r"매출액\s*대비", 60, 30))
        else:
            print(r["truth"], "|", t[:300]); print("-")


# ================================================================ 후속(2026-09-29 코디네이터 지시): 실제 결손 지점 수동 라벨 채점
"""후속 사전등록(라벨·호출 전 고정)
[AMEND] kr_event_signals.jsonl 에서 kind=supply_contract ∧ is_correction ∧ reason=amend_unknown ∧ 저장 본문 있음(34건).
  정답 = Claude Code 수동 판독(정정사항 표 정정전/정정후 직접 읽음) 6종 {amount_up, amount_down, period_only, typo, termination, other}
  + 금액 전/후(원). 규칙 = 현행 parse_amendment_text+contract_amendment_diff(원장 기록값). Jev 레시피 = 정정사유~정정사항 표
  구간만 state, 단일 Choice(설명 포함) + 문서 권장 추출(표 구간 숫자 후보 → 정정전 금액·정정후 금액 Choice 2개, 'none' 포함),
  증감률은 코드 계산. 영향 = 수동 정답으로 금액 +5% 이상이면 positive_increase → 원장에 기록된 quote/liq/시각으로
  decide("supply_contract_amend") 재생(open_n·new_today는 0 가정, 상한이 걸렸을 수 있음 → 상한 표시).
[NEWS] data/news/{kr,us}/*_preopen.json 최근 15개 세션. 층 4개 × 50: KR broad_weak(규칙상 비관련), KR 유지(broad_weak 아님),
  US weak_generic(score_news_item signal_type), US 유지(prompt_eligible). seed 0, 종목당 최대 2건.
  정답 = Claude Code 수동 판독 "이 기사가 해당 종목(회사)에 관한 것인가(주제·주요 대상이거나 그 회사에 직접 영향을 주는 구체 사건)".
  규칙 예측: broad_weak/weak_generic → 비관련, 유지 → 관련. Jev: Noul(설명 criteria) + Choice{about_company, mentions_in_passing, unrelated}.
"""
AMEND = OUT / "sets" / "AMEND.jsonl"
NEWS = OUT / "sets" / "NEWS.jsonl"
NEWS_LABELS = OUT / "news_labels.json"
# 수동 판독 정답(각 본문 정정사항 표를 직접 읽음). (label, before, after)
AMEND_TRUTH = [
    ("amount_up", 394_627_500_000, 399_288_100_000), ("amount_up", 394_627_500_000, 399_288_100_000),
    ("amount_up", 27_631_500_000, 34_028_600_000), ("period_only", None, None), ("amount_up", 193_467_000_000, 212_772_000_000),
    ("period_only", None, None), ("amount_up", 10_457_456_291, 10_709_456_291), ("amount_up", 18_546_220_000, 19_546_220_000),
    ("period_only", None, None), ("period_only", None, None), ("amount_up", 383_225_300_000, 482_351_178_000),
    ("other", None, None), ("period_only", None, None), ("amount_up", 179_129_727_000, 188_034_000_000),
    ("period_only", None, None), ("termination", None, None), ("period_only", None, None),
    ("amount_up", 42_756_846_825, 44_877_170_938), ("amount_up", 48_426_500_000, 58_847_000_000),
    ("amount_up", 30_561_679_000, 32_072_700_000), ("period_only", None, None), ("typo", None, None), ("period_only", None, None),
    ("period_only", None, None), ("amount_up", 2_400_544_888, 2_612_619_176), ("period_only", None, None),
    ("amount_up", 40_198_249_200, 54_470_191_200), ("period_only", None, None), ("period_only", None, None),
    ("period_only", None, None), ("period_only", None, None), ("other", None, None), ("period_only", None, None),
    ("amount_up", 16_689_710_000, 19_933_171_000),
]
A_LABELS = ["amount_up", "amount_down", "period_only", "typo", "termination", "other"]
A_DESC = {"amount_up": "the contract amount is increased (정정후 금액 > 정정전 금액)",
          "amount_down": "the contract amount is decreased",
          "period_only": "only the contract period (start/end date) changes; the amount is unchanged",
          "typo": "a simple clerical correction (e.g. a date of the filing itself, a typo) with no economic change",
          "termination": "the contract is terminated, cancelled, or ended early",
          "other": "other changes: disclosure-deferral dates, counterparty disclosure, revealing a previously withheld amount, etc."}
KDOC = ROOT / "data" / "shadow" / "kr_event_docs"


def amend_segment(t):
    t = re.sub(r"\s+", " ", t)
    k = t.find("정정사유")
    e = t.find("단일판매ㆍ공급계약 체결 1.", k)
    e2 = t.find("단일판매ㆍ공급계약체결 1.", k)
    ends = [x for x in (e, e2) if x > k]
    return t[max(0, k): min(ends) if ends else k + 900][:1200]


def build_amend():
    rows = [json.loads(l) for l in (ROOT / "data/shadow/kr_event_signals.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    s = [r for r in rows if r["kind"] == "supply_contract" and r.get("is_correction") and r["reason"] == "amend_unknown"]
    has = [r for r in s if (KDOC / f"{r['rcept_no']}.txt").exists()]
    assert len(has) == len(AMEND_TRUTH), (len(has), len(AMEND_TRUTH))
    out = []
    for r, (lab, b, a) in zip(has, AMEND_TRUTH):
        out.append({"id": r["rcept_no"], "truth": lab, "before": b, "after": a, "session_date": r["session_date"],
                    "stock": r["stock_code"], "ts_detected": r.get("ts_detected"), "quote": r.get("quote"), "liq": r.get("liq"),
                    "rule_kind": (r.get("fields") or {}).get("kind")})
    wjl(AMEND, out)
    return out


def v_amend(name):
    def mk(r):
        seg = amend_segment((KDOC / f"{r['id']}.txt").read_text(encoding="utf-8"))
        qs = {"q": q_choice("What does this correction (정정) of a Korean supply-contract disclosure change?", A_LABELS, A_DESC)}
        cands = [(v, ctx) for v, ctx in num_candidates(seg, 30) if len(v.replace(",", "")) >= 7]
        opts = {f"c{i}": f"{v} (context: ...{ctx}...)" for i, (v, ctx) in enumerate(cands)}
        opts["none"] = "no contract amount appears in the before/after correction table"
        qs["before"] = {"type": "choice", "instructions": "In the correction table (정정사항: 정정항목 / 정정전 / 정정후), which number is the contract amount BEFORE the correction (정정전 계약금액)?", "criteria": opts}
        qs["after"] = {"type": "choice", "instructions": "In the correction table (정정사항: 정정항목 / 정정전 / 정정후), which number is the contract amount AFTER the correction (정정후 계약금액)?", "criteria": opts}

        def dec(a, r=None, cands=cands):
            x = a["q"]
            def val(ch):
                return None if ch == "none" else float(cands[int(ch[1:])][0].replace(",", ""))
            return x["choice"], x.get("probabilities"), x.get("confidence"), {"before": val(a["before"]["choice"]), "after": val(a["after"]["choice"]),
                                                                              "before_c": a["before"].get("confidence"), "after_c": a["after"].get("confidence")}
        return {"korean_supply_contract_correction_table": seg}, qs, dec
    return mk


NEWS_Q = "Is this news item specifically about the company named in `company` (the company is the subject, or the item reports a concrete event directly affecting that company)?"


def v_news(name):
    def mk(r):
        st = {"company": {"name": r["name"], "ticker": r["ticker"], "market": "Korea" if r["market"] == "KR" else "US"},
              "news_item": {"title": r["title"], "summary": (r.get("content") or "")[:600], "source": r.get("provider") or r.get("source")}}
        qs = {"n": {"type": "noul", "instructions": NEWS_Q,
                    "criteria": {"true": "the company is the subject of the article or directly affected by the specific event reported",
                                 "false": "market/sector/theme roundups, lists of many stocks, articles about another company, or only a passing mention"}},
              "c": q_choice("How does this news item relate to the company named in `company`?", ["about_company", "mentions_in_passing", "unrelated"],
                            {"about_company": "the company is the main subject or directly affected by the specific event",
                             "mentions_in_passing": "the company appears in a list, a market roundup, or a sector/theme story among many",
                             "unrelated": "the article is not about this company at all"})}

        def dec(a, r=None):
            p = a["n"]["noul"]; c = a["c"]
            pred = "relevant" if (p >= 0.5 if name == "noul" else c["choice"] == "about_company") else "irrelevant"
            return pred, ({"relevant": p, "irrelevant": 1 - p} if name == "noul" else c.get("probabilities")), (None if name == "noul" else c.get("confidence")), {"noul": p, "choice": c["choice"]}
        return st, qs, dec
    return mk


def build_news(per=50, max_per_ticker=2):
    import glob
    sys.path.insert(0, str(ROOT))
    from preopen import news_enrichment as ne, news_quality as nq
    strata = defaultdict(list)
    for mk_ in ("kr", "us"):
        files = sorted(glob.glob(str(ROOT / "data" / "news" / mk_ / "*_preopen.json")))[-15:]
        for fp in files:
            d = json.loads(Path(fp).read_text(encoding="utf-8"))
            for tk, ent in (d.get("corp_news") or {}).items():
                if not isinstance(ent, dict):
                    continue
                nm = ent.get("name") or ""
                for it in ent.get("items") or []:
                    if not isinstance(it, dict) or not it.get("title"):
                        continue
                    M = mk_.upper()
                    bw = ne._item_is_broad_weak(M, tk, nm, it)
                    sig = nq.score_news_item(M, tk, nm, it, broad_weak=bw)
                    rec = {"market": M, "ticker": tk, "name": nm, "title": it.get("title"), "content": (it.get("content") or "")[:600],
                           "provider": it.get("provider") or it.get("source"), "date": Path(fp).stem[:10], "broad_weak": bw,
                           "signal_type": sig.get("signal_type"), "prompt_eligible": bool(sig.get("prompt_eligible"))}
                    if M == "KR":
                        strata["KR_broad_weak" if bw else "KR_kept"].append(rec)
                    else:
                        if sig.get("signal_type") == "weak_generic":
                            strata["US_weak_generic"].append(rec)
                        elif sig.get("prompt_eligible"):
                            strata["US_kept"].append(rec)
    rng = random.Random(0); out = []
    pools = {k: len(v) for k, v in strata.items()}
    for k in ("KR_broad_weak", "KR_kept", "US_weak_generic", "US_kept"):
        v = strata[k][:]; rng.shuffle(v); seen = Counter(); titles = set(); n = 0
        for rec in v:
            if seen[rec["ticker"]] >= max_per_ticker or rec["title"] in titles:
                continue
            seen[rec["ticker"]] += 1; titles.add(rec["title"]); n += 1
            rec["stratum"] = k; rec["rule"] = "irrelevant" if k in ("KR_broad_weak", "US_weak_generic") else "relevant"
            rec["id"] = f"{k}_{n:03d}"
            out.append(rec)
            if n >= per:
                break
    print("NEWS pools", pools)
    wjl(NEWS, out)
    return out


def news_items():
    items = jl(NEWS)
    lab = json.loads(NEWS_LABELS.read_text(encoding="utf-8")) if NEWS_LABELS.exists() else {}
    for r in items:
        r["truth"] = lab.get(r["id"])
    return [r for r in items if r["truth"]]


def collect_followup(which):
    CACHE.mkdir(parents=True, exist_ok=True)
    k = key()
    if which == "AMEND":
        items, jobs = jl(AMEND) or build_amend(), [("AMEND", "recipe", v_amend("recipe"))]
    else:
        items, jobs = news_items(), [("NEWS", "noul", v_news("noul")), ("NEWS", "choice", v_news("choice"))]
    for s, v, fn in jobs:
        cache = CACHE / f"{s}__{v}.jsonl"
        done = {r["id"] for r in jl(cache) if "pred" in r}
        todo = [r for r in items if r["id"] not in done]

        def one(r):
            state, qs, dec = fn(r)
            j, sec, err = call(state, qs, k)
            if j is None:
                return {"id": r["id"], "error": err}
            pred, probs, conf, extra = dec(j["answers"], r)
            return {"id": r["id"], "truth": r["truth"], "pred": pred, "probs": probs, "conf": conf, "extra": extra, "sec": sec,
                    "in_tok": (j.get("usage") or {}).get("input_tokens"), "model": j.get("model")}
        with cf.ThreadPoolExecutor(6) as ex, cache.open("a", encoding="utf-8") as fh:
            for res in ex.map(one, todo):
                fh.write(json.dumps(res, ensure_ascii=False) + "\n")
        print(s, v, len(todo))


# ================================================================ 3차(2026-09-29): preopen scorer 뉴스 항이 shadow 랭킹 초과에 기여하나
"""3차 사전등록(계산 전 고정)
[데이터] logs/preopen/{date}_{M}_candidates.jsonl(세션별 마지막 캡처 = 장전 최종 랭킹; 캡처는 전부 개장 전 확인),
  logs/preopen/{date}_{M}_outcome.jsonl(종목별 최대 offset 레코드).
[보유] 소스 기준: preopen outcome 추적기는 개장가→종가(KR 390분·US 385분)까지 기록하고, continuation_shadow 기본 평가 offset은 30분.
  1차 = open_to_close_pct(개장가 진입·종가 청산), 2차 = post_open_30m_return_pct.
[초과] 같은 세션·같은 규모 3분위(extended_dollar_volume, 없으면 prior_day_traded_value) 후보 평균 대비.
[뉴스 항] +0.12 = quality_tags∋direct_news_catalyst, +0.05 = analyst_news, −0.05 = preopen_reason∋weak_news_penalty
  (PREOPEN_WEAK_NEWS_PENALTY 현행 0.05; 과거값 미확인). 반사실 점수 = clip(score − 항, 0, 1)(저장 점수가 이미 clip이면 근사).
[비교] 세션별 top-K(K=5, 15) 평균 초과: 실제 랭킹 − 반사실 랭킹, 날짜 클러스터 t(세션 = 1관측). KR/US 분리.
  보조: 뉴스 항 종류별 후보의 평균 초과(세션 평균의 평균, 날짜 클러스터 t).
[Jev 보정] direct_news 가점 후보의 top_news 제목(없으면 sample_title), weak 감점 후보의 sample_title을 Jev Noul(2차 레시피)로
  관련성 채점. 보정 항: 가점은 관련(≥0.5)일 때만 유지, 감점은 비관련일 때만 유지. 예산 ≤ $1.
[판정] K=5·15 모두 실제−반사실 차이의 t ≥ 2이고 두 시장 중 하나 이상 양수 → 뉴스 항은 수익 레버 후보. 아니면 레버 아님.
"""
import ast


def _load_preopen(M):
    rows = []
    for f in sorted(glob_mod.glob(str(ROOT / "logs" / "preopen" / f"*_{M}_candidates.jsonl"))):
        last = {}
        cap = None
        recs = []
        for l in open(f, encoding="utf-8"):
            try:
                r = json.loads(l)
            except Exception:  # noqa: BLE001
                continue
            recs.append(r)
            cap = max(cap, r.get("captured_at") or "") if cap else r.get("captured_at")
        snap = [r for r in recs if r.get("captured_at") == cap]
        of = f.replace("candidates", "outcome")
        outc = {}
        if Path(of).exists():
            for l in open(of, encoding="utf-8"):
                try:
                    o = json.loads(l)
                except Exception:  # noqa: BLE001
                    continue
                k = o.get("ticker")
                if k not in outc or (o.get("offset_min") or 0) >= (outc[k].get("offset_min") or 0):
                    outc[k] = o
        for r in snap:
            o = outc.get(r.get("ticker"))
            if not o or o.get("open_to_close_pct") is None:
                continue
            qt = set(r.get("quality_tags") or []); pr = set(r.get("preopen_reason") or [])
            term = (0.12 if "direct_news_catalyst" in qt else 0.05 if "analyst_news" in qt else 0.0) - (0.05 if "weak_news_penalty" in pr else 0.0)
            dv = r.get("extended_dollar_volume") or r.get("prior_day_traded_value") or 0
            tn = r.get("top_news")
            if isinstance(tn, str):
                try:
                    tn = ast.literal_eval(tn)
                except Exception:  # noqa: BLE001
                    tn = []
            titles = [x.get("title") for x in (tn or []) if isinstance(x, dict) and x.get("title")]
            rows.append({"M": M, "sess": r.get("session_date"), "ticker": r.get("ticker"), "name": r.get("name"),
                         "score": float(r.get("preopen_score") or 0), "term": term,
                         "kind": "direct" if "direct_news_catalyst" in qt else "analyst" if "analyst_news" in qt else "weak" if "weak_news_penalty" in pr else "none",
                         "dv": float(dv or 0), "ret": float(o["open_to_close_pct"]), "ret30": o.get("post_open_30m_return_pct"),
                         "titles": titles[:2] or ([r.get("news_or_earnings_sample_title")] if r.get("news_or_earnings_sample_title") else [])})
    df = pd.DataFrame(rows)
    df["ret"] = df["ret"].clip(-60, 150)
    df["ret30"] = pd.to_numeric(df["ret30"], errors="coerce").clip(-60, 150)
    df["szb"] = df.groupby("sess")["dv"].transform(lambda s: pd.qcut(s.rank(method="first"), 3, labels=False) if len(s) >= 3 else 0)
    for c in ("ret", "ret30"):
        df[f"ex_{c}"] = df[c] - df.groupby(["sess", "szb"])[c].transform("mean")
    return df


def _dayt(v):
    v = np.asarray([x for x in v if x == x], float)
    if len(v) < 3:
        return float("nan"), float("nan"), len(v)
    return v.mean(), v.mean() / (v.std(ddof=1) / np.sqrt(len(v))) if v.std(ddof=1) > 0 else float("nan"), len(v)


def news_rank_eval(df, score_col_alt, K, col="ex_ret"):
    diffs = []
    for s, g in df.groupby("sess"):
        g = g[g[col].notna()]
        if len(g) < K * 2:
            continue
        a = g.sort_values("score", ascending=False).head(K)[col].mean()
        b = g.sort_values(score_col_alt, ascending=False).head(K)[col].mean()
        diffs.append(a - b)
    return _dayt(diffs), sum(1 for d in diffs if d != 0)


def jev_score_titles(df):
    cache = CACHE / "PREOPEN_NEWS__noul.jsonl"
    done = {r["id"]: r for r in jl(cache) if "noul" in r}
    todo = {}
    for r in df[df.kind.isin(["direct", "weak"])].itertuples():
        for t in r.titles[:1]:
            i = f"{r.M}|{r.ticker}|{t}"
            if i not in done:
                todo[i] = (r.M, r.ticker, r.name, t)
    k = key()
    fn = v_news("noul")

    def one(item):
        i, (M, tk, nm, t) = item
        st, qs, dec = fn({"market": M, "ticker": tk, "name": nm, "title": t, "content": "", "provider": ""})
        qs = {"n": qs["n"]}
        j, sec, err = call(st, qs, k)
        if j is None:
            return {"id": i, "error": err}
        return {"id": i, "noul": j["answers"]["n"]["noul"], "in_tok": (j.get("usage") or {}).get("input_tokens")}
    with cf.ThreadPoolExecutor(6) as ex, cache.open("a", encoding="utf-8") as fh:
        for res in ex.map(one, list(todo.items())):
            fh.write(json.dumps(res, ensure_ascii=False) + "\n")
    done = {r["id"]: r for r in jl(cache) if "noul" in r}
    tok = sum(r.get("in_tok") or 0 for r in done.values())
    print(f"Jev preopen titles: new {len(todo)}, total {len(done)}, ${tok*PRICE:.3f}")
    return done


def preopen_news_study():
    out = {}
    for M in ("KR", "US"):
        df = _load_preopen(M)
        df["score_cf"] = (df.score - df.term).clip(0, 1)
        J = jev_score_titles(df)

        def jterm(r):
            if r.kind not in ("direct", "weak") or not r.titles:
                return r.term
            p = (J.get(f"{r.M}|{r.ticker}|{r.titles[0]}") or {}).get("noul")
            if p is None:
                return r.term
            if r.kind == "direct":
                return 0.12 if p >= 0.5 else 0.0
            return -0.05 if p < 0.5 else 0.0
        df["term_j"] = [jterm(r) for r in df.itertuples()]
        df["score_j"] = (df.score - df.term + df.term_j).clip(0, 1)
        df["jrel"] = [((J.get(f"{r.M}|{r.ticker}|{r.titles[0]}") or {}).get("noul") if r.titles and r.kind in ("direct", "weak") else None) for r in df.itertuples()]
        res = {"n_rows": len(df), "sessions": df.sess.nunique(), "kinds": df.kind.value_counts().to_dict(),
               "clipped_top": int(((df.score >= 1.0) & (df.term > 0)).sum())}
        for col in ("ex_ret", "ex_ret30"):
            for K in (5, 15):
                (m, t, n), nz = news_rank_eval(df, "score_cf", K, col)
                (mj, tj, nj), nzj = news_rank_eval(df, "score_j", K, col)
                res[f"{col}_K{K}"] = {"actual_minus_nonews": [m, t, n, nz], "actual_minus_jev": [mj, tj, nj, nzj]}
            for kd in ("direct", "analyst", "weak", "none"):
                v = df[df.kind == kd].groupby("sess")[col].mean()
                res[f"{col}_kind_{kd}"] = list(_dayt(v.values))
            for kd, lo, hi in (("direct", 0.5, 1.01), ("direct", -1, 0.5), ("weak", 0.5, 1.01), ("weak", -1, 0.5)):
                sub = df[(df.kind == kd) & df.jrel.notna() & (df.jrel >= lo) & (df.jrel < hi)]
                res[f"{col}_{kd}_jrel_{'rel' if lo >= 0.5 else 'irr'}"] = list(_dayt(sub.groupby("sess")[col].mean().values)) + [len(sub)]
        out[M] = res
        df.drop(columns=["titles"]).to_csv(OUT / f"preopen_news_{M}.csv.gz", index=False)
    (OUT / "preopen_news_results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1, default=float))


import glob as glob_mod  # noqa: E402

if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "preopen_news":
        preopen_news_study(); sys.exit(0)
    if a and a[0] == "followup":
        if a[1] == "build_amend":
            build_amend()
        elif a[1] == "build_news":
            build_news()
        else:
            collect_followup(a[1])
        sys.exit(0)
    if a and a[0] == "build":
        for s in (a[1:] or BUILD):
            BUILD[s]()
    elif a and a[0] == "spot":
        spot(a[1], int(a[2]) if len(a) > 2 else 20)
    elif a and a[0] == "collect":
        s = a[1]; vs = a[2].split(",") if len(a) > 2 and a[2] != "all" else None
        lim = int(a[3]) if len(a) > 3 else None
        collect(s, vs, lim)
    else:
        evaluate()
