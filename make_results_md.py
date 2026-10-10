"""
실행 결과를 RESULTS.md 한 장으로 정리 (README에서 링크).
숫자는 전부 output/ 폴더의 실제 실행 결과에서 읽음. 직접 쓰는 숫자 없음.

실행:  python make_results_md.py
"""
import csv
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")


def load(name):
    with open(os.path.join(OUT, name), encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def pct(v):
    return f"{float(v) * 100:.1f}%"


def main():
    kpi = load("kpi_by_week.csv")
    models = load("model_comparison.csv")
    reviewers = [r for r in load("reviewer_weakness.csv") if r["flag"]]
    conf = load("confusion_top.csv")
    causes = ["SOP 모호", "정책 이해 부족", "단순 실수", "정책 변경 미반영", "Arbi 판정 오류"]

    lines = ["# 실행 결과", "",
             "> `make_results_md.py`가 output 폴더의 실제 실행 결과로 자동 작성한 문서입니다. "
             "데이터는 전부 가상입니다.", "",
             "## 1. KPI (코드 계산)", "",
             "| 주차 | 샘플 | 불일치 | 정확도 | 보정 정확도 |", "|---|---|---|---|---|"]
    for r in kpi:
        lines.append(f"| {r['week']} | {r['total_sampled']} | {r['differences']} | "
                     f"{pct(r['accuracy'])} | {pct(r['adjusted_accuracy'])} |")
    lines += ["", "**자주 헷갈린 태그 (심사원 → Arbi):** " +
              ", ".join(f"{c['reviewer_tag']} → {c['arbi_tag']} {c['count']}건" for c in conf[:3]),
              "", "**취약 심사원:** " +
              (", ".join(f"{r['reviewer_id']} {pct(r['accuracy'])}" for r in reviewers) or "없음"),
              "", "## 2. 어필 원인 분류 모델 비교 (정답지 채점)", "",
              "| 모델 | 정확도 | " + " | ".join(causes) + " | 형식 실패 | 건당 시간 |",
              "|---|---|" + "---|" * len(causes) + "---|---|"]
    for m in models:
        rec = " | ".join(pct(m[f"recall_{c}"]) if m[f"recall_{c}"] else "-" for c in causes)
        lines.append(f"| {m['model']} | **{pct(m['accuracy'])}** | {rec} | "
                     f"{m['format_failures']} | {m['avg_sec_per_case']}초 |")
    lines += ["", "원인별 수치는 '그 원인인 건 중 맞힌 비율'(재현율)입니다.", "",
              "## 3. One-pager 요약 문장 검증", ""]
    for name in sorted(os.listdir(OUT)):
        m = re.fullmatch(r"onepager_week(\d+)\.html", name)
        if m:
            with open(os.path.join(OUT, name), encoding="utf-8") as f:
                src = re.search(r"요약: [^<]*", f.read())
            lines.append(f"- {m.group(1)}주차: {src.group(0) if src else '정보 없음'}")

    path = os.path.join(HERE, "RESULTS.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"저장: {path}")


if __name__ == "__main__":
    main()
