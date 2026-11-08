"""
message_generator.py
Message Generator: drafts a personalized recovery message for one customer.
Input : customer, analysis (analyze_lead), score (calculate_recovery_score),
        strategy (choose_recovery_strategy)
Output: message, customer_id, strategy, generation_status, generated_at (+ channel, error)

Drafts only. This module never sends anything.
Modes : "mock" (no credentials, deterministic templates) or "live" (Vultr Serverless Inference).
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

from lead_analysis import analyze_lead, load_customers
from recovery_scoring import calculate_recovery_score
from strategy_agent import (
    CONCERN, ESCALATE, FRIENDLY, INFO, NO_CONTACT, STRATEGIES,
    _is_opted_out, choose_recovery_strategy,
)

# --- Configuration (all via environment variables) ---------------------------
KEY_ENV = "VULTR_SERVERLESS_INFERENCE_API_KEY"
DEFAULT_URL = "https://api.vultrinference.com/v1/chat/completions"
MAX_ATTEMPTS = 3
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_TOKENS = 500
TEMPERATURE = 0.6


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _config() -> dict:
    """Read config at call time so tests and deployments can change env vars."""
    return {
        "api_key": os.environ.get(KEY_ENV, "").strip(),
        "url": os.environ.get("VULTR_INFERENCE_URL", DEFAULT_URL).strip(),
        "model": os.environ.get("VULTR_MODEL", "").strip(),
        "mode": os.environ.get("MESSAGE_GENERATOR_MODE", "mock").strip().lower(),
        "connect_timeout": _env_float("VULTR_CONNECT_TIMEOUT", 5.0),
        "read_timeout": _env_float("VULTR_READ_TIMEOUT", 30.0),
        "sender": os.environ.get("SENDER_NAME", "The Sales Team").strip(),
    }


# --- Guardrails --------------------------------------------------------------
# Rejects anything that looks like an invented price, discount, or promise.
FORBIDDEN = re.compile(
    r"[$€£]|\d+\s*%|\bdiscounts?\b|\bfor free\b|\bfree (?:trial|onboarding|demo|upgrade)\b"
    r"|\bguarantee[sd]?\b|\blimited[- ]time\b",
    re.IGNORECASE,
)


def _violates_guardrails(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in FORBIDDEN.finditer(text)})


# --- Prompt building (live mode) --------------------------------------------
SYSTEM_PROMPT = (
    "You write short, natural sales follow-up messages for a B2B sales team.\n"
    "Hard rules:\n"
    "- Use ONLY the lead facts you are given. Do not invent anything else.\n"
    "- Never mention or imply discounts, prices, payment terms, free offers, or special deals.\n"
    "- Never describe specific product features, integrations, or roadmap items.\n"
    "- Never invent past conversations, meetings, or customer history beyond the facts given.\n"
    "- Never make promises or guarantees, and never create false urgency.\n"
    "- The 'possible reason' hint is an uncertain internal guess: never state it as fact or quote it.\n"
    "- Never mention internal scores, priorities, or that an AI wrote the message.\n"
    "- Sound like a considerate human. No hype, no emojis, no placeholders like [Name].\n"
    "- Output only the message itself, with no commentary."
)

STRATEGY_GUIDANCE = {
    FRIENDLY: "Warm, low-pressure check-in. Ask whether now is a better time to revisit the conversation. No pitch.",
    INFO: "Offer to share more product information relevant to their needs, if they want it. Do not describe any specific feature.",
    CONCERN: "Gently invite them to share any concerns about the earlier discussion and say you would like to help address them. Do not mention prices, discounts, or terms.",
    ESCALATE: "A personal note from a sales representative who wants to reconnect one-to-one. Ask for a short conversation.",
}

REASON_HINT = {
    "price_too_high": "budget or cost may have been a factor",
    "chose_competitor": "they may have been weighing other options",
    "bad_timing": "timing or internal priorities may have been the issue",
    "no_response": "communication went quiet",
    "missing_feature": "the fit with their needs may have been a question",
    "champion_left": "their main contact or sponsor may have changed",
    "unknown": "unknown",
}

CHANNEL_FORMAT = {
    "email": "a short email. First line must be 'Subject: ...', then a blank line, then the body. Max 110 words.",
    "linkedin": "a short LinkedIn message with no subject line. Max 60 words.",
    "phone": "a short phone-call opening a sales rep can say aloud, with no subject line. Max 70 words.",
}


def _first_name(customer: dict) -> str:
    name = str(customer.get("name") or "").strip()
    return name.split()[0] if name else "there"


def _build_user_prompt(customer: dict, analysis: dict, strategy_name: str, channel: str, sender: str) -> str:
    reason = analysis.get("loss_reason", "unknown")
    facts = [
        f"- First name: {_first_name(customer)}",
        f"- Company: {customer.get('company', 'unknown')}",
        f"- Role: {customer.get('role', 'unknown')}",
        f"- Plan they were interested in: {customer.get('plan_interested', 'unknown')}",
        f"- Deal reached this stage before stalling: {customer.get('stage_lost_at', 'unknown')}",
    ]
    if customer.get("days_inactive") is not None:
        facts.append(f"- Days since last contact: {customer['days_inactive']}")

    return (
        f"Write {CHANNEL_FORMAT.get(channel, CHANNEL_FORMAT['email'])}\n\n"
        "Lead facts (the only facts you may use):\n" + "\n".join(facts) + "\n\n"
        f"Approach: {STRATEGY_GUIDANCE[strategy_name]}\n"
        f"Possible reason the deal stalled (uncertain, internal): {REASON_HINT.get(reason, 'unknown')}\n"
        f"Sign off as: {sender}"
    )


# --- Mock mode ---------------------------------------------------------------
MOCK_SUBJECT = {
    FRIENDLY: "Checking in",
    INFO: "More information on {plan}",
    CONCERN: "Anything we can clear up?",
    ESCALATE: "Reconnecting",
}

MOCK_BODY = {
    FRIENDLY: (
        "I hope you're doing well. It's been a little while since we last spoke about the {plan} plan "
        "for {company}, and I wanted to check in. If the timing is better now, I'd be glad to pick the "
        "conversation back up. If not, no problem at all."
    ),
    INFO: (
        "I wanted to follow up on our earlier conversation about the {plan} plan for {company}. "
        "If it would be useful, I'd be happy to share more information. Just tell me what would be "
        "most helpful to see."
    ),
    CONCERN: (
        "When we last spoke about the {plan} plan for {company}, I may not have covered everything on "
        "your mind. If any concerns came up, I'd welcome the chance to talk them through. "
        "Would a short conversation be useful?"
    ),
    ESCALATE: (
        "I'm reaching out personally to reconnect about the {plan} plan discussions with {company}. "
        "Is now a good moment for a quick chat, or would another time suit you better?"
    ),
}


def _mock_message(customer: dict, strategy_name: str, channel: str, sender: str) -> str:
    fill = {"plan": customer.get("plan_interested", "our"), "company": customer.get("company", "your company")}
    first = _first_name(customer)
    body = MOCK_BODY[strategy_name].format(**fill)

    if channel == "phone":
        return f"Hi {first}, this is {sender}. {body}"
    if channel == "linkedin":
        return f"Hi {first}, {body}\n\n{sender}"
    subject = MOCK_SUBJECT[strategy_name].format(**fill)
    return f"Subject: {subject}\n\nHi {first},\n\n{body}\n\nBest regards,\n{sender}"


# --- Vultr call (live mode) --------------------------------------------------
class GenerationError(Exception):
    pass


def _clean(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text


def _extract_text(resp: requests.Response) -> str:
    try:
        text = resp.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise GenerationError("unexpected response format from inference API")
    if not isinstance(text, str) or not text.strip():
        raise GenerationError("inference API returned empty content")
    return _clean(text)


def _call_vultr(cfg: dict, messages: list[dict]) -> str:
    headers = {"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"}
    body = {"model": cfg["model"], "messages": messages, "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS}
    timeout = (cfg["connect_timeout"], cfg["read_timeout"])
    last_error = "unknown error"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(cfg["url"], headers=headers, json=body, timeout=timeout)
        except requests.exceptions.Timeout:
            last_error = f"request timed out (connect {timeout[0]}s / read {timeout[1]}s)"
        except requests.exceptions.RequestException as exc:
            last_error = f"network error: {type(exc).__name__}"
        else:
            if resp.status_code == 200:
                return _extract_text(resp)
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if resp.status_code not in RETRYABLE_STATUS:
                raise GenerationError(last_error)  # 400/401/403/404: retrying won't help
        if attempt < MAX_ATTEMPTS:
            time.sleep(1.5 * attempt)

    raise GenerationError(f"{last_error} (after {MAX_ATTEMPTS} attempts)")


# --- Main function -----------------------------------------------------------
def _result(customer_id, strategy, status, message=None, channel=None, error=None) -> dict:
    return {
        "message": message,
        "customer_id": customer_id,
        "strategy": strategy,
        "generation_status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "channel": channel,
        "error": error,
    }


def generate_recovery_message(customer: dict, analysis: dict, score: dict, strategy: dict, mode: str | None = None) -> dict:
    """
    generation_status values:
      mock | generated | skipped_opted_out | skipped_no_contact |
      failed_invalid_input | failed_config | failed_api | failed_guardrail
    `score` is accepted for interface consistency but deliberately never shown to the LLM.
    """
    cfg = _config()

    if not isinstance(customer, dict) or customer.get("id") is None:
        return _result(None, None, "failed_invalid_input", error="Missing or invalid customer record.")
    cid = customer["id"]

    if isinstance(strategy, str):
        strategy = {"strategy": strategy}
    strategy = strategy if isinstance(strategy, dict) else {}
    strategy_name = strategy.get("strategy")
    channel = strategy.get("recommended_channel") or "email"
    analysis = analysis if isinstance(analysis, dict) else {}

    # Opt-out is checked first, independently of what the strategy says.
    if _is_opted_out(customer, []):
        return _result(cid, strategy_name, "skipped_opted_out", channel=None,
                       error="Customer has opted out. No message generated.")

    if strategy_name not in STRATEGIES:
        return _result(cid, strategy_name, "failed_invalid_input", error=f"Unknown strategy: {strategy_name!r}.")
    if strategy_name == NO_CONTACT:
        return _result(cid, strategy_name, "skipped_no_contact", channel=None,
                       error="Strategy is 'No contact'. No message generated.")

    mode = (mode or cfg["mode"]).lower()
    if mode not in ("mock", "live"):
        return _result(cid, strategy_name, "failed_config", channel=channel,
                       error=f"Invalid mode {mode!r}. Use 'mock' or 'live'.")

    if mode == "mock":
        text = _mock_message(customer, strategy_name, channel, cfg["sender"])
        status = "mock"
    else:
        missing = [n for n, v in ((KEY_ENV, cfg["api_key"]), ("VULTR_MODEL", cfg["model"])) if not v]
        if missing:
            return _result(cid, strategy_name, "failed_config", channel=channel,
                           error=f"Live mode needs environment variable(s): {', '.join(missing)}.")
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(customer, analysis, strategy_name, channel, cfg["sender"])},
        ]
        try:
            text = _call_vultr(cfg, messages)
        except GenerationError as exc:
            return _result(cid, strategy_name, "failed_api", channel=channel, error=str(exc))
        status = "generated"

    violations = _violates_guardrails(text)
    if violations:
        return _result(cid, strategy_name, "failed_guardrail", channel=channel,
                       error=f"Message rejected, contains forbidden content: {violations}")

    return _result(cid, strategy_name, status, message=text, channel=channel)


# --- Test runner -------------------------------------------------------------
def _build_rows(customers: list[dict]) -> list[tuple]:
    rows = []
    for c in customers:
        a = analyze_lead(c)
        s = calculate_recovery_score(c, a)
        rows.append((c, a, s, choose_recovery_strategy(c, a, s)))
    return rows


def _show(title: str, customer: dict, strategy: dict, result: dict) -> None:
    print(f"=== {title} ===")
    print(f"Customer #{customer['id']} {customer['name']} ({customer['company']}) | "
          f"strategy: {strategy['strategy']} | channel: {result['channel']}")
    print(f"status: {result['generation_status']} | generated_at: {result['generated_at']}")
    if result["error"]:
        print(f"note: {result['error']}")
    if result["message"]:
        print("--- message (draft, NOT sent) ---")
        print(result["message"])
    print()


def run_mock_tests(rows: list[tuple]) -> None:
    # 1. One example per contactable strategy
    shown = set()
    for c, a, s, st in rows:
        if st["strategy"] == NO_CONTACT or st["strategy"] in shown:
            continue
        shown.add(st["strategy"])
        res = generate_recovery_message(c, a, s, st, mode="mock")
        assert res["generation_status"] == "mock" and res["message"], "Mock generation failed"
        _show(f"MOCK: {st['strategy']}", c, st, res)

    # 2. Opted-out customer, even though the (stale) strategy says contact
    c, a, s, st = next(r for r in rows if r[3]["strategy"] != NO_CONTACT)
    opted = {**c, "opted_out": True}
    res = generate_recovery_message(opted, a, s, st, mode="mock")
    assert res["message"] is None and res["generation_status"] == "skipped_opted_out", "Opt-out violated!"
    _show("OPTED OUT (strategy said contact, opt-out wins)", opted, st, res)

    # 3. No-contact strategy
    nc = next((r for r in rows if r[3]["strategy"] == NO_CONTACT), None)
    if nc:
        c, a, s, st = nc
        res = generate_recovery_message(c, a, s, st, mode="mock")
        assert res["message"] is None and res["generation_status"] == "skipped_no_contact"
        _show("NO CONTACT strategy", c, st, res)

    # 4. All customers: right status, and no invented prices/discounts/promises
    for c, a, s, st in rows:
        res = generate_recovery_message(c, a, s, st, mode="mock")
        expected = "skipped_no_contact" if st["strategy"] == NO_CONTACT else "mock"
        assert res["generation_status"] == expected, f"Wrong status for id {c['id']}"
        if res["message"]:
            assert not _violates_guardrails(res["message"]), f"Guardrail hit for id {c['id']}"
    print(f"All {len(rows)} customers passed (statuses correct, no forbidden content).\n")

    # 5. Live mode without credentials must fail cleanly, with no network call
    saved = os.environ.pop(KEY_ENV, None)
    try:
        c, a, s, st = next(r for r in rows if r[3]["strategy"] != NO_CONTACT)
        res = generate_recovery_message(c, a, s, st, mode="live")
        assert res["generation_status"] == "failed_config" and res["message"] is None
        print(f"Live mode without key -> {res['generation_status']}: {res['error']}\n")
    finally:
        if saved is not None:
            os.environ[KEY_ENV] = saved

    # 6. Guardrail and invalid-input checks
    assert _violates_guardrails("Get 20% off with this discount") and _violates_guardrails("Only $99")
    assert not _violates_guardrails("Feel free to reply whenever suits you.")
    assert generate_recovery_message(None, {}, {}, {})["generation_status"] == "failed_invalid_input"
    assert generate_recovery_message(rows[0][0], {}, {}, {"strategy": "bogus"})["generation_status"] == "failed_invalid_input"
    print("Guardrail and invalid-input checks passed.")


def main() -> None:
    args = sys.argv[1:]
    live = "--live" in args
    ids = [int(a) for a in args if a.isdigit()]
    rows = _build_rows(load_customers())

    if live or ids:
        mode = "live" if live else "mock"
        if ids:
            row = next((r for r in rows if r[0]["id"] == ids[0]), None)
            if not row:
                sys.exit(f"No customer with id {ids[0]}")
        else:
            row = next((r for r in rows if r[3]["strategy"] != NO_CONTACT), None)
            if not row:
                sys.exit("No customer with a contactable strategy found.")
        c, a, s, st = row
        res = generate_recovery_message(c, a, s, st, mode=mode)
        _show(f"{mode.upper()} generation", c, st, res)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return

    run_mock_tests(rows)


if __name__ == "__main__":
    main()