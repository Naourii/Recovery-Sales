"""
lead_analysis.py
Lead Analysis Agent: infers why a deal was lost and how engaged the lead is.
Input : one customer dict (see data/customers.json)
Output: analysis dict
"""

import json
import sys
from pathlib import Path

DATA_FILE = Path("data/customers.json")

# keyword -> weight, per loss reason. Matched against lowercased crm_notes.
LOSS_RULES = {
    "price_too_high": {
        "budget": 3, "discount": 3, "expensive": 3, "annual cost": 2,
        "price": 2, "cost": 1,
    },
    "chose_competitor": {
        "competitor": 3, "another vendor": 3, "signed with": 3, "evaluating": 1,
    },
    "bad_timing": {
        "priorities changed": 3, "follow up in": 3, "restructuring": 3,
        "bandwidth": 2, "implementation window": 3, "audit": 2, "this quarter": 1,
    },
    "no_response": {
        "stopped replying": 3, "went silent": 3, "unanswered": 3,
        "never responded": 3, "no calls returned": 3, "went quiet": 1,
    },
    "missing_feature": {
        "integration": 3, "feature": 2, "reporting": 2, "multi-language": 2,
        "on-premise": 3, "cloud only": 3, "we do not offer": 2, "missing": 2,
    },
    "champion_left": {
        "left the company": 3, "reassigned": 3, "sponsor": 2,
        "no replacement": 3, "contact left": 3,
    },
}

# Suggested recovery angle per loss reason (consumed later by the Strategy Agent).
RECOVERY_ANGLE = {
    "price_too_high": "Offer a flexible payment plan, a smaller starter package, or a limited-time discount.",
    "chose_competitor": "Highlight differentiators and offer a side-by-side comparison or free onboarding.",
    "bad_timing": "Re-engage on the date they requested with a short, low-effort check-in.",
    "no_response": "Switch channel (phone/LinkedIn) and send a short, value-first message.",
    "missing_feature": "Share the product roadmap or a workaround; re-engage when the feature ships.",
    "champion_left": "Identify and reach out to a new stakeholder at the company.",
    "unknown": "Run a manual review and a soft check-in to learn the real blocker.",
}


def classify_loss_reason(notes: str, open_rate: float, meetings: int) -> tuple[str, float, list[str]]:
    """Return (reason, confidence 0-1, matched keywords) from CRM notes."""
    text = notes.lower()
    scores: dict[str, int] = {}
    matched: dict[str, list[str]] = {}

    for reason, keywords in LOSS_RULES.items():
        hits = [(kw, w) for kw, w in keywords.items() if kw in text]
        if hits:
            scores[reason] = sum(w for _, w in hits)
            matched[reason] = [kw for kw, _ in hits]

    if not scores:
        # No textual evidence: fall back to engagement behavior.
        if open_rate < 0.3 and meetings <= 1:
            return "no_response", 0.35, []
        return "unknown", 0.0, []

    best = max(scores, key=scores.get)
    total = sum(scores.values())
    share = scores[best] / total                 # how dominant the winner is
    strength = min(scores[best] / 5, 1.0)        # how much evidence it has
    confidence = round(0.5 * share + 0.5 * strength, 2)
    return best, confidence, matched[best]


def engagement_level(open_rate: float, meetings: int) -> str:
    score = open_rate * 50 + min(meetings, 4) * 12.5   # 0-100
    if score >= 60:
        return "high"
    if score >= 30:
        return "medium"
    return "low"


def urgency_level(days_inactive: int) -> str:
    if days_inactive <= 30:
        return "warm"       # recent, act fast
    if days_inactive <= 90:
        return "cooling"    # still recoverable, act soon
    return "cold"           # needs a strong re-engagement hook


def analyze_lead(customer: dict) -> dict:
    sent = customer.get("emails_sent", 0)
    opened = customer.get("emails_opened", 0)
    meetings = customer.get("meetings_held", 0)
    days_inactive = customer.get("days_inactive", 0)

    open_rate = round(min(opened / sent, 1.0), 2) if sent else 0.0

    reason, confidence, signals = classify_loss_reason(
        customer.get("crm_notes", ""), open_rate, meetings
    )
    engagement = engagement_level(open_rate, meetings)
    urgency = urgency_level(days_inactive)

    summary = (
        f"{customer['name']} ({customer['company']}) was lost at '{customer['stage_lost_at']}' "
        f"for a ${customer['deal_value']:,.0f} {customer['plan_interested']} deal. "
        f"Likely reason: {reason.replace('_', ' ')} (confidence {confidence:.0%}). "
        f"Engagement is {engagement}; lead is {urgency} after {days_inactive} days of inactivity."
    )

    return {
        "customer_id": customer["id"],
        "loss_reason": reason,
        "confidence": confidence,
        "evidence": signals,
        "email_open_rate": open_rate,
        "engagement_level": engagement,
        "urgency": urgency,
        "recommended_angle": RECOVERY_ANGLE[reason],
        "summary": summary,
    }


def load_customers() -> list[dict]:
    if not DATA_FILE.exists():
        sys.exit(f"{DATA_FILE} not found. Run: python generate_data.py")
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    customers = load_customers()

    # Optional: python lead_analysis.py 7  -> analyze only customer id 7
    if len(sys.argv) > 1:
        target = int(sys.argv[1])
        customer = next((c for c in customers if c["id"] == target), None)
        if not customer:
            sys.exit(f"No customer with id {target}")
        print(json.dumps(analyze_lead(customer), indent=2))
        return

    correct = 0
    misses = []
    for c in customers:
        result = analyze_lead(c)
        if result["loss_reason"] == c["_true_loss_reason"]:
            correct += 1
        else:
            misses.append((c, result))

    print("Sample analysis:")
    print(json.dumps(analyze_lead(customers[0]), indent=2))
    print(f"\nAccuracy vs ground truth: {correct}/{len(customers)} ({correct / len(customers):.0%})")

    if misses:
        print("\nMisclassified:")
        for c, r in misses:
            print(f"  id={c['id']} true={c['_true_loss_reason']} predicted={r['loss_reason']}")
            print(f"    notes: {c['crm_notes']}")


if __name__ == "__main__":
    main()