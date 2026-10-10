"""
4단계: 주간 QA One-pager 자동 생성 (HTML 한 장, 브라우저에서 열고 PDF로 인쇄 가능)

입력:  raw_cases.csv
       output/root_cause_qwen2.5_7b.csv   (step2 결과, --causes 로 변경 가능)
출력:  output/onepager_week<N>.html

실행:  python step4_onepager.py --week 5
       python step4_onepager.py --week 8 --no-llm     (LLM 없이 규칙 기반 문장만)

설계 원칙: "숫자는 코드가, 문장은 LLM이"
- KPI, 순위, 비율 같은 숫자는 전부 코드가 계산함.
- LLM은 계산된 사실만 받아서 요약 문장과 액션 아이템을 씀.
- LLM 문장에 '사실 목록에 없는 숫자'가 들어가면 환각으로 보고 버린 뒤 규칙 기반 문장으로 대체함.
"""
import argparse
import csv
import html
import json
import os
import re
import urllib.request
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")
OLLAMA_URL = "http://localhost:11434/api/chat"
CAUSES = ["SOP 모호", "정책 이해 부족", "단순 실수", "정책 변경 미반영", "Arbi 판정 오류"]
WEAK_GAP = 0.05        # 주간 평균보다 5%p 이상 낮으면 '주의 심사원'
MIN_REVIEWER_CASES = 15  # 표본이 너무 적은 심사원은 판단 보류
SPIKE_MIN = 5          # 원인 급증: 이번 주 5건 이상이면서
SPIKE_RATIO = 2.0      #           직전 3주 평균의 2배 이상


def load(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def pct(x):
    return f"{x * 100:.1f}%"


# ---------------------------------------------------------------- 숫자 계산 (코드)
def weekly_accuracy(cases):
    tot, diff, succ = Counter(), Counter(), Counter()
    for c in cases:
        w = int(c["week"])
        tot[w] += 1
        if c["reviewer_tag"] != c["arbi_tag"]:
            diff[w] += 1
            succ[w] += c["appeal_result"] == "성공"
    return {w: {"total": tot[w], "diff": diff[w],
                "acc": 1 - diff[w] / tot[w],
                "adj": 1 - (diff[w] - succ[w]) / tot[w]} for w in sorted(tot)}


def build_facts(cases, causes, week):
    acc = weekly_accuracy(cases)
    if week not in acc:
        raise SystemExit(f"{week}주차 데이터가 없어요. 가능한 주차: {list(acc)}")
    wk = [c for c in cases if int(c["week"]) == week]
    diffs = [c for c in wk if c["reviewer_tag"] != c["arbi_tag"]]
    prev = acc.get(week - 1)

    # 혼동 패턴 상위 3
    conf = Counter((c["reviewer_tag"], c["arbi_tag"]) for c in diffs).most_common(3)

    # 주의 심사원 (이번 주 기준)
    by_r = defaultdict(list)
    for c in wk:
        by_r[c["reviewer_id"]].append(c)
    weak = []
    for r, cs in sorted(by_r.items()):
        if len(cs) < MIN_REVIEWER_CASES:
            continue
        a = sum(c["reviewer_tag"] == c["arbi_tag"] for c in cs) / len(cs)
        if a < acc[week]["acc"] - WEAK_GAP:
            weak.append((r, a, len(cs)))

    # 원인 분포 (LLM 분류 결과) + 급증 감지
    cause_week = defaultdict(Counter)
    for r in causes:
        cause_week[int(r["week"])][r["predicted_cause"]] += 1
    this = cause_week[week]
    base_weeks = [w for w in range(week - 3, week) if w in cause_week]
    spikes = []
    for c in CAUSES:
        base = (sum(cause_week[w][c] for w in base_weeks) / len(base_weeks)) if base_weeks else 0
        if this[c] >= SPIKE_MIN and this[c] >= SPIKE_RATIO * max(base, 1):
            spikes.append((c, this[c], base))

    # SOP 개선 후보: 이번 주 그레이 에리어 어필
    grey = [c for c in wk if c["appeal_result"] == "그레이"]

    return {
        "week": week, "acc": acc, "cur": acc[week], "prev": prev,
        "conf": conf, "n_diff": len(diffs), "weak": weak,
        "cause_counts": this, "n_appeals": sum(this.values()),
        "spikes": spikes, "grey": grey,
    }


def facts_text(f):
    """LLM에 넘길 사실 목록. 여기 없는 숫자를 LLM이 쓰면 환각으로 간주."""
    cur, prev = f["cur"], f["prev"]
    lines = [f"{f['week']}주차 정확도 {pct(cur['acc'])}, 보정 정확도 {pct(cur['adj'])}",
             f"샘플 {cur['total']}건 중 불일치 {cur['diff']}건"]
    if prev:
        d = (cur["acc"] - prev["acc"]) * 100
        lines.append(f"전주 정확도 {pct(prev['acc'])}, 변화 {d:+.1f}%p")
    for (a, b), n in f["conf"]:
        lines.append(f"혼동 패턴: 심사원 {a} → Arbi {b} {n}건")
    for r, a, n in f["weak"]:
        lines.append(f"주의 심사원 {r}: 이번 주 정확도 {pct(a)} ({n}건)")
    for c in CAUSES:
        if f["cause_counts"][c]:
            lines.append(f"어필 원인 '{c}' {f['cause_counts'][c]}건")
    for c, n, base in f["spikes"]:
        lines.append(f"급증: '{c}' {n}건 (직전 3주 평균 {base:.1f}건)")
    lines.append(f"그레이 에리어 어필 {len(f['grey'])}건 (SOP 개선 후보)")
    return "\n".join(lines)


# ---------------------------------------------------------------- 문장 생성 (LLM)
def rule_based_text(f):
    cur, prev = f["cur"], f["prev"]
    s = f"{f['week']}주차 정확도는 {pct(cur['acc'])}"
    if prev:
        d = (cur["acc"] - prev["acc"]) * 100
        s += f"로 전주 대비 {abs(d):.1f}%p {'상승' if d >= 0 else '하락'}했습니다."
    else:
        s += "입니다."
    if f["spikes"]:
        c, n, _ = f["spikes"][0]
        s += f" 어필 원인 중 '{c}'가 {n}건으로 급증했습니다."
    actions = []
    for c, _, _ in f["spikes"]:
        actions.append(f"'{c}' 급증 원인 확인 및 관련 공지·교육 재실시")
    if f["conf"]:
        (a, b), _ = f["conf"][0]
        actions.append(f"'{a} ↔ {b}' 경계 기준을 SOP에 예시로 보강")
    if f["weak"]:
        actions.append(f"{', '.join(r for r, _, _ in f['weak'])} 대상 1:1 피드백")
    if f["grey"]:
        actions.append(f"그레이 에리어 {len(f['grey'])}건 검토 후 SOP 개정 여부 결정")
    return s, actions[:3]


def llm_text(model, facts):
    prompt = (
        "너는 콘텐츠 심사 QA 리드다. 아래 사실만 근거로 주간 보고 요약을 쓴다.\n"
        "규칙: 사실 목록에 없는 숫자는 절대 쓰지 말 것. 추측 금지. 존댓말.\n"
        "summary: 2~3문장, 가장 중요한 변화부터.\n"
        "actions: 다음 주 실행할 구체적 액션 3개 (각 한 문장).\n\n"
        f"[사실]\n{facts}"
    )
    schema = {"type": "object",
              "properties": {"summary": {"type": "string"},
                             "actions": {"type": "array", "items": {"type": "string"}}},
              "required": ["summary", "actions"]}
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "format": schema, "stream": False,
                       "options": {"temperature": 0}}).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        out = json.loads(json.loads(resp.read())["message"]["content"])
    return out["summary"].strip(), [a.strip() for a in out["actions"]][:3]


def numbers_in(text):
    return set(re.findall(r"\d+(?:\.\d+)?", text))


def unsupported_numbers(text, facts):
    """LLM 문장 속 숫자 중 사실 목록에 없는 것 (환각 검사)."""
    allowed = numbers_in(facts) | {"1", "2", "3"}  # 순서 표현(첫째/1:1 등) 허용
    return sorted(numbers_in(text) - allowed)


# ---------------------------------------------------------------- HTML
def sparkline(acc, week):
    """8주 정확도 추이. 단일 시리즈라 범례 없음, 이번 주 점만 강조. 마우스 올리면 값 표시."""
    weeks = sorted(acc)
    W, H, P = 520, 150, 28
    lo = min(a["acc"] for a in acc.values()) - 0.02
    hi = min(1.0, max(a["acc"] for a in acc.values()) + 0.02)
    x = lambda i: P + i * (W - 2 * P) / max(1, len(weeks) - 1)
    y = lambda v: H - P - (v - lo) / (hi - lo) * (H - 2 * P)
    pts = " ".join(f"{x(i):.1f},{y(acc[w]['acc']):.1f}" for i, w in enumerate(weeks))
    dots, labels = [], []
    for i, w in enumerate(weeks):
        cur = w == week
        dots.append(
            f'<g><circle cx="{x(i):.1f}" cy="{y(acc[w]["acc"]):.1f}" r="14" fill="transparent"/>'
            f'<circle cx="{x(i):.1f}" cy="{y(acc[w]["acc"]):.1f}" r="{5 if cur else 3.5}" '
            f'class="{"dot cur" if cur else "dot"}"/>'
            f'<title>{w}주차 정확도 {pct(acc[w]["acc"])}</title></g>')
        labels.append(f'<text x="{x(i):.1f}" y="{H - 8}" class="{"axis cur" if cur else "axis"}" '
                      f'text-anchor="middle">{w}주</text>')
    v = acc[week]["acc"]
    i = weeks.index(week)
    tag = (f'<text x="{x(i):.1f}" y="{y(v) - 12:.1f}" text-anchor="middle" class="val">{pct(v)}</text>')
    return (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="주차별 정확도 추이">'
            f'<line x1="{P}" x2="{W - P}" y1="{H - P}" y2="{H - P}" class="base"/>'
            f'<polyline points="{pts}" class="line"/>{"".join(dots)}{"".join(labels)}{tag}</svg>')


def cause_bars(f):
    total = max(1, f["n_appeals"])
    spiked = {c for c, _, _ in f["spikes"]}
    mx = max([f["cause_counts"][c] for c in CAUSES] + [1])
    rows = []
    for c in sorted(CAUSES, key=lambda c: -f["cause_counts"][c]):
        n = f["cause_counts"][c]
        flag = '<span class="flag">▲ 급증</span>' if c in spiked else ""
        rows.append(
            f'<div class="brow" title="{c}: {n}건 ({n / total:.0%})">'
            f'<span class="blabel">{c}</span>'
            f'<span class="btrack"><span class="bar" style="width:{n / mx * 100:.0f}%"></span></span>'
            f'<span class="bval">{n}건 · {n / total:.0%}</span>{flag}</div>')
    return "".join(rows)


def render(f, summary, actions, source, model_note):
    e = html.escape
    cur, prev = f["cur"], f["prev"]
    delta = ""
    if prev:
        d = (cur["acc"] - prev["acc"]) * 100
        delta = f'<div class="delta">전주 대비 {"▲" if d >= 0 else "▼"} {abs(d):.1f}%p</div>'
    conf = "".join(f"<li><b>{e(a)} → {e(b)}</b> <span class='muted'>{n}건</span></li>"
                   for (a, b), n in f["conf"]) or "<li class='muted'>없음</li>"
    weak = "".join(f"<li><b>{r}</b> {pct(a)} <span class='muted'>({n}건)</span></li>"
                   for r, a, n in f["weak"]) or "<li class='muted'>해당 없음</li>"
    grey = "".join(f"<li><b>{e(c['reviewer_tag'])} ↔ {e(c['arbi_tag'])}</b> "
                   f"<span class='muted'>{e(c['case_id'])}</span><br>{e(c['appeal_reason'])}</li>"
                   for c in f["grey"][:5]) or "<li class='muted'>없음</li>"
    more = f"<p class='muted'>외 {len(f['grey']) - 5}건</p>" if len(f["grey"]) > 5 else ""
    acts = "".join(f"<li>{e(a)}</li>" for a in actions)
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QA 주간 리포트 {f['week']}주차</title>
<style>
:root{{--bg:#fcfcfb;--card:#ffffff;--ink:#0b0b0b;--ink2:#52514e;--muted:#8a8984;--line:#e6e5e1;
--s1:#2a78d6;--track:#eef3fb;--warn:#b25e00}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1a1a19;--card:#222221;--ink:#fff;--ink2:#c3c2b7;
--muted:#8f8e86;--line:#383835;--s1:#3987e5;--track:#26303d;--warn:#f0a35a}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.55 "Pretendard","Apple SD Gothic Neo","Malgun Gothic",sans-serif}}
.page{{max-width:860px;margin:0 auto;padding:28px 16px}}
header{{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;
border-bottom:2px solid var(--ink);padding-bottom:10px;margin-bottom:18px}}
h1{{font-size:20px;margin:0}}h2{{font-size:13px;letter-spacing:.04em;color:var(--ink2);margin:0 0 10px}}
.muted{{color:var(--muted)}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}}
.span{{grid-column:1/-1}}.hero{{display:flex;gap:28px;align-items:flex-end;flex-wrap:wrap}}
.big{{font-size:40px;font-weight:700;line-height:1}}.sub{{color:var(--ink2)}}
.delta{{font-weight:600;color:var(--ink2)}}
svg{{width:100%;height:auto;display:block}}.line{{fill:none;stroke:var(--s1);stroke-width:2}}
.dot{{fill:var(--s1);stroke:var(--card);stroke-width:2}}.dot.cur{{stroke-width:2.5}}
.base{{stroke:var(--line)}}.axis{{fill:var(--muted);font-size:11px}}.axis.cur{{fill:var(--ink);font-weight:700}}
.val{{fill:var(--ink);font-size:12px;font-weight:700}}
.brow{{display:grid;grid-template-columns:110px 1fr 86px auto;align-items:center;gap:8px;margin:6px 0}}
.blabel{{color:var(--ink2)}}.btrack{{background:var(--track);border-radius:4px;height:12px}}
.bar{{display:block;height:12px;background:var(--s1);border-radius:4px;min-width:2px}}
.bval{{color:var(--ink2);font-variant-numeric:tabular-nums}}.flag{{color:var(--warn);font-weight:700;font-size:12px}}
ul,ol{{margin:0;padding-left:18px}}li{{margin:4px 0}}
.summary{{font-size:15px}}.actions li{{margin:6px 0}}
footer{{margin-top:16px;font-size:12px;color:var(--muted)}}
@media (max-width:640px){{.grid{{grid-template-columns:1fr}}.brow{{grid-template-columns:1fr auto;row-gap:2px}}.blabel{{grid-column:1}}.bval{{grid-column:2}}
.btrack{{grid-column:1/-1;grid-row:2}}.flag{{grid-column:1/-1}}}}
@media print{{body{{background:#fff}}.card{{break-inside:avoid}}}}
</style></head><body><div class="page">
<header><h1>QA 주간 리포트 · {f['week']}주차</h1>
<span class="muted">가상 데이터 기반 데모 · 자동 생성</span></header>
<div class="grid">
<section class="card span"><h2>요약</h2><p class="summary">{e(summary)}</p>
<p class="muted" style="margin:6px 0 0;font-size:12px">{e(source)}</p></section>
<section class="card"><h2>정확도</h2><div class="hero">
<div><div class="big">{pct(cur['acc'])}</div>{delta}</div>
<div class="sub">보정 정확도 <b>{pct(cur['adj'])}</b><br>샘플 {cur['total']}건 · 불일치 {cur['diff']}건</div></div></section>
<section class="card"><h2>8주 추이</h2>{sparkline(f['acc'], f['week'])}</section>
<section class="card span"><h2>어필 원인 분포 ({f['n_appeals']}건, LLM 분류)</h2>{cause_bars(f)}</section>
<section class="card"><h2>자주 헷갈린 태그 (심사원 → Arbi)</h2><ol>{conf}</ol></section>
<section class="card"><h2>주의 심사원</h2><ul>{weak}</ul>
<p class="muted" style="font-size:12px;margin-top:8px">주간 평균보다 {WEAK_GAP:.0%}p 이상 낮음, {MIN_REVIEWER_CASES}건 이상만</p></section>
<section class="card span"><h2>SOP 개선 후보 (그레이 에리어 {len(f['grey'])}건)</h2><ul>{grey}</ul>{more}</section>
<section class="card span"><h2>다음 주 액션</h2><ol class="actions">{acts}</ol></section>
</div>
<footer>정확도 = 1 − 불일치/샘플 · 보정 정확도 = 어필 성공 건을 불일치에서 제외 · {e(model_note)}</footer>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int, default=0, help="리포트 주차 (기본: 마지막 주)")
    ap.add_argument("--model", default="qwen2.5:7b", help="요약 문장 작성 모델")
    ap.add_argument("--causes", default="root_cause_qwen2.5_7b.csv", help="step2 결과 파일명")
    ap.add_argument("--no-llm", action="store_true", help="LLM 없이 규칙 기반 문장만")
    args = ap.parse_args()

    cases = load(os.path.join(HERE, "raw_cases.csv"))
    causes_path = os.path.join(OUT, args.causes)
    if not os.path.exists(causes_path):
        raise SystemExit(f"{causes_path} 가 없어요. step2를 먼저 실행하세요.")
    causes = load(causes_path)
    week = args.week or max(int(c["week"]) for c in cases)

    f = build_facts(cases, causes, week)
    facts = facts_text(f)

    summary, actions = rule_based_text(f)
    source = "요약: 규칙 기반 생성"
    if not args.no_llm:
        try:
            s, a = llm_text(args.model, facts)
            bad = unsupported_numbers(s + " " + " ".join(a), facts)
            if bad:
                source = f"요약: LLM 문장에 근거 없는 숫자 {bad} 발견 → 폐기하고 규칙 기반으로 대체"
            elif len(a) < 3:
                source = "요약: LLM 액션 아이템 부족 → 규칙 기반으로 대체"
            else:
                summary, actions = s, a
                source = f"요약: {args.model} 생성 · 숫자 검증 통과"
        except Exception as e:
            source = f"요약: LLM 호출 실패({type(e).__name__}) → 규칙 기반으로 대체"

    path = os.path.join(OUT, f"onepager_week{week}.html")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(render(f, summary, actions, source, f"원인 분류: {args.causes}"))
    print(facts, "\n")
    print(source)
    print(f"저장: {path}")


if __name__ == "__main__":
    main()
