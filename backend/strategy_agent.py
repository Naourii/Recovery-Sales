"""
strategy_agent.py
Strategy Agent: deterministic, rule-based choice of recovery strategy.
Input : customer dict, analysis dict (analyze_lead), score dict (calculate_recovery_score)
Output: strategy, reasoning, recommended_channel, priority, next_action (+ customer_id, warnings)
"""

import json
import sys

from lead_analysis import RECOVERY_ANGLE, analyze_lead, load_customers
from recovery_scoring import HIGH_THRESHOLD, MEDIUM_THRESHOLD, calculate_recovery_score

# --- Strategies -------------------------------------------------------------
FRIENDLY = "Friendly follow-up"
INFO = "Provide additional product information"
CONCERN = "Address a possible customer concern"
ESCALATE = "Escalate to a human sales representative"
NO_CONTACT = "No contact"
STRATEGIES = {FRIENDLY, INFO, CONCERN, ESCALATE, NO_CONTACT}

# --- Tunable rules ----------------------------------------------------------
ESCALATION_DEAL_VALUE = 30_000      # High-priority deals at/above this go to a human
CHAMPION_LEFT_MIN_VALUE = 15_000    # relationship rebuild is worth a human above this
MIN_CONFIDENCE_FOR_TARGETED = 0.5   # below this, don't assume a specific concern
FATIGUE_EMAILS_SENT = 12            # lots of emails sent...
FATIGUE_MAX_OPEN_RATE = 0.15        # ...almost none opened -> stop emailing

TIMING = {"warm": "within 24 hours", "cooling": "within 3 days", "cold": "within 7 days"}
CONCERN_REASONS = {"price_too_high", "chose_competitor"}
INFO_REASONS = {"missing_feature"}

TRUE_VALUES = {"true", "yes", "y", "1", "opted_out", "opt-out", "unsubscribed"}
FALSE_VALUES = {"false", "no", "n", "0", ""}


# --- Helpers ----------------------------------------------------------------
def _to_float(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default  # reject NaN


def _is_opted_out(customer: dict, warnings: list[str]) -> bool:
    raw = customer.get("opted_out")
    if raw is None:
        warnings.append("No 'opted_out' field on record; assuming the customer has not opted out.")
        return False
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw != 0
    text = str(raw).strip().lower()
    if text in FALSE_VALUES:
        return False
    if text in TRUE_VALUES:
        return True
    warnings.append(f"Unrecognized opted_out value {raw!r}; treating as opted out.")
    return True


def _priority_from_score(score: dict, value: float) -> str:
    label = score.get("priority")
    if label in ("High", "Medium", "Low"):
        return label
    if value >= HIGH_THRESHOLD:
        return "High"
    if value >= MEDIUM_THRESHOLD:
        return "Medium"
    return "Low"


def _timing(analysis: dict, days_inactive) -> str:
    urgency = analysis.get("urgency")
    if urgency not in TIMING:
        days = _to_float(days_inactive, 0.0)
        urgency = "warm" if days <= 30 else "cooling" if days <= 90 else "cold"
    return TIMING[urgency]


def _channel(strategy: str, loss_reason: str) -> str:
    if strategy == ESCALATE:
        return "phone"
    if loss_reason == "no_response":
        return "linkedin"   # email already failed; switch channel (matches RECOVERY_ANGLE)
    return "email"


def _result(customer_id, strategy, reasoning, channel, priority, next_action, warnings) -> dict:
    return {
        "customer_id": customer_id,
        "strategy": strategy,
        "reasoning": reasoning,
        "recommended_channel": channel,
        "priority": priority,
        "next_action": next_action,
        "warnings": warnings,
    }


def _no_contact(customer_id, reasoning, next_action, warnings) -> dict:
    return _result(customer_id, NO_CONTACT, reasoning, "none", "None", next_action, warnings)


# --- Main function ----------------------------------------------------------
def choose_recovery_strategy(customer: dict, analysis: dict, score: dict) -> dict:
    warnings: list[str] = []

    # 1. Invalid customer record: nothing safe to do.
    if not isinstance(customer, dict) or customer.get("id") is None:
        return _no_contact(
            None,
            "Customer record is missing or invalid.",
            "Fix the customer data before any outreach.",
            ["Invalid customer record."],
        )
    cid = customer["id"]

    # 2. Opt-out always wins, before any other rule.
    if _is_opted_out(customer, warnings):
        return _no_contact(
            cid,
            "Customer has opted out of communication. Recovery score is irrelevant.",
            "Do not contact. Keep the record flagged as opted out in the CRM.",
            warnings,
        )

    # 3. Default handling for missing analysis / score.
    analysis = analysis if isinstance(analysis, dict) else {}
    score = score if isinstance(score, dict) else {}

    loss_reason = analysis.get("loss_reason")
    if loss_reason not in RECOVERY_ANGLE:
        warnings.append("Missing or unknown loss_reason in analysis; using 'unknown'.")
        loss_reason = "unknown"
    confidence = _to_float(analysis.get("confidence"), 0.0)

    score_value = _to_float(score.get("recovery_score"))
    if score_value is None:
        return _no_contact(
            cid,
            "No valid recovery score available, so the lead cannot be prioritized.",
            "Run analyze_lead() and calculate_recovery_score() first, or review manually.",
            warnings + ["Missing or invalid recovery_score."],
        )
    score_value = max(0.0, min(100.0, score_value))
    priority = _priority_from_score(score, score_value)

    deal_value = _to_float(customer.get("deal_value"), 0.0)
    stage = customer.get("stage_lost_at", "")
    emails_sent = _to_float(customer.get("emails_sent"), 0.0)
    open_rate = _to_float(analysis.get("email_open_rate"), 0.0)
    when = _timing(analysis, customer.get("days_inactive"))
    reason_text = loss_reason.replace("_", " ")
    angle = RECOVERY_ANGLE[loss_reason]

    # 4. Outreach fatigue: a high score does NOT override this.
    if emails_sent >= FATIGUE_EMAILS_SENT and open_rate < FATIGUE_MAX_OPEN_RATE:
        return _no_contact(
            cid,
            f"Score is {score_value}/100 ({priority}), but {int(emails_sent)} emails were sent "
            f"with a {open_rate:.0%} open rate. More outreach is likely to be ignored or hurt the brand.",
            "Pause outreach for 30 days, then re-evaluate or try a non-email channel via a human.",
            warnings,
        )

    # 5. Low potential: not worth spending outreach on.
    if priority == "Low":
        return _no_contact(
            cid,
            f"Low recovery potential ({score_value}/100). Likely loss reason: {reason_text}.",
            "Add to a long-term nurture list and re-score in 60 days.",
            warnings,
        )

    # 6. Human escalation for high-stakes or relationship-driven deals.
    if priority == "High" and (deal_value >= ESCALATION_DEAL_VALUE or stage == "Negotiation"):
        strategy = ESCALATE
        reasoning = (
            f"High priority ({score_value}/100) on a ${deal_value:,.0f} deal lost at '{stage}'. "
            f"A personal touch is justified. Likely loss reason: {reason_text}."
        )
    elif loss_reason == "champion_left" and deal_value >= CHAMPION_LEFT_MIN_VALUE:
        strategy = ESCALATE
        reasoning = (
            f"Internal sponsor left and the deal is worth ${deal_value:,.0f}. "
            "A rep needs to find and build a relationship with a new stakeholder."
        )
    # 7. Targeted strategies, only when the diagnosis is trustworthy.
    elif loss_reason in CONCERN_REASONS and confidence >= MIN_CONFIDENCE_FOR_TARGETED:
        strategy = CONCERN
        reasoning = (
            f"{priority} priority ({score_value}/100). Likely concern: {reason_text} "
            f"(diagnosis confidence {confidence:.0%})."
        )
    elif loss_reason in INFO_REASONS and confidence >= MIN_CONFIDENCE_FOR_TARGETED:
        strategy = INFO
        reasoning = (
            f"{priority} priority ({score_value}/100). The lead was lost to a product gap "
            f"({reason_text}, confidence {confidence:.0%}); relevant product information may reopen the deal."
        )
    # 8. Default: low-pressure check-in.
    else:
        strategy = FRIENDLY
        reasoning = (
            f"{priority} priority ({score_value}/100). Loss reason is '{reason_text}' "
            f"(confidence {confidence:.0%}), so a low-pressure check-in is the safest first step."
        )

    channel = _channel(strategy, loss_reason)
    next_actions = {
        ESCALATE: f"Assign to a sales rep for personal {channel} outreach {when}. Angle: {angle}",
        CONCERN: f"Prepare a message addressing the {reason_text} concern and send via {channel} {when}. Angle: {angle}",
        INFO: f"Send relevant product material (roadmap, integration docs) via {channel} {when}. Angle: {angle}",
        FRIENDLY: f"Send a short, low-pressure check-in via {channel} {when}. Angle: {angle}",
    }

    return _result(cid, strategy, reasoning, channel, priority, next_actions[strategy], warnings)


# --- Test runner ------------------------------------------------------------
def _build_rows(customers: list[dict]) -> list[tuple[dict, dict, dict]]:
    rows = []
    for c in customers:
        a = analyze_lead(c)
        rows.append((c, a, calculate_recovery_score(c, a)))
    return rows


def _show(title: str, customer: dict, score: dict, result: dict) -> None:
    print(f"=== {title} ===")
    print(f"Customer #{customer['id']} {customer['name']} ({customer['company']}) | "
          f"{customer['days_inactive']} days inactive | ${customer['deal_value']:,.0f} | "
          f"score {score['recovery_score']} [{score['priority']}]")
    print(json.dumps(result, indent=2))
    print()


def main() -> None:
    customers = load_customers()
    rows = _build_rows(customers)

    # Optional: python strategy_agent.py 7 -> strategy for customer id 7 only
    if len(sys.argv) > 1:
        target = int(sys.argv[1])
        row = next((r for r in rows if r[0]["id"] == target), None)
        if not row:
            sys.exit(f"No customer with id {target}")
        print(json.dumps(choose_recovery_strategy(*row), indent=2))
        return

    # Test 1: high-priority, most inactive (fallback: highest score)
    highs = [r for r in rows if r[2]["priority"] == "High"]
    high_row = max(highs, key=lambda r: r[0]["days_inactive"]) if highs else max(rows, key=lambda r: r[2]["recovery_score"])
    c, a, s = high_row
    res = choose_recovery_strategy(c, a, s)
    assert res["strategy"] in STRATEGIES
    _show("TEST 1: high-priority inactive customer" + ("" if highs else " (no High found; used top score)"), c, s, res)

    # Test 2: medium-priority customer (fallback: median score)
    mediums = [r for r in rows if r[2]["priority"] == "Medium"]
    ordered = sorted(rows, key=lambda r: r[2]["recovery_score"])
    med_row = mediums[0] if mediums else ordered[len(ordered) // 2]
    c, a, s = med_row
    res = choose_recovery_strategy(c, a, s)
    assert res["strategy"] in STRATEGIES
    _show("TEST 2: medium-priority customer" + ("" if mediums else " (no Medium found; used median)"), c, s, res)

    # Test 3: opted-out customer (copy of the top-scoring real record)
    c, a, s = max(rows, key=lambda r: r[2]["recovery_score"])
    opted = {**c, "opted_out": True}
    res = choose_recovery_strategy(opted, a, s)
    assert res["strategy"] == NO_CONTACT and res["recommended_channel"] == "none", "Opt-out violated!"
    _show("TEST 3: opted-out customer (top-scoring record, opted_out=True)", opted, s, res)

    # Test 4: opt-out must hold for ALL customers, regardless of score
    for c, a, s in rows:
        r = choose_recovery_strategy({**c, "opted_out": True}, a, s)
        assert r["strategy"] == NO_CONTACT, f"Opt-out violated for id {c['id']}"
    for value in ("yes", "True", 1, "garbage"):
        r = choose_recovery_strategy({**c, "opted_out": value}, a, s)
        assert r["strategy"] == NO_CONTACT, f"Opt-out value {value!r} not respected"
    print("Opt-out check passed for all customers and value formats.\n")

    # Test 5: invalid / missing data defaults
    assert choose_recovery_strategy(None, {}, {})["strategy"] == NO_CONTACT
    assert choose_recovery_strategy(customers[0], None, None)["strategy"] == NO_CONTACT
    assert choose_recovery_strategy(customers[0], {}, {"recovery_score": "abc"})["strategy"] == NO_CONTACT
    assert choose_recovery_strategy(customers[0], None, {"recovery_score": 70})["strategy"] in STRATEGIES
    print("Invalid-data handling check passed.\n")

    # Distribution across all customers
    counts: dict[str, int] = {}
    for row in rows:
        strategy = choose_recovery_strategy(*row)["strategy"]
        counts[strategy] = counts.get(strategy, 0) + 1
    print("Strategy distribution (all customers, none opted out):")
    for strategy, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>2}  {strategy}")


if __name__ == "__main__":
    main()