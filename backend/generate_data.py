"""
generate_data.py
Generates synthetic lost-deal customers for AI Sales Recovery.
Output: data/customers.json
Uses only the Python standard library, so there is nothing to install.
"""

import json
import random
from datetime import date, timedelta
from pathlib import Path

SEED = 42  # same seed = same data every run (reproducible demos)
NUM_CUSTOMERS = 200
OUTPUT_FILE = Path("data/customers.json")

FIRST_NAMES = ["Sarah", "Omar", "Lina", "James", "Yasmine", "Daniel", "Amira", "Carlos",
               "Nadia", "Michael", "Fatima", "Ethan", "Karim", "Sofia", "Adam", "Meriem"]
LAST_NAMES = ["Johnson", "Benali", "Khelifi", "Smith", "Haddad", "Garcia", "Mansouri",
              "Brown", "Cherif", "Lopez", "Bouzid", "Wilson", "Taleb", "Martin", "Amrani"]
COMPANY_PREFIXES = ["Nova", "Apex", "Blue", "Quantum", "Orbit", "Summit", "Atlas", "Pulse",
                    "Vertex", "Horizon", "Cedar", "Ionic"]
COMPANY_SUFFIXES = ["Logistics", "Retail", "Health", "Systems", "Foods", "Labs",
                    "Solutions", "Industries", "Media", "Finance"]
INDUSTRIES = ["Logistics", "Retail", "Healthcare", "Technology", "Food & Beverage",
              "Finance", "Manufacturing", "Media"]
ROLES = ["CEO", "CTO", "Head of Operations", "Procurement Manager", "VP Sales", "Finance Director"]

PLANS = {
    "Starter": (2_000, 8_000),
    "Professional": (8_000, 30_000),
    "Enterprise": (30_000, 120_000),
}
STAGES = ["Qualified", "Demo Completed", "Proposal Sent", "Negotiation"]

# Each loss reason has realistic CRM-style notes the Lead Analysis Agent will have to interpret.
LOSS_REASONS = {
    "price_too_high": [
        "Prospect said the proposal was well above their budget range.",
        "Asked for a 30% discount, we could not match it. Went quiet afterward.",
        "Finance team flagged the annual cost as too expensive for current quarter.",
    ],
    "chose_competitor": [
        "Prospect mentioned they are evaluating a cheaper competitor with similar features.",
        "Told us a competitor offered a free onboarding package.",
        "Signed with another vendor already used by their parent company.",
    ],
    "bad_timing": [
        "Said priorities changed this quarter, asked us to follow up in 3 months.",
        "Company is mid-restructuring, no bandwidth for a new tool right now.",
        "Implementation window conflicts with their annual audit season.",
    ],
    "no_response": [
        "Stopped replying after the demo. Three follow-up emails unanswered.",
        "Went silent after receiving the proposal. No calls returned.",
        "Opened our last emails multiple times but never responded.",
    ],
    "missing_feature": [
        "Needed a native integration with their legacy ERP that we do not offer.",
        "Asked about advanced reporting and multi-language support, both missing.",
        "Required on-premise deployment, we are cloud only.",
    ],
    "champion_left": [
        "Our main contact left the company. No replacement contact identified.",
        "Internal sponsor was reassigned to another department.",
    ],
}


def generate_customer(customer_id: int) -> dict:
    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)
    company = f"{random.choice(COMPANY_PREFIXES)} {random.choice(COMPANY_SUFFIXES)}"
    plan = random.choice(list(PLANS))
    low, high = PLANS[plan]
    reason = random.choice(list(LOSS_REASONS))

    days_inactive = random.randint(7, 180)
    last_contact = date.today() - timedelta(days=days_inactive)

    # "no_response" leads are cold on meetings; others had real engagement.
    meetings = random.randint(0, 1) if reason == "no_response" else random.randint(1, 5)

    return {
        "id": customer_id,
        "name": f"{first} {last}",
        "email": f"{first.lower()}.{last.lower()}@{company.lower().replace(' ', '')}.com",
        "company": company,
        "industry": random.choice(INDUSTRIES),
        "role": random.choice(ROLES),
        "plan_interested": plan,
        "deal_value": round(random.uniform(low, high), -2),
        "stage_lost_at": random.choice(STAGES),
        "last_contact_date": last_contact.isoformat(),
        "days_inactive": days_inactive,
        "emails_sent": random.randint(3, 20),
        "emails_opened": random.randint(0, 15),
        "meetings_held": meetings,
        "crm_notes": random.choice(LOSS_REASONS[reason]),
        "_true_loss_reason": reason,  # ground truth, used later to test our agent's accuracy
    }


def main() -> None:
    random.seed(SEED)
    customers = [generate_customer(i) for i in range(1, NUM_CUSTOMERS + 1)]

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(customers, f, indent=2, ensure_ascii=False)

    total = sum(c["deal_value"] for c in customers)
    print(f"Generated {len(customers)} customers -> {OUTPUT_FILE}")
    print(f"Total lost pipeline value: ${total:,.0f}")
    print("\nSample record:")
    print(json.dumps(customers[0], indent=2))


if __name__ == "__main__":
    main()