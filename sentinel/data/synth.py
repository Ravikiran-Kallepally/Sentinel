"""Synthetic "source systems" for Sentinel.

Real fraud data is private, so we simulate a small bank. The simulation is deliberately
*imperfect* in the same ways real systems are, because handling imperfection is most of the
job in data engineering:

  * Transaction events arrive on a stream with ~0.5% duplicates (producer retries) and ~1% late
    events (mobile terminals that were offline).
  * Fraud labels are NOT on the transaction. They arrive later from a separate chargeback feed,
    exactly like real life: you only learn a transaction was fraud when the customer disputes it.
  * Customer records change over time (address, risk tier), which is why we need CDC and SCD2.
  * Complaint narratives are free text with typos, PII and a category label assigned by an agent.

Outputs (under settings.sources_dir):
    oltp.sqlite                 customers, cards, merchants   (the "core banking" database)
    events/transactions.jsonl   the transaction event log     (what Kafka would carry)
    labels/chargebacks.csv      fraud labels with report dates
    complaints.csv              complaint narratives + category
    disputes/<txn_id>.txt       customer dispute letters for a sample of fraud transactions
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from sentinel.config import get_settings

START = datetime(2026, 5, 1)

CATEGORIES = {
    # category: (typical amount mean, std, risk weight for fraudsters)
    "grocery": (55, 30, 0.2),
    "restaurant": (38, 22, 0.3),
    "gas": (45, 15, 0.4),
    "retail": (80, 60, 0.6),
    "pharmacy": (30, 20, 0.1),
    "utilities": (120, 40, 0.05),
    "entertainment": (40, 25, 0.5),
    "travel": (420, 300, 1.0),
    "online_retail": (95, 80, 2.0),
    "electronics": (380, 260, 3.0),
    "gift_cards": (150, 90, 4.0),
    "crypto_exchange": (600, 400, 4.0),
}

FIRST = ["Ava", "Liam", "Mia", "Noah", "Zoe", "Ethan", "Ivy", "Lucas", "Nora", "Omar", "Priya", "Ravi",
         "Sofia", "Mateo", "Hana", "Kenji", "Amara", "Diego", "Leah", "Samir", "Grace", "Wei", "Elena", "Kofi"]
LAST = ["Patel", "Garcia", "Kim", "Nguyen", "Smith", "Johnson", "Okafor", "Chen", "Rossi", "Khan",
        "Silva", "Novak", "Haddad", "Reyes", "Tanaka", "Brown", "Mensah", "Ivanova", "Lopez", "Shah"]
MERCHANT_WORDS = ["Blue", "Metro", "Prime", "Sun", "River", "Peak", "Urban", "North", "Golden", "Swift",
                  "Cedar", "Harbor", "Nova", "Summit", "Lake", "Bright", "Iron", "Maple"]
MERCHANT_SUFFIX = {
    "grocery": "Market", "restaurant": "Grill", "gas": "Fuel", "retail": "Outfitters", "pharmacy": "Pharmacy",
    "utilities": "Power & Water", "entertainment": "Cinemas", "travel": "Airways", "online_retail": "Shop Online",
    "electronics": "Electronics", "gift_cards": "Gift Card Hub", "crypto_exchange": "Crypto Exchange",
}

COMPLAINT_CATEGORIES = ["unauthorized_transaction", "billing_error", "card_lost_stolen", "fees",
                        "account_access", "customer_service"]


def _luhn_complete(prefix: str) -> str:
    """Append a Luhn check digit so fake card numbers look valid to a PII detector."""
    digits = [int(d) for d in prefix]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return prefix + str((10 - total % 10) % 10)


def _haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(a))


# ---------------------------------------------------------------------------------------------
# Reference data: customers, cards, merchants
# ---------------------------------------------------------------------------------------------
def make_customers(rng: np.random.Generator, n: int) -> pd.DataFrame:
    ids = [f"C{i:05d}" for i in range(n)]
    first = rng.choice(FIRST, n)
    last = rng.choice(LAST, n)
    # Home locations clustered around a handful of US metro areas.
    metros = np.array([[40.71, -74.00], [41.88, -87.63], [34.05, -118.24], [29.76, -95.37], [33.75, -84.39],
                       [47.61, -122.33], [39.74, -104.99], [25.76, -80.19]])
    m = metros[rng.integers(0, len(metros), n)]
    lat = m[:, 0] + rng.normal(0, 0.15, n)
    lon = m[:, 1] + rng.normal(0, 0.15, n)
    spend_mu = rng.lognormal(mean=0.0, sigma=0.35, size=n)  # per-customer spend multiplier
    segment = rng.choice(["mass", "affluent", "student", "small_business"], n, p=[0.6, 0.15, 0.15, 0.1])
    created = [START - timedelta(days=int(d)) for d in rng.integers(30, 3000, n)]
    return pd.DataFrame({
        "customer_id": ids,
        "full_name": [f"{a} {b}" for a, b in zip(first, last)],
        "email": [f"{a.lower()}.{b.lower()}{i}@example.com" for i, (a, b) in enumerate(zip(first, last))],
        "phone": [f"+1-555-{rng.integers(100, 999)}-{rng.integers(1000, 9999)}" for _ in range(n)],
        "home_lat": lat.round(4),
        "home_lon": lon.round(4),
        "segment": segment,
        "spend_multiplier": spend_mu.round(3),
        "risk_tier": rng.choice(["low", "medium", "high"], n, p=[0.75, 0.2, 0.05]),
        "created_at": [d.isoformat() for d in created],
        "updated_at": [START.isoformat()] * n,
    })


def make_cards(rng: np.random.Generator, customers: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for cid in customers.customer_id:
        pan = _luhn_complete("4" + "".join(str(d) for d in rng.integers(0, 10, 14)))
        rows.append({"card_id": f"K{cid[1:]}", "customer_id": cid, "pan_last4": pan[-4:], "status": "active",
                     "updated_at": START.isoformat()})
    return pd.DataFrame(rows)


def make_merchants(rng: np.random.Generator, customers: pd.DataFrame, n: int = 420) -> pd.DataFrame:
    cats = list(CATEGORIES)
    weights = np.array([10, 10, 6, 8, 4, 3, 4, 2, 5, 2, 1, 1], dtype=float)
    cat = rng.choice(cats, n, p=weights / weights.sum())
    anchor = customers.sample(n, replace=True, random_state=int(rng.integers(1e9)))
    online = np.isin(cat, ["online_retail", "gift_cards", "crypto_exchange", "utilities"])
    names = [f"{rng.choice(MERCHANT_WORDS)} {MERCHANT_SUFFIX[c]}" for c in cat]
    # Make names unique by suffixing a number where needed
    seen: dict[str, int] = {}
    uniq = []
    for nm in names:
        seen[nm] = seen.get(nm, 0) + 1
        uniq.append(nm if seen[nm] == 1 else f"{nm} #{seen[nm]}")
    return pd.DataFrame({
        "merchant_id": [f"M{i:04d}" for i in range(n)],
        "merchant_name": uniq,
        "category": cat,
        "lat": np.where(online, np.nan, anchor.home_lat.values + rng.normal(0, 0.05, n)).round(4),
        "lon": np.where(online, np.nan, anchor.home_lon.values + rng.normal(0, 0.05, n)).round(4),
        "is_online": online,
        "country": np.where(rng.random(n) < 0.05, rng.choice(["GB", "NG", "RO", "BR", "VN"], n), "US"),
    })


# ---------------------------------------------------------------------------------------------
# Transactions with injected fraud
# ---------------------------------------------------------------------------------------------
def make_transactions(rng: np.random.Generator, customers: pd.DataFrame, cards: pd.DataFrame,
                      merchants: pd.DataFrame, n_days: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    m_by_cat = {c: g for c, g in merchants.groupby("category")}
    local_m = merchants[~merchants.is_online]
    card_of = dict(zip(cards.customer_id, cards.card_id))
    rows = []

    # 1) Normal behaviour: each customer has a handful of favourite merchants near home.
    for cust in customers.itertuples(index=False):
        d = (local_m.lat - cust.home_lat) ** 2 + (local_m.lon - cust.home_lon) ** 2
        near = local_m.loc[d.nsmallest(25).index]
        favs = pd.concat([near.sample(8, random_state=int(rng.integers(1e9))),
                          merchants[merchants.is_online].sample(3, random_state=int(rng.integers(1e9)))])
        lam = rng.uniform(0.25, 0.9)
        device = f"D{rng.integers(1e8):08d}"
        # Legit "noise" that looks like fraud: a phone upgrade (new device) and a trip to another city.
        new_phone_day = int(rng.integers(10, n_days)) if rng.random() < 0.3 else None
        trip_start = int(rng.integers(5, n_days - 8)) if rng.random() < 0.18 else None
        trip_len = int(rng.integers(3, 7))
        if trip_start is not None:
            dest = customers.iloc[rng.integers(0, len(customers))]
            dd = (local_m.lat - dest.home_lat) ** 2 + (local_m.lon - dest.home_lon) ** 2
            trip_favs = local_m.loc[dd.nsmallest(15).index]
        counts = rng.poisson(lam, n_days)
        for day, k in enumerate(counts):
            if new_phone_day is not None and day == new_phone_day:
                device = f"D{rng.integers(1e8):08d}"
            on_trip = trip_start is not None and trip_start <= day < trip_start + trip_len
            for _ in range(k + (1 if on_trip else 0)):
                pool = trip_favs if on_trip else favs
                # 12% of the time people try somewhere new.
                pool = merchants if rng.random() < 0.12 else pool
                mer = pool.iloc[rng.integers(0, len(pool))]
                mean, std, _ = CATEGORIES[mer.category]
                amt = max(1.0, rng.normal(mean, std) * cust.spend_multiplier)
                hour = int(np.clip(rng.normal(14, 5), 0, 23))
                ts = START + timedelta(days=day, hours=hour, minutes=int(rng.integers(60)), seconds=int(rng.integers(60)))
                # Physical stores also sell online, so e-commerce is not limited to online-only merchants.
                ecom = mer.is_online or rng.random() < 0.15
                rows.append((cust.customer_id, card_of[cust.customer_id], mer.merchant_id, round(amt, 2), ts,
                             "ecom" if ecom else "pos", device, 0))
        # Occasional legitimate travel: high amounts far from home (this is the "noise" that makes
        # fraud hard - a good model must not flag every big airline ticket).
        if rng.random() < 0.08:
            day = int(rng.integers(5, n_days - 5))
            mer = m_by_cat["travel"].iloc[rng.integers(0, len(m_by_cat["travel"]))]
            ts = START + timedelta(days=day, hours=int(rng.integers(8, 20)))
            rows.append((cust.customer_id, card_of[cust.customer_id], mer.merchant_id,
                         round(float(rng.uniform(300, 1500)), 2), ts, "ecom", device, 0))

    # 2) Fraud: a card is compromised and used in a short burst at risky merchants, on a new device,
    #    often at night, sometimes from abroad.
    risky = merchants[merchants.category.isin(["electronics", "gift_cards", "crypto_exchange", "online_retail", "retail"])]
    risk_w = risky.category.map(lambda c: CATEGORIES[c][2]).values
    risk_w = risk_w / risk_w.sum()
    victims = customers.sample(frac=0.09, random_state=int(rng.integers(1e9)))
    mixed = merchants[merchants.category.isin(["retail", "online_retail", "restaurant", "entertainment", "gas"])]
    for cust in victims.itertuples(index=False):
        day = int(rng.integers(3, n_days - 2))
        # 35% of victims suffer "low and slow" fraud: few, ordinary-looking purchases in daytime. These are
        # the hard cases that keep real fraud models far from perfect.
        low_and_slow = rng.random() < 0.35
        start_hour = int(rng.integers(9, 19)) if low_and_slow else int(rng.choice([1, 2, 3, 4, 22, 23, 12, 15]))
        t = START + timedelta(days=day, hours=start_hour, minutes=int(rng.integers(60)))
        device = f"X{rng.integers(1e8):08d}"
        if low_and_slow and rng.random() < 0.5:
            # Stolen phone / malware / family member: the fraud comes from the victim's own device.
            device = next((r[6] for r in reversed(rows) if r[0] == cust.customer_id and r[7] == 0), device)
        n_fraud = int(rng.integers(1, 3)) if low_and_slow else int(rng.integers(2, 9))
        for _ in range(n_fraud):
            if low_and_slow:
                mer = mixed.iloc[rng.integers(0, len(mixed))]
                mean, std, _ = CATEGORIES[mer.category]
                amt = max(5.0, rng.normal(mean, std) * cust.spend_multiplier)
                rows.append((cust.customer_id, card_of[cust.customer_id], mer.merchant_id, round(amt, 2), t,
                             "ecom", device, 1))
                t += timedelta(hours=int(rng.integers(3, 30)))
                continue
            mer = risky.iloc[rng.choice(len(risky), p=risk_w)]
            mean, std, _ = CATEGORIES[mer.category]
            amt = max(5.0, rng.normal(mean * 1.4, std))
            rows.append((cust.customer_id, card_of[cust.customer_id], mer.merchant_id, round(amt, 2), t,
                         "ecom" if (mer.is_online or rng.random() < 0.6) else "pos", device, 1))
            t += timedelta(minutes=int(rng.integers(2, 70)))
        # A slice of fraudsters "test" the card with a tiny purchase first.
        if rng.random() < 0.4:
            mer = risky.iloc[rng.integers(0, len(risky))]
            rows.append((cust.customer_id, card_of[cust.customer_id], mer.merchant_id,
                         round(float(rng.uniform(0.5, 3.0)), 2), t - timedelta(hours=3), "ecom", device, 1))

    tx = pd.DataFrame(rows, columns=["customer_id", "card_id", "merchant_id", "amount", "event_time", "channel",
                                     "device_id", "is_fraud"])
    tx = tx.sort_values("event_time").reset_index(drop=True)
    tx.insert(0, "txn_id", [f"T{i:07d}" for i in range(len(tx))])

    # Location of the transaction: POS = merchant location; e-commerce = IP geolocation.
    mer = merchants.set_index("merchant_id")
    cust = customers.set_index("customer_id")
    lat = mer.loc[tx.merchant_id, "lat"].values.astype(float)
    lon = mer.loc[tx.merchant_id, "lon"].values.astype(float)
    home_lat = cust.loc[tx.customer_id, "home_lat"].values
    home_lon = cust.loc[tx.customer_id, "home_lon"].values
    online = np.isnan(lat)
    # Legit e-commerce geolocates near home. Fraud geolocates anywhere - except that half of fraudsters use a
    # residential proxy near the victim, which defeats a naive "distance from home" rule.
    far = (tx.is_fraud.values == 1) & (rng.random(len(tx)) < 0.5)
    lat = np.where(online & ~far, home_lat + rng.normal(0, 0.1, len(tx)), lat)
    lon = np.where(online & ~far, home_lon + rng.normal(0, 0.1, len(tx)), lon)
    lat = np.where(np.isnan(lat), rng.uniform(25, 48, len(tx)), lat)
    lon = np.where(np.isnan(lon), rng.uniform(-123, -70, len(tx)), lon)
    tx["lat"] = lat.round(4)
    tx["lon"] = lon.round(4)
    tx["currency"] = "USD"

    # Labels are noisy, as in real life: ~8% of fraud is never reported by the customer, and a few legitimate
    # purchases are falsely disputed ("friendly fraud"). No model can be better than its labels.
    reported = (tx.is_fraud == 1) & (rng.random(len(tx)) > 0.08)
    friendly = (tx.is_fraud == 0) & (rng.random(len(tx)) < 0.0004)
    labels = tx.loc[reported | friendly, ["txn_id", "event_time"]].copy()
    # Chargebacks are reported 1-20 days after the transaction.
    labels["reported_at"] = labels.event_time + pd.to_timedelta(rng.integers(1, 21, len(labels)), unit="D")
    labels = labels[["txn_id", "reported_at"]]
    return tx, labels


def write_event_log(rng: np.random.Generator, tx: pd.DataFrame, path: Path) -> dict:
    """Write the transaction stream as JSON lines, adding duplicates and late arrivals.

    `ingest_time` is when the event reached the platform; `event_time` is when it happened.
    The gap between the two is what watermarks are about.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    events = tx.drop(columns=["is_fraud"]).copy()  # labels are NOT on the stream
    lag = pd.to_timedelta(rng.exponential(3, len(events)), unit="s")
    late = rng.random(len(events)) < 0.01
    lag = lag.where(~late, pd.to_timedelta(rng.uniform(30, 240, len(events)), unit="m"))
    events["ingest_time"] = events.event_time + lag
    dups = events.sample(frac=0.005, random_state=int(rng.integers(1e9))).copy()
    dups["ingest_time"] = dups.ingest_time + pd.Timedelta(seconds=5)  # producer retry
    events = pd.concat([events, dups]).sort_values("ingest_time").reset_index(drop=True)
    with path.open("w", encoding="utf-8") as f:
        for r in events.itertuples(index=False):
            rec = r._asdict()
            rec["event_time"] = rec["event_time"].isoformat()
            rec["ingest_time"] = rec["ingest_time"].isoformat()
            f.write(json.dumps(rec) + "\n")
    return {"events": len(events), "duplicates": len(dups), "late": int(late.sum())}


# ---------------------------------------------------------------------------------------------
# Complaints (unstructured text) with a realistic daily volume pattern
# ---------------------------------------------------------------------------------------------
TEMPLATES = {
    "unauthorized_transaction": [
        "I see a charge of ${amt} at {merchant} on {date} that I never made. Please reverse it.",
        "There are {n} transactions on my card ending {last4} I did not authorize, including ${amt} at {merchant}.",
        "Someone used my card at {merchant} for ${amt}. I was at home asleep. This is fraud!!",
        "unknown purchase ${amt} {merchant} {date} not me, card {last4}. need refund asap",
    ],
    "billing_error": [
        "I was charged twice for the same purchase of ${amt} at {merchant} on {date}.",
        "My statement shows ${amt} but the receipt from {merchant} says a lower amount. Please correct the billing error.",
        "The merchant {merchant} refunded me but the credit of ${amt} never posted to my account.",
    ],
    "card_lost_stolen": [
        "My wallet was stolen on {date} and my card ending {last4} is missing. Please block it.",
        "I lost my card yesterday. I want to report it before anyone uses it. Card ends in {last4}.",
        "Card stolen from my car on {date}. Please send a replacement and stop all charges.",
    ],
    "fees": [
        "I was charged a ${fee} late fee even though I paid on time on {date}.",
        "Why is there a ${fee} foreign transaction fee? I never left the country.",
        "An overdraft fee of ${fee} was applied unfairly. I want it waived.",
    ],
    "account_access": [
        "I cannot log in to online banking since {date}. The password reset link never arrives.",
        "My account is locked and nobody can tell me why. I need access to pay my bills.",
        "Two-factor codes are not being sent to my phone {phone}. I am locked out.",
    ],
    "customer_service": [
        "I waited on hold for {n} hours and the agent hung up on me. Terrible service.",
        "The branch staff were rude and refused to help me with my dispute.",
        "Nobody responded to my emails at {email} for two weeks. Very disappointed.",
        "I called about a charge at {merchant} and was transferred {n} times. Nobody could help.",
    ],
}
# Ambiguous narratives that genuinely mix topics. Real complaint data is full of these, and they are why
# a classifier should never reach 100% (if yours does, suspect leakage or too-clean data).
AMBIGUOUS = [
    ("billing_error", "I see two charges of ${amt} at {merchant} and I only recognise one of them."),
    ("unauthorized_transaction", "A charge from {merchant} for ${amt} appeared and the fee was also wrong. I never shopped there."),
    ("card_lost_stolen", "My card ending {last4} is missing and I also see a charge I don't recognise at {merchant}."),
    ("customer_service", "My card was blocked for fraud and the agent was rude when I called to unlock it."),
    ("account_access", "I can't log in to check whether the ${amt} charge at {merchant} was refunded."),
    ("fees", "After the dispute about {merchant} I was charged a ${fee} fee for the replacement card."),
]
NEG = ["Very frustrated.", "This is unacceptable.", "I am considering closing my account.", "Please help.", "",
       "Thank you for looking into this.", "I expect a response within 10 days."]


def make_complaints(rng: np.random.Generator, customers: pd.DataFrame, merchants: pd.DataFrame,
                    cards: pd.DataFrame, n_days: int) -> pd.DataFrame:
    days = np.arange(n_days)
    weekly = np.array([1.25, 1.1, 1.0, 1.0, 0.95, 0.6, 0.5])  # Mon..Sun
    base = 14 + 0.06 * days  # slow upward trend
    spike = np.where((days > 70) & (days < 78), 9, 0)  # a merchant-breach week
    dows = [(START + timedelta(days=int(d))).weekday() for d in days]
    lam = base * weekly[dows] + spike
    counts = rng.poisson(lam)
    last4 = dict(zip(cards.customer_id, cards.pan_last4))
    rows = []
    cat_p = np.array([0.3, 0.2, 0.12, 0.16, 0.12, 0.1])
    for d, k in zip(days, counts):
        for _ in range(k):
            c = customers.iloc[rng.integers(0, len(customers))]
            p = cat_p.copy()
            if spike[d]:
                p[0] += 0.3
            cat = rng.choice(COMPLAINT_CATEGORIES, p=p / p.sum())
            t = rng.choice(TEMPLATES[cat])
            if rng.random() < 0.15:
                cat, t = AMBIGUOUS[rng.integers(0, len(AMBIGUOUS))]
            # ~7% label noise: intake agents pick the wrong category sometimes.
            if rng.random() < 0.07:
                cat = rng.choice(COMPLAINT_CATEGORIES)
            when = START + timedelta(days=int(d), hours=int(rng.integers(7, 22)))
            text = t.format(
                amt=f"{rng.uniform(5, 900):.2f}", merchant=merchants.merchant_name.iloc[rng.integers(0, len(merchants))],
                date=(when - timedelta(days=int(rng.integers(1, 10)))).strftime("%B %d"), n=int(rng.integers(2, 6)),
                last4=last4[c.customer_id], fee=rng.choice([15, 25, 35, 39]), phone=c.phone, email=c.email,
            )
            # Some customers paste their full card number - this is what PII redaction is for.
            if rng.random() < 0.05:
                text += f" My card number is {_luhn_complete('4' + ''.join(str(x) for x in rng.integers(0, 10, 14)))}."
            text = (text + " " + rng.choice(NEG)).strip()
            rows.append({"complaint_id": f"Q{len(rows):06d}", "customer_id": c.customer_id,
                         "received_at": when.isoformat(), "product": "credit_card", "category": cat, "narrative": text})
    return pd.DataFrame(rows)


def write_dispute_letters(rng: np.random.Generator, tx: pd.DataFrame, customers: pd.DataFrame,
                          merchants: pd.DataFrame, cards: pd.DataFrame, out: Path, n: int = 200) -> int:
    """Customer dispute letters: the unstructured input to the `extract_dispute_facts` tool."""
    out.mkdir(parents=True, exist_ok=True)
    fraud = tx[tx.is_fraud == 1].sample(min(n, int((tx.is_fraud == 1).sum())), random_state=int(rng.integers(1e9)))
    cust = customers.set_index("customer_id")
    mer = merchants.set_index("merchant_id")
    last4 = dict(zip(cards.customer_id, cards.pan_last4))
    for r in fraud.itertuples(index=False):
        c = cust.loc[r.customer_id]
        reported = (r.event_time + timedelta(days=int(rng.integers(1, 70)))).strftime("%B %d, %Y")
        letter = (
            f"To: Sentinel Bank Disputes\nFrom: {c.full_name} ({c.email})\nDate: {reported}\n\n"
            f"I am writing to dispute a charge of ${r.amount:.2f} at {mer.loc[r.merchant_id, 'merchant_name']} "
            f"on {r.event_time.strftime('%B %d, %Y')} on my card ending in {last4[r.customer_id]}. "
            f"I did not make or authorize this transaction. "
            + rng.choice(["My card was in my possession the whole time. ",
                          "I noticed my card was missing the day before. ",
                          "I received a suspicious text message asking for a code shortly before. "])
            + rng.choice(["I have not shared my PIN with anyone. ", "", "I already called and froze the card. "])
            + "Please investigate and credit my account.\n\nSincerely,\n" + c.full_name + "\n"
        )
        (out / f"{r.txn_id}.txt").write_text(letter, encoding="utf-8")
    return len(fraud)


def write_oltp(customers: pd.DataFrame, cards: pd.DataFrame, merchants: pd.DataFrame, path: Path) -> None:
    if path.exists():
        path.unlink()
    with sqlite3.connect(path) as con:
        customers.drop(columns=["spend_multiplier"]).to_sql("customers", con, index=False)
        cards.to_sql("cards", con, index=False)
        merchants.to_sql("merchants", con, index=False)


def simulate_customer_updates(n: int = 40, when: datetime | None = None, seed: int = 99) -> int:
    """Mutate some customer rows in the OLTP database, as an app would.

    Run this between two CDC loads to see SCD Type 2 history appear in silver.dim_customer.
    """
    s = get_settings()
    rng = np.random.default_rng(seed)
    when = when or (START + timedelta(days=s.n_days))
    with sqlite3.connect(s.sources_dir / "oltp.sqlite") as con:
        ids = [r[0] for r in con.execute("SELECT customer_id FROM customers ORDER BY RANDOM() LIMIT ?", (n,))]
        for cid in ids:
            if rng.random() < 0.5:
                con.execute("UPDATE customers SET risk_tier=?, updated_at=? WHERE customer_id=?",
                            (str(rng.choice(["low", "medium", "high"])), when.isoformat(), cid))
            else:
                con.execute("UPDATE customers SET home_lat=home_lat+?, home_lon=home_lon+?, updated_at=? "
                            "WHERE customer_id=?", (float(rng.normal(0, 2)), float(rng.normal(0, 2)), when.isoformat(), cid))
    return len(ids)


def generate_all() -> dict:
    """Generate every source system. Deterministic for a given SENTINEL_SEED."""
    s = get_settings()
    rng = np.random.default_rng(s.seed)
    src = s.sources_dir
    customers = make_customers(rng, s.n_customers)
    cards = make_cards(rng, customers)
    merchants = make_merchants(rng, customers)
    tx, labels = make_transactions(rng, customers, cards, merchants, s.n_days)
    write_oltp(customers, cards, merchants, src / "oltp.sqlite")
    stream_stats = write_event_log(rng, tx, src / "events" / "transactions.jsonl")
    (src / "labels").mkdir(parents=True, exist_ok=True)
    labels.to_csv(src / "labels" / "chargebacks.csv", index=False)
    complaints = make_complaints(rng, customers, merchants, cards, s.n_days)
    complaints.to_csv(src / "complaints.csv", index=False)
    n_letters = write_dispute_letters(rng, tx, customers, merchants, cards, src / "disputes")
    # Ground truth kept separately ONLY for the demo's "replay" feature and tests.
    tx[["txn_id", "is_fraud"]].to_parquet(src / "_ground_truth.parquet", index=False)
    return {
        "customers": len(customers), "merchants": len(merchants), "transactions": len(tx),
        "fraud_rate": round(float(tx.is_fraud.mean()), 4), "chargebacks": len(labels),
        "complaints": len(complaints), "dispute_letters": n_letters, **stream_stats,
    }


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(generate_all(), indent=2))
