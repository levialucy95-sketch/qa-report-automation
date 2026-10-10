"""
1단계: KPI 자동 계산 + 혼동 패턴 / 심사원별 취약점 추출 (LLM 사용 없음)

입력:  raw_cases.csv (같은 폴더)
출력:  output/kpi_by_week.csv        주차별 정확도, 보정 정확도
       output/confusion_top.csv      자주 헷갈리는 태그 조합 상위
       output/reviewer_weakness.csv  심사원별 정확도와 취약 태그
       output/summary.txt            한 장 요약 (리포트 초안 재료)

실행:  python step1_kpi_analysis.py
"""
import csv
import os
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")
TOP_N = 5
WEAK_GAP = 0.05  # 전체 평균보다 5%p 이상 낮으면 '취약 심사원'


def load(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write(name, rows):
    with open(os.path.join(OUT, name), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def kpi_by_week(cases):
    """
    정확도      = 1 - (불일치 건수 / 샘플 수)
    보정 정확도  = 어필 '성공' 건은 심사원이 맞았던 것이므로 불일치에서 제외하고 다시 계산
    """
    weeks = defaultdict(lambda: Counter())
    for c in cases:
        w = weeks[int(c["week"])]
        w["total"] += 1
        if c["reviewer_tag"] != c["arbi_tag"]:
            w["diff"] += 1
            if c["appeal_result"]:
                w[c["appeal_result"]] += 1
    rows = []
    for wk in sorted(weeks):
        w = weeks[wk]
        rows.append({
            "week": wk,
            "total_sampled": w["total"],
            "differences": w["diff"],
            "appeal_success": w["성공"],
            "appeal_fail": w["실패"],
            "grey_area": w["그레이"],
            "accuracy": round(1 - w["diff"] / w["total"], 4),
            "adjusted_accuracy": round(1 - (w["diff"] - w["성공"]) / w["total"], 4),
        })
    return rows


def confusion_top(cases):
    """'심사원 태그 → Arbi 태그' 조합별 건수. 어떤 정책 경계에서 헷갈리는지 보여줌."""
    pairs = Counter(
        (c["reviewer_tag"], c["arbi_tag"])
        for c in cases if c["reviewer_tag"] != c["arbi_tag"]
    )
    total = sum(pairs.values())
    return [
        {"reviewer_tag": a, "arbi_tag": b, "count": n, "share": round(n / total, 3)}
        for (a, b), n in pairs.most_common(TOP_N)
    ]


def reviewer_weakness(cases):
    """심사원별 정확도 + 가장 많이 틀린 태그 조합. 전체 평균보다 크게 낮으면 표시."""
    by_r = defaultdict(list)
    for c in cases:
        by_r[c["reviewer_id"]].append(c)
    overall = sum(c["reviewer_tag"] == c["arbi_tag"] for c in cases) / len(cases)

    rows = []
    for r in sorted(by_r):
        cs = by_r[r]
        diffs = [c for c in cs if c["reviewer_tag"] != c["arbi_tag"]]
        acc = 1 - len(diffs) / len(cs)
        worst = Counter((c["reviewer_tag"], c["arbi_tag"]) for c in diffs).most_common(1)
        rows.append({
            "reviewer_id": r,
            "cases": len(cs),
            "differences": len(diffs),
            "accuracy": round(acc, 4),
            "gap_vs_avg": round(acc - overall, 4),
            "top_confusion": f"{worst[0][0][0]} → {worst[0][0][1]} ({worst[0][1]}건)" if worst else "",
            "flag": "취약" if acc < overall - WEAK_GAP else "",
        })
    return rows, overall


def summary_text(kpi, conf, rev, overall):
    first, last = kpi[0], kpi[-1]
    best_drop = min(kpi[1:], key=lambda r: r["accuracy"] - kpi[kpi.index(r) - 1]["accuracy"])
    prev = kpi[kpi.index(best_drop) - 1]
    weak = [r for r in rev if r["flag"]]
    lines = [
        "=== 주간 QA 요약 (자동 생성) ===",
        f"정확도: {first['week']}주차 {first['accuracy']:.1%} → {last['week']}주차 {last['accuracy']:.1%}",
        f"보정 정확도(어필 성공 반영): {last['week']}주차 {last['adjusted_accuracy']:.1%}",
        f"가장 큰 하락: {prev['week']}→{best_drop['week']}주차 "
        f"({prev['accuracy']:.1%} → {best_drop['accuracy']:.1%}) — 원인 확인 필요",
        "",
        "자주 헷갈리는 태그 조합 (심사원 → Arbi):",
        *[f"  {i+1}. {c['reviewer_tag']} → {c['arbi_tag']}: {c['count']}건 ({c['share']:.0%})"
          for i, c in enumerate(conf)],
        "",
        f"전체 평균 정확도 {overall:.1%} 대비 {WEAK_GAP:.0%}p 이상 낮은 심사원:",
        *[f"  {r['reviewer_id']}: {r['accuracy']:.1%} (주요 혼동: {r['top_confusion']})" for r in weak],
    ]
    if not weak:
        lines.append("  없음")
    return "\n".join(lines)


def main():
    os.makedirs(OUT, exist_ok=True)
    cases = load(os.path.join(HERE, "raw_cases.csv"))

    kpi = kpi_by_week(cases)
    conf = confusion_top(cases)
    rev, overall = reviewer_weakness(cases)

    write("kpi_by_week.csv", kpi)
    write("confusion_top.csv", conf)
    write("reviewer_weakness.csv", rev)

    text = summary_text(kpi, conf, rev, overall)
    with open(os.path.join(OUT, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
