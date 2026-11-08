"""
recovery_scoring.py
Recovery Scoring Engine: transparent, rule-based score (0-100) for how likely
a lost deal is worth recovering.
Input : customer dict (data/customers.json) + analysis dict from analyze_lead()
Output: score, priority label, per-factor breakdown, short explanation
"""

import sys
from lead_analysis import analyze_lead, load_customers

# Max points per factor (sum = 100)
MAX_RECENCY = 25
MAX_INTEREST = 25
MAX_VALUE = 20
MAX_HISTORY = 15
MAX_RECOVERABILITY = 15

# Purchase-history proxy: how far the deal progressed before it was lost.
STAGE_POINTS = {
    "Qualified": 3,
    "Demo Completed": 7,
    "Proposal Sent": 11,
    "Negotiation": 15,
}

# How recoverable each loss reason typically is (max 15, scaled by confidence).
REASON_POINTS = {
    "bad_timing": 15,
    "price_too_high": 12,
    "champion_left": 11,
    "no_response": 8,
    "missing_feature": 6,
    "chose_competitor": 5,
    "unknown": 4,
}

HIGH_THRESHOLD = 65
MEDIUM_THRESHOLD = 40


def recency_points(days_inactive: int) -> int:
    if days_inactive <= 30:
        return 25
    if days_inactive <= 60:
        return 20
    if days_inactive <= 90:
        return 14
    if days_inactive <= 150:
        return 8
    return 3


def interest_points(open_rate: float, meetings: int) -> float:
    # Up to 12.5 from email opens, up to 12.5 from meetings (capped at 4).
    return open_rate * 12.5 + (min(meetings, 4) / 4) * 12.5


def value_points(deal_value: float) -> int:
    if deal_value >= 60_000:
        return 20
    if deal_value >= 30_000:
        return 16
    if deal_value >= 15_000:
        return 12
    if deal_value >= 8_000:
        return 8
    return 4


def purchase_history_points(stage_lost_at: str) -> int:
    return STAGE_POINTS.get(stage_lost_at, 0)


def recoverability_points(loss_reason: str, confidence: float) -> float:
    base = REASON_POINTS.get(loss_reason, REASON_POINTS["unknown"])
    return base * (0.6 + 0.4 * confidence)  # low-confidence diagnoses count for less


def priority_label(score: float) -> str:
    if score >= HIGH_THRESHOLD:
        return "High"
    if score >= MEDIUM_THRESHOLD:
        return "Medium"
    return "Low"


def _strength(points: float, max_points: float) -> str:
    ratio = points / max_points
    if ratio >= 0.7:
        return "strong"
    if ratio >= 0.4:
        return "moderate"
    return "weak"


def calculate_recovery_score(customer: dict, analysis: dict) -> dict:
    days = customer.get("days_inactive", 0)
    meetings = customer.get("meetings_held", 0)
    deal_value = customer.get("deal_value", 0)
    stage = customer.get("stage_lost_at", "")

    recency = recency_points(days)
    interest = interest_points(analysis.get("email_open_rate", 0.0), meetings)
    value = value_points(deal_value)
    history = purchase_history_points(stage)
    recoverability = recoverability_points(
        analysis.get("loss_reason", "unknown"), analysis.get("confidence", 0.0)
    )

    raw = recency + interest + value + history + recoverability
    score = round(max(0.0, min(100.0, raw)), 1)  # hard clamp to 0-100
    label = priority_label(score)

    reason = analysis.get("loss_reason", "unknown").replace("_", " ")
    explanation = (
        f"{label} priority ({score}/100): "
        f"{_strength(recency, MAX_RECENCY)} recency ({days} days inactive), "
        f"{_strength(interest, MAX_INTEREST)} interest "
        f"({analysis.get('email_open_rate', 0):.0%} open rate, {meetings} meetings), "
        f"{_strength(value, MAX_VALUE)} deal value (${deal_value:,.0f}), "
        f"{_strength(history, MAX_HISTORY)} deal progress (lost at {stage}), "
        f"{_strength(recoverability, MAX_RECOVERABILITY)} recoverability (loss reason: {reason})."
    )

    return {
        "customer_id": customer["id"],
        "recovery_score": score,
        "priority": label,
        "breakdown": {
            "recency": round(recency, 1),
            "interest": round(interest, 1),
            "deal_value": round(value, 1),
            "purchase_history": round(history, 1),
            "recoverability": round(recoverability, 1),
        },
        "explanation": explanation,
    }


def main() -> None:
    customers = load_customers()

    # Optional: python recovery_scoring.py 7 -> score only customer id 7
    if len(sys.argv) > 1:
        target = int(sys.argv[1])
        customer = next((c for c in customers if c["id"] == target), None)
        if not customer:
            sys.exit(f"No customer with id {target}")
        import json
        print(json.dumps(calculate_recovery_score(customer, analyze_lead(customer)), indent=2))
        return

    results = []
    for c in customers:
        r = calculate_recovery_score(c, analyze_lead(c))
        assert 0 <= r["recovery_score"] <= 100, f"Score out of range for id {c['id']}"
        results.append((c, r))

    results.sort(key=lambda x: x[1]["recovery_score"], reverse=True)

    print(f"All {len(results)} scores are within 0-100.\n")

    print("Top 5 recovery opportunities:")
    for c, r in results[:5]:
        print(f"  #{c['id']} {c['name']} ({c['company']}): {r['recovery_score']} [{r['priority']}]")
        print(f"    {r['explanation']}")

    print("\nBottom 3:")
    for c, r in results[-3:]:
        print(f"  #{c['id']} {c['name']} ({c['company']}): {r['recovery_score']} [{r['priority']}]")
        print(f"    {r['explanation']}")

    counts = {"High": 0, "Medium": 0, "Low": 0}
    for _, r in results:
        counts[r["priority"]] += 1
    print(f"\nPriority distribution: {counts}")

    scores = [r["recovery_score"] for _, r in results]
    print(f"Min {min(scores)} | Max {max(scores)} | Avg {sum(scores) / len(scores):.1f}")


if __name__ == "__main__":
    main()