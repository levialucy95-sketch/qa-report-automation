"""
3단계: 모델별 분류 결과를 정답지(answer_key.csv)로 채점하고 비교

입력:  output/root_cause_*.csv (step2 결과, 모델 개수만큼)
       answer_key.csv
출력:  output/model_comparison.csv  모델별 정확도, 원인별 정확도, 속도
       output/model_errors_<모델>.csv  틀린 건만 모아둔 표 (프롬프트 개선 재료)
       화면에 비교 요약 출력

실행:  python step3_compare_models.py
"""
import csv
import glob
import os
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")
CAUSES = ["SOP 모호", "정책 이해 부족", "단순 실수", "정책 변경 미반영", "Arbi 판정 오류"]


def load(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def score(preds, truth):
    """전체 정확도 + 원인별 재현율(그 원인인 건 중 몇 %를 맞혔나)."""
    hit, per_total, per_hit, errors = 0, Counter(), Counter(), []
    for p in preds:
        t = truth.get(p["case_id"])
        if t is None:
            continue
        per_total[t] += 1
        if p["predicted_cause"] == t:
            hit += 1
            per_hit[t] += 1
        else:
            errors.append({**p, "true_cause": t})
    n = sum(per_total.values())
    recall = {c: (per_hit[c] / per_total[c] if per_total[c] else None) for c in CAUSES}
    return hit / n, n, recall, errors


def main():
    truth = {r["case_id"]: r["true_root_cause"] for r in load(os.path.join(HERE, "answer_key.csv"))}
    files = sorted(glob.glob(os.path.join(OUT, "root_cause_*.csv")))
    if not files:
        print("output/root_cause_*.csv 가 없어요. step2를 먼저 실행하세요.")
        return

    rows = []
    for path in files:
        model = os.path.basename(path)[len("root_cause_"):-len(".csv")]
        preds = load(path)
        acc, n, recall, errors = score(preds, truth)
        secs = [float(p["seconds"]) for p in preds]
        fails = sum(p["predicted_cause"] == "분류 실패" for p in preds)
        rows.append({
            "model": model, "cases": n, "accuracy": round(acc, 4),
            **{f"recall_{c}": (round(v, 3) if v is not None else "") for c, v in recall.items()},
            "format_failures": fails,
            "avg_sec_per_case": round(sum(secs) / len(secs), 2),
            "total_sec": round(sum(secs), 1),
        })
        if errors:
            with open(os.path.join(OUT, f"model_errors_{model}.csv"), "w",
                      newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=list(errors[0]))
                w.writeheader()
                w.writerows(errors)

    with open(os.path.join(OUT, "model_comparison.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    print("=== 모델 비교 (정답지 기준) ===")
    for r in rows:
        print(f"\n[{r['model']}] 정확도 {r['accuracy']:.1%} ({r['cases']}건) | "
              f"건당 {r['avg_sec_per_case']}초, 총 {r['total_sec']}초 | 형식 실패 {r['format_failures']}건")
        for c in CAUSES:
            v = r[f"recall_{c}"]
            print(f"   {c}: {v:.0%}" if v != "" else f"   {c}: -")
    print("\n저장: output/model_comparison.csv, output/model_errors_<모델>.csv")


if __name__ == "__main__":
    main()
