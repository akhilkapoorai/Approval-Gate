"""Customer support source-of-truth checks (Lumen Cloud).

Extracts the checkable facts in a draft email (prices, discounts, refunds, trials, response times)
and compares each one with price_sheet.csv and policies.json. Also flags promises an agent cannot
make and hype language (business_rules.md rules 3 and 4).

Everything here is rule-based and general: nothing matches practice-set wording.
Each finding is (level, message) where level is BLOCK, HOLD_FOR_REVIEW, or PASS (verified fact, evidence only).
"""
import re

BLOCK, HOLD, PASS = "BLOCK", "HOLD_FOR_REVIEW", "PASS"
PLAN_RE = re.compile(r"\b(Starter|Pro|Business|Enterprise)\b")  # case-sensitive: avoids "business day", "process"
ALL_PLANS_RE = re.compile(r"\b(every|all|each|any)\b[\w\s-]{0,20}\bplans?\b", re.I)
NEGATION_RE = re.compile(r"\b(not|no|never|cannot|can't|can not|isn't|aren't|don't|doesn't|won't|ineligible|unable)\b", re.I)

# ---------- number normalisation ("nineteen dollars" -> "19 dollars") ----------

UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split())}
TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
NUM_WORD = r"(?:%s)" % "|".join(sorted(list(UNITS) + list(TENS) + ["hundred"], key=len, reverse=True))
NUM_PHRASE_RE = re.compile(r"\b%s(?:[\s-]+(?:and[\s-]+)?%s)*\b" % (NUM_WORD, NUM_WORD), re.I)


def _words_to_int(phrase):
    total, current = 0, 0
    for w in re.split(r"[\s-]+", phrase.lower()):
        if w == "and":
            continue
        if w == "hundred":
            current = max(current, 1) * 100
        elif w in TENS:
            current += TENS[w]
        else:
            current += UNITS[w]
    return total + current


def normalise(text):
    text = NUM_PHRASE_RE.sub(lambda m: str(_words_to_int(m.group(0))), text)
    # only durations ("within a year"), not rates ("$49 a month")
    text = re.sub(r"\b(the )?first (full )?year\b", "365 days", text, flags=re.I)
    text = re.sub(r"\b(within|in|for|after|up to) (a|one) (full )?year\b", r"\1 365 days", text, flags=re.I)
    text = re.sub(r"\b(within|in|for|after|up to) a (single |business )?(day|week|month)\b", r"\1 1 \3", text, flags=re.I)
    return text.replace(",", "")  # "$1,200" -> "$1200"


def clauses(text):
    """Split into sentence-level clauses so each fact is matched with the plan it talks about."""
    parts = re.split(r"(?<=[.!?;])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


# ---------- extraction helpers ----------

MONEY_RE = re.compile(r"(?:\$|USD\s?)(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s?(?:dollars|usd|bucks)\b", re.I)
PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s?(?:%|percent\b|per cent\b)", re.I)
DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)[\s-]?(hours?|hrs?|h\b|business days?|days?|weeks?|months?)", re.I)
UNLIMITED_RE = re.compile(r"\b(any ?time|at any point|forever|lifetime|no matter when|no time limit|whenever)\b", re.I)


def to_days(n, unit):
    unit = unit.lower()
    if unit.startswith("week"):
        return n * 7
    if unit.startswith("month"):
        return n * 30
    return n


def to_hours(n, unit):
    unit = unit.lower()
    if unit.startswith(("hour", "hr", "h")):
        return n
    if "day" in unit:
        return n * 24
    if unit.startswith("week"):
        return n * 24 * 7
    return None


def plan_near(clause, pos):
    """The plan mentioned closest before `pos`, else the first one after it."""
    before = [m for m in PLAN_RE.finditer(clause) if m.start() < pos]
    if before:
        return before[-1].group(1)
    after = [m for m in PLAN_RE.finditer(clause) if m.start() >= pos]
    return after[0].group(1) if after else None


def negated_before(low, keyword):
    """True if a negation word appears just before `keyword` ("no free trial", "isn't eligible for a trial")."""
    i = low.find(keyword)
    return i >= 0 and bool(NEGATION_RE.search(low[max(0, i - 30): i]))


def fmt(n):
    return f"{n:g}"


# ---------- the checks ----------

def check(text, context, sot):
    prices = {row["plan"]: row for row in sot["price_sheet"]}
    pol = sot["policies"]
    ctx_plan = context.get("plan")
    tenure = context.get("customer_tenure_months")
    findings = []

    def add(level, msg):
        findings.append((level, msg))

    norm = normalise(text)
    for clause in clauses(norm):
        low = clause.lower()
        negated = bool(NEGATION_RE.search(clause))
        discount_clause = bool(re.search(r"\b(discount|off|save|saving|savings|loyalty)\b", low))

        # 1. Prices (rule 1)
        if not re.search(r"\brefund", low):
            for m in MONEY_RE.finditer(clause):
                value = float(m.group(1) or m.group(2))
                plan = plan_near(clause, m.start()) or (ctx_plan if re.search(r"\byour\b", low) else None)
                if plan == "Enterprise":
                    add(BLOCK, f"Enterprise pricing is custom (contact {pol['enterprise']['contact']}); the email quotes ${fmt(value)}.")
                    continue
                if plan not in prices:
                    add(HOLD, f"The email quotes ${fmt(value)} but does not say which plan it is for.")
                    continue
                row = prices[plan]
                monthly, annual = float(row["monthly_price_usd"]), float(row["annual_price_per_month_usd"])
                allowed = {"monthly price": monthly, "annual-billing price per month": annual,
                           "monthly price for a year": monthly * 12, "annual-billing price per year": annual * 12}
                loyal = pol["discounts"]["loyalty"]
                if plan in loyal["applies_to"]:
                    allowed["price with loyalty discount"] = round(monthly * (1 - loyal["percent"] / 100), 2)
                match = next((k for k, v in allowed.items() if abs(v - value) < 0.005), None)
                if match:
                    add(PASS, f"{plan} at ${fmt(value)} matches the {match} on the price sheet.")
                else:
                    add(BLOCK, f"{plan} is ${fmt(monthly)}/month (${fmt(annual)}/month billed annually); the email says ${fmt(value)}.")

        # 2. Discounts (rule 2)
        if discount_clause:
            stacking = re.search(r"\b(combine[ds]?|combining|stack(ed|ing)?|on top of|together with|both discounts|in addition to)\b", low)
            mentions_loyalty, mentions_annual = "loyalty" in low, bool(re.search(r"\b(annual|yearly|annually)\b", low))
            if stacking and not negated and (mentions_loyalty or mentions_annual):
                add(BLOCK, "Discounts cannot be stacked; the email says they can be combined.")
            elif stacking and negated:
                add(PASS, "Correctly states that discounts cannot be stacked.")
            else:
                for m in PCT_RE.finditer(clause):
                    pct = float(m.group(1))
                    window = low[max(0, m.start() - 60): m.end() + 60]
                    kind = "loyalty" if "loyalty" in window else "annual_billing" if re.search(r"\b(annual|yearly|annually)\b", window) else None
                    if kind is None:
                        if not negated:
                            add(HOLD, f"{fmt(pct)}% discount is not a discount in company policy; agents cannot offer discounts beyond policy.")
                        continue
                    expected = pol["discounts"][kind]["percent"]
                    name = kind.replace("_", " ")
                    if pct != expected:
                        add(BLOCK, f"The {name} discount is {expected}%; the email says {fmt(pct)}%.")
                    else:
                        add(PASS, f"{name.capitalize()} discount of {fmt(pct)}% matches policy.")

            # plans named alongside a discount must be eligible
            for kind, word in (("loyalty", "loyalty"), ("annual_billing", "annual")):
                if word not in low or negated:
                    continue
                for p in set(PLAN_RE.findall(clause)):
                    if p not in pol["discounts"][kind]["applies_to"]:
                        add(BLOCK, f"The {kind.replace('_', ' ')} discount does not apply to the {p} plan.")

            # the customer must be eligible for a loyalty discount being offered
            if mentions_loyalty and not negated_before(low, "loyalty"):
                loyal = pol["discounts"]["loyalty"]
                if ctx_plan is None or tenure is None:
                    add(HOLD, "Loyalty discount offered but the customer's plan or tenure is unknown; eligibility cannot be confirmed.")
                elif ctx_plan not in loyal["applies_to"]:
                    add(BLOCK, f"Customer is on {ctx_plan}; the loyalty discount only applies to {', '.join(loyal['applies_to'])}.")
                elif tenure < loyal["min_tenure_months"]:
                    add(BLOCK, f"Customer tenure is {tenure} months; the loyalty discount needs {loyal['min_tenure_months']}.")
                else:
                    add(PASS, f"Customer is eligible for the loyalty discount ({ctx_plan}, {tenure} months).")

        # 3. Refunds (rule 1)
        if re.search(r"\brefund", low):
            ref = pol["refunds"]
            kind = "annual" if re.search(r"\b(annual|yearly)\b", low) else "monthly" if re.search(r"\bmonthly\b", low) else None
            limits = {"annual": ref["annual_plans_days"], "monthly": ref["monthly_plans_days"]}
            stated = [to_days(float(n), u) for n, u in DURATION_RE.findall(clause) if not u.lower().startswith(("h", "business"))]
            if UNLIMITED_RE.search(clause) and not negated:
                add(BLOCK, f"Refunds are limited to {ref['monthly_plans_days']} days (monthly) or {ref['annual_plans_days']} days (annual); the email promises no time limit.")
            for days in stated:
                ok = days == limits[kind] if kind else days in limits.values()
                label = f"{kind} plans" if kind else "either plan type"
                if ok:
                    add(PASS, f"Refund window of {fmt(days)} days matches policy for {label}.")
                elif not negated:
                    want = f"{limits[kind]} days" if kind else f"{ref['monthly_plans_days']} (monthly) or {ref['annual_plans_days']} (annual) days"
                    add(BLOCK, f"Refund window for {label} is {want}; the email says {fmt(days)} days.")
            if not stated and not UNLIMITED_RE.search(clause) and not negated and re.search(r"\b(i|we)('ll| will|'ve| have)\b", low):
                add(HOLD, "The agent promises a refund outside the stated policy window; a manager should approve the exception.")

        # 4. Free trials (rule 1)
        if "trial" in low and not negated_before(low, "trial"):
            trial = pol["free_trial"]
            for n, u in DURATION_RE.findall(clause):
                days = to_days(float(n), u)
                if days == trial["days"]:
                    add(PASS, f"Free trial of {fmt(days)} days matches policy.")
                else:
                    add(BLOCK, f"The free trial is {trial['days']} days; the email says {fmt(days)} days.")
            for p in set(PLAN_RE.findall(clause)):
                if p not in trial["plans"]:
                    add(BLOCK, f"The {p} plan has no free trial (trials: {', '.join(trial['plans'])}).")
            card_free = re.search(r"\b(no|without|not|n't)\b[\w\s]{0,20}\b(card|payment)", low)
            if not trial["card_required"] and not card_free and re.search(r"\b(card|payment details?)\b[\w\s]{0,15}\b(required|needed)|\b(require|requires|need|needs)\b[\w\s]{0,15}\b(card|payment)", low):
                add(BLOCK, "The free trial does not require a card; the email says it does.")

        # 5. First-response times (rule 1)
        if re.search(r"\b(respon(se|d|ds)|repl(y|ies)|get back|hear back|hear from|answer)\b", low):
            hours = [to_hours(float(n), u) for n, u in DURATION_RE.findall(clause)]
            hours = [h for h in hours if h is not None]
            if hours:
                plans = sorted(set(PLAN_RE.findall(clause)) - {"Enterprise"})
                if not plans and ALL_PLANS_RE.search(clause):
                    plans = list(prices)
                if not plans and ctx_plan in prices:
                    plans = [ctx_plan]
                if not plans:
                    add(HOLD, f"The email promises a {fmt(hours[0])}-hour response without naming a plan.")
                for p in plans:
                    want = float(prices[p]["first_response_hours"])
                    for h in hours:
                        if h == want:
                            add(PASS, f"{p} first response within {fmt(h)} hours matches the price sheet.")
                        else:
                            add(BLOCK, f"{p} first response is {fmt(want)} hours; the email says {fmt(h)} hours.")

    # 6. Promises an agent can't make (rule 3) -- whole text
    promises = [
        r"\b(i|we)('ll| will| are going to|'re going to| can)\b[^.!?]{0,40}\b(ship|release|launch|build|add|deliver|implement|roll out|develop)\b",
        r"\b(i|we)('ll| will)\s+make sure\b",
        r"\bpromise\b",
        r"\b(make an exception|exception for you|waive|on the house|free upgrade|upgrade you for free|bump you up)\b",
        r"\b(feature|integration|roadmap|release|version)\b[^.!?]{0,60}\b(next (week|month|quarter|year)|by (monday|tuesday|wednesday|thursday|friday|the end of|end of)|in \d+ (days|weeks|months)|this (month|quarter))\b",
    ]
    for pat in promises:
        m = re.search(pat, norm, re.I)
        if m:
            add(HOLD, f"Agents cannot promise features, dates, or exceptions (\"{m.group(0).strip()}\"); manager review needed.")
            break

    # 7. Hype and guarantees (rule 4) -- backup to Ezio's tone score
    hype = []
    if re.search(r"\bguarantee(d|s)?\b", text, re.I):
        hype.append("a guarantee")
    if re.search(r"!{2,}", text):
        hype.append("repeated exclamation marks")
    shouting = [w for w in re.findall(r"\b[A-Z]{3,}\b", text) if w not in {"GDPR", "CCPA", "USD", "SLA", "API", "FAQ", "HIPAA", "SOC", "SSO", "EU", "USA", "CSV", "PDF"}]
    if len(shouting) >= 1:
        hype.append(f"all-caps hype ({', '.join(shouting[:3])})")
    if hype:
        add(HOLD, f"Over-the-top or promotional language: {', '.join(hype)}.")

    # de-duplicate while keeping order
    seen, out = set(), []
    for f in findings:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out
