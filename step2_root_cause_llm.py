"""
2단계: 어필 사유 텍스트를 로컬 LLM(Ollama)으로 원인 분류

입력:  raw_cases.csv (같은 폴더)
출력:  output/root_cause_<모델명>.csv   케이스별 분류 결과 + 처리 시간
       output/cause_by_week_<모델명>.csv 주차별 원인 비율 (정책 변경 스파이크 확인용)

실행:  python step2_root_cause_llm.py --model qwen2.5:7b
       python step2_root_cause_llm.py --model qwen2.5:3b
       (먼저 Ollama가 켜져 있어야 함. 처음 한 번:  ollama pull qwen2.5:3b)

설계 메모
- answer_key.csv(정답지)는 여기서 절대 읽지 않음. 채점은 step3에서만.
- 어필 결과(성공/실패/그레이)도 모델에 주지 않음. 결과를 보면 원인을 '컨닝'할 수 있어서,
  순수하게 사유 문장만 읽고 판단하는 능력을 측정.
- 추가 설치 없음 (파이썬 기본 모듈만 사용).
"""
import argparse
import csv
import json
import os
import time
import urllib.request
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")
OLLAMA_URL = "http://localhost:11434/api/chat"

CAUSES = {
    "SOP 모호": "가이드라인/SOP에 기준이 없거나 두 가지로 해석될 수 있어서 판단이 갈림",
    "정책 이해 부족": "심사원이 정책 정의나 적용 범위를 잘못 이해함",
    "단순 실수": "클릭 실수, 재확인 누락 등 판단과 무관한 조작 실수",
    "정책 변경 미반영": "정책이 바뀐 걸 늦게 알거나 이전 기준을 적용함",
    "Arbi 판정 오류": "심사원 판단이 맞고 Arbi(검수자) 쪽이 근거를 놓쳤다고 주장함",
}
CAUSE_LIST = list(CAUSES)

SYSTEM_PROMPT = (
    "너는 콘텐츠 심사 QA 분석가다. 심사원이 제출한 어필 사유를 읽고, "
    "불일치의 근본 원인을 아래 5개 중 정확히 하나로 분류한다.\n\n"
    + "\n".join(f"- {k}: {v}" for k, v in CAUSES.items())
    + "\n\n반드시 JSON으로만 답한다: {\"cause\": \"<위 5개 중 하나>\"}"
)

# Ollama 0.5+ 는 JSON 스키마를 주면 그 형식으로만 답하게 강제할 수 있음
FORMAT_SCHEMA = {
    "type": "object",
    "properties": {"cause": {"type": "string", "enum": CAUSE_LIST}},
    "required": ["cause"],
}


def ask_llm(model, reviewer_tag, arbi_tag, reason, timeout=120):
    user_msg = (
        f"심사원 태그: {reviewer_tag}\n"
        f"Arbi 태그: {arbi_tag}\n"
        f"어필 사유: {reason}"
    )
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "format": FORMAT_SCHEMA,
        "stream": False,
        "options": {"temperature": 0},  # 같은 입력이면 같은 답 (재현성)
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content = json.loads(resp.read())["message"]["content"]
    return parse_cause(content)


def parse_cause(content):
    """모델 답에서 원인을 뽑음. 형식이 깨져도 최대한 살리고, 안 되면 '분류 실패'."""
    try:
        cause = json.loads(content).get("cause", "")
    except (json.JSONDecodeError, AttributeError):
        cause = content
    cause = str(cause).strip()
    if cause in CAUSES:
        return cause
    for c in CAUSE_LIST:  # 앞뒤에 다른 말이 붙은 경우
        if c in cause:
            return c
    return "분류 실패"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="예: qwen2.5:7b")
    ap.add_argument("--limit", type=int, default=0, help="테스트용: 앞에서 N건만 처리")
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(HERE, "raw_cases.csv"), encoding="utf-8-sig") as f:
        appeals = [c for c in csv.DictReader(f) if c["appealed"] == "Y"]
    if args.limit:
        appeals = appeals[:args.limit]

    # 처음 한 번은 모델을 메모리에 올리느라 느림 → 측정에서 빼려고 미리 한 번 호출
    print(f"[{args.model}] 모델 로딩 중...")
    try:
        ask_llm(args.model, "정상", "스팸", "테스트")
    except Exception as e:
        print(f"Ollama 연결 실패: {e}\n→ Ollama 실행 여부와 'ollama list'에 {args.model}이 있는지 확인하세요.")
        return

    rows, started = [], time.perf_counter()
    for i, c in enumerate(appeals, 1):
        t0 = time.perf_counter()
        try:
            cause = ask_llm(args.model, c["reviewer_tag"], c["arbi_tag"], c["appeal_reason"])
        except Exception as e:  # 한 건 실패해도 전체는 계속
            cause = "분류 실패"
            print(f"  {c['case_id']} 오류: {e}")
        sec = time.perf_counter() - t0
        rows.append({
            "case_id": c["case_id"], "week": c["week"],
            "reviewer_tag": c["reviewer_tag"], "arbi_tag": c["arbi_tag"],
            "appeal_reason": c["appeal_reason"], "appeal_result": c["appeal_result"],
            "predicted_cause": cause, "seconds": round(sec, 2),
        })
        if i % 20 == 0 or i == len(appeals):
            print(f"  {i}/{len(appeals)} 완료")
    total = time.perf_counter() - started

    safe = args.model.replace(":", "_").replace("/", "_")
    path = os.path.join(OUT, f"root_cause_{safe}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # 주차별 원인 비율: 5주차 '정책 변경 미반영' 스파이크가 보여야 정상
    by_week = defaultdict(Counter)
    for r in rows:
        by_week[int(r["week"])][r["predicted_cause"]] += 1
    week_rows = []
    for wk in sorted(by_week):
        n = sum(by_week[wk].values())
        week_rows.append({"week": wk, "appeals": n,
                          **{c: by_week[wk][c] for c in CAUSE_LIST + ["분류 실패"]}})
    with open(os.path.join(OUT, f"cause_by_week_{safe}.csv"), "w",
              newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(week_rows[0]))
        w.writeheader()
        w.writerows(week_rows)

    dist = Counter(r["predicted_cause"] for r in rows)
    print(f"\n완료: {len(rows)}건, 총 {total:.1f}초 (건당 평균 {total/len(rows):.2f}초)")
    for c, n in dist.most_common():
        print(f"  {c}: {n}건")
    print(f"저장: {path}")


if __name__ == "__main__":
    main()
