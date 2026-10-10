"""
QA 리포트 자동화 포트폴리오용 가상 데이터 생성기.
실제 고객사 데이터·정책명·사유 문장은 전혀 포함하지 않음 (전부 지어낸 값).

출력:
  raw_cases.csv      - 케이스별 로우데이터 (파이프라인 입력)
  weekly_kpi.csv     - 주차별 KPI 요약 (파이프라인 입력)
  answer_key.csv     - 어필 사유의 '정답' 원인 (LLM 분류 정확도 채점용, 파이프라인에 넣지 말 것)
"""
import csv
import random
from datetime import date, timedelta

random.seed(42)  # 같은 데이터가 매번 나오도록 고정

WEEKS = 8
CASES_PER_WEEK = 300
START = date(2026, 6, 1)  # 1주차 월요일 (가상)
POLICY_CHANGE_WEEK = 5    # 정책 변경 발생 주차 (가상 시나리오)
SOP_FIX_WEEK = 4          # SOP 개정 주차 (이후 'SOP 모호' 원인 감소)

TAGS = ["정상", "스팸", "폭력", "선정성", "허위정보", "혐오표현"]
TAG_WEIGHTS = [50, 15, 8, 10, 9, 8]

# 헷갈리기 쉬운 태그 쌍 (정답 → 심사원이 잘못 붙이기 쉬운 태그)
CONFUSABLE = {
    "정상": ["선정성", "스팸", "허위정보"],
    "스팸": ["정상"],
    "폭력": ["혐오표현"],
    "선정성": ["정상"],
    "허위정보": ["정상"],
    "혐오표현": ["폭력", "정상"],
}

# 심사원 10명: 기본 오류율이 서로 다름 (R07, R09가 취약)
REVIEWERS = {f"R{i:02d}": rate for i, rate in enumerate(
    [0.12, 0.15, 0.10, 0.18, 0.14, 0.11, 0.30, 0.16, 0.26, 0.13], start=1)}

# 원인 유형별 어필 사유 템플릿 (태그 이름은 {a}=심사원 태그, {b}=Arbi 태그)
REASONS = {
    "SOP 모호": [
        "가이드라인에 이런 경우가 명확히 안 나와 있어서 {a}로 판단했습니다.",
        "SOP 예시가 {a}와 {b} 둘 다로 읽힐 수 있다고 생각합니다.",
        "비슷한 케이스가 지난주에는 {a}로 처리됐는데 기준이 애매합니다.",
        "맥락상 {a}로 볼 여지가 있고 문서에 경계 기준이 없습니다.",
        "팀 내에서도 의견이 갈리는 유형이라 {a}로 태깅했습니다.",
    ],
    "정책 이해 부족": [
        "{a}에 해당한다고 생각했는데 {b} 기준을 잘못 이해했던 것 같습니다.",
        "{b}의 정의가 이렇게 넓은 줄 몰랐습니다.",
        "영상 일부만 보고 {a}로 판단했습니다. 전체 맥락 기준인지 몰랐어요.",
        "풍자 콘텐츠는 {a}로 보는 줄 알았습니다.",
    ],
    "단순 실수": [
        "태그 선택할 때 클릭 실수가 있었습니다.",
        "드롭다운에서 바로 위 항목을 잘못 눌렀습니다.",
        "시간에 쫓겨 재확인을 못 했습니다. 제 실수 맞습니다.",
    ],
    "정책 변경 미반영": [
        "이번 주 업데이트 공지를 늦게 확인해서 이전 기준({a})으로 했습니다.",
        "변경 전 기준으로는 {a}가 맞는 케이스입니다.",
        "새 기준 적용일이 언제부터인지 헷갈렸습니다.",
    ],
    "Arbi 판정 오류": [
        "영상 후반부에 {a} 근거가 분명히 있습니다. 타임스탬프 확인 부탁드립니다.",
        "{b}로 볼 요소가 없고, SOP 3.2항 예시와 동일하게 {a}입니다.",
        "Arbi 쪽에서 설명란 텍스트를 놓친 것 같습니다. {a}가 맞습니다.",
        "동일 계정의 다른 케이스도 {a}로 확정됐습니다.",
    ],
}

# 원인별 어필 결과 분포 (성공 / 실패 / 그레이)
RESULT_DIST = {
    "SOP 모호": [0.10, 0.20, 0.70],
    "정책 이해 부족": [0.05, 0.90, 0.05],
    "단순 실수": [0.00, 1.00, 0.00],
    "정책 변경 미반영": [0.05, 0.70, 0.25],
    "Arbi 판정 오류": [0.85, 0.05, 0.10],
}
# 원인별 '어필을 하는' 비율 (단순 실수는 잘 안 함)
APPEAL_RATE = {
    "SOP 모호": 0.75, "정책 이해 부족": 0.40, "단순 실수": 0.15,
    "정책 변경 미반영": 0.55, "Arbi 판정 오류": 0.90,
}


def cause_weights(week):
    """주차에 따라 원인 비율이 달라짐 (시나리오: SOP 개정 효과, 정책 변경 스파이크)."""
    w = {"SOP 모호": 35, "정책 이해 부족": 25, "단순 실수": 20,
         "정책 변경 미반영": 2, "Arbi 판정 오류": 8}
    if week >= SOP_FIX_WEEK:
        w["SOP 모호"] = 12
    if week == POLICY_CHANGE_WEEK:
        w["정책 변경 미반영"] = 30
    elif week == POLICY_CHANGE_WEEK + 1:
        w["정책 변경 미반영"] = 10
    return w


def error_multiplier(week):
    """개선 활동으로 주차가 갈수록 오류율 감소 (정책 변경 주차는 일시 상승)."""
    m = max(0.25, 1.6 - 0.2 * (week - 1))
    if week == POLICY_CHANGE_WEEK:
        m *= 1.5
    return m


def fix_josa(text):
    """태그 이름 뒤 조사를 받침에 맞게 고침. 예: '정상로' → '정상으로', '정상가' → '정상이'."""
    def has_batchim(word):
        code = ord(word[-1]) - 0xAC00
        return 0 <= code < 11172 and code % 28 != 0
    def is_rieul(word):  # ㄹ받침은 '로'가 맞음
        return (ord(word[-1]) - 0xAC00) % 28 == 8
    pairs = {"로": "으로", "와": "과", "가": "이"}
    for tag in TAGS:
        for plain, after_batchim in pairs.items():
            right = after_batchim if has_batchim(tag) and not (plain == "로" and is_rieul(tag)) else plain
            for wrong in (plain, after_batchim):
                text = text.replace(tag + wrong, tag + right) if wrong != right else text
    return text


def pick(dist_dict):
    keys = list(dist_dict)
    return random.choices(keys, weights=[dist_dict[k] for k in keys])[0]


raw_rows, key_rows, kpi_rows = [], [], []
case_no = 0

for week in range(1, WEEKS + 1):
    week_start = START + timedelta(weeks=week - 1)
    counts = {"diff": 0, "성공": 0, "실패": 0, "그레이": 0}

    for _ in range(CASES_PER_WEEK):
        case_no += 1
        case_id = f"C{case_no:05d}"
        reviewer = random.choice(list(REVIEWERS))
        review_date = week_start + timedelta(days=random.randint(0, 4))
        truth = random.choices(TAGS, weights=TAG_WEIGHTS)[0]

        err_p = min(0.6, REVIEWERS[reviewer] * error_multiplier(week))
        reviewer_tag, arbi_tag = truth, truth
        appealed, reason, result, cause = "N", "", "", ""

        if random.random() < err_p:
            cause = pick(cause_weights(week))
            wrong = random.choice(CONFUSABLE[truth])
            if cause == "Arbi 판정 오류":
                # 심사원이 맞았고 Arbi가 틀린 경우
                arbi_tag = wrong
            else:
                reviewer_tag = wrong
            counts["diff"] += 1

            if random.random() < APPEAL_RATE[cause]:
                appealed = "Y"
                reason = fix_josa(random.choice(REASONS[cause]).format(a=reviewer_tag, b=arbi_tag))
                result = random.choices(["성공", "실패", "그레이"],
                                        weights=RESULT_DIST[cause])[0]
                counts[result] += 1
                key_rows.append({"case_id": case_id, "true_root_cause": cause})

        raw_rows.append({
            "case_id": case_id, "week": week, "review_date": review_date.isoformat(),
            "reviewer_id": reviewer, "reviewer_tag": reviewer_tag, "arbi_tag": arbi_tag,
            "appealed": appealed, "appeal_reason": reason, "appeal_result": result,
        })

    kpi_rows.append({
        "week": week, "week_start": week_start.isoformat(),
        "total_sampled": CASES_PER_WEEK, "differences": counts["diff"],
        "appeal_success": counts["성공"], "appeal_fail": counts["실패"],
        "grey_area": counts["그레이"],
        "accuracy": round(1 - counts["diff"] / CASES_PER_WEEK, 4),
    })


def write(path, rows):
    # utf-8-sig: 엑셀에서 열어도 한글이 안 깨짐
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


write("raw_cases.csv", raw_rows)
write("weekly_kpi.csv", kpi_rows)
write("answer_key.csv", key_rows)

for r in kpi_rows:
    print(r["week"], r["differences"], r["accuracy"], r["appeal_success"], r["appeal_fail"], r["grey_area"])
print("appeals:", len(key_rows))
