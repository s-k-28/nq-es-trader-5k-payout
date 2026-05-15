"""Fetch Jan-Feb 2026 MNQ data from TopStep expired contracts.
Run this when the API comes back online:  python fetch_jan_feb_2026.py
"""
import requests, json, csv, time
from datetime import datetime, timedelta, timezone

API_KEY = "3ClvgDuzSepQqLF6h8myxcjy1+vbMS84r8Axbf0O9H4="
USERNAME = "staunchmaaz"
BASE = "https://api.topstepx.com"

def auth():
    resp = requests.post(f"{BASE}/api/Auth/loginKey",
        json={"userName": USERNAME, "apiKey": API_KEY},
        headers={"Content-Type": "application/json", "accept": "text/plain"},
        timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Auth failed: {data}")
    print("Authenticated.")
    return data["token"]

def search_contracts(token):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "accept": "text/plain"}
    resp = requests.post(f"{BASE}/api/Contract/search",
        json={"live": False, "searchText": "MNQ"},
        headers=headers, timeout=30)
    contracts = resp.json().get("contracts", [])
    print(f"Found {len(contracts)} MNQ contracts:")
    for c in contracts:
        print(f"  {c['id']} - {c.get('name','')} - {c.get('description','')}")
    return contracts

def fetch_bars(token, contract_id, start_dt, end_dt):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    all_bars = []
    chunk_start = start_dt
    while chunk_start < end_dt:
        chunk_end = min(chunk_start + timedelta(minutes=999), end_dt)
        resp = requests.post(f"{BASE}/api/History/retrieveBars", json={
            "contractId": contract_id,
            "startTime": chunk_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endTime": chunk_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "unit": 2, "unitNumber": 1, "limit": 1000,
            "live": False, "includePartialBar": False,
        }, headers=headers, timeout=30)
        bars = resp.json().get("bars", [])
        all_bars.extend(bars)
        if len(all_bars) % 5000 < 1000:
            print(f"  {len(all_bars)} bars fetched, at {chunk_start.strftime('%Y-%m-%d %H:%M')}")
        if len(bars) < 1000:
            chunk_start = chunk_end
        else:
            last_t = bars[-1].get("t", "")
            if last_t:
                chunk_start = datetime.fromisoformat(last_t.replace("Z", "+00:00")) + timedelta(minutes=1)
            else:
                chunk_start = chunk_end
        time.sleep(0.05)
    return all_bars

def save_bars(bars, filename):
    seen = set()
    rows = []
    for b in bars:
        t = b.get("t", "")
        if t not in seen:
            seen.add(t)
            rows.append([t, b["o"], b["h"], b["l"], b["c"], b.get("v", 0)])
    rows.sort(key=lambda r: r[0])
    with open(filename, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["datetime", "open", "high", "low", "close", "volume"])
        w.writerows(rows)
    print(f"Saved {len(rows)} bars to {filename}")
    return len(rows)

def main():
    token = auth()

    # Step 1: Find expired MNQ contracts
    contracts = search_contracts(token)

    # Contracts we need:
    # MNQH26 (March 2026) - covers Dec 2025 to Mar 2026
    # MNQZ25 (Dec 2025) - covers Sep 2025 to Dec 2025
    # We want Jan 1 - Mar 14, 2026 to fill the gap before our M26 data

    h26_id = "CON.F.US.EMNQ.H26"  # Try micro contract ID format
    z25_id = "CON.F.US.EMNQ.Z25"

    # Also try the format we know works
    h26_alt = "CON.F.US.MNQ.H26"

    # Try H26 first (Jan - Mar 2026)
    print(f"\n=== Fetching MNQH26 bars (Jan 1 - Mar 15, 2026) ===")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 3, 15, tzinfo=timezone.utc)

    for cid in [h26_alt, h26_id]:
        print(f"Trying contract ID: {cid}")
        try:
            bars = fetch_bars(token, cid, start, end)
            if bars:
                print(f"Got {len(bars)} bars from {cid}")
                n = save_bars(bars, f"data/mnq_h26_1min.csv")
                break
            else:
                print(f"No bars from {cid}")
        except Exception as e:
            print(f"Error with {cid}: {e}")
    else:
        print("Could not fetch H26 data. Check contract IDs from search results above.")

    # Also try to fetch from Z25 (if H26 doesn't cover January)
    print(f"\n=== Trying MNQZ25 for Dec 2025 - Jan 2026 overlap ===")
    for cid in ["CON.F.US.MNQ.Z25", "CON.F.US.EMNQ.Z25"]:
        print(f"Trying: {cid}")
        try:
            probe_start = datetime(2025, 12, 15, tzinfo=timezone.utc)
            probe_end = datetime(2025, 12, 22, tzinfo=timezone.utc)
            bars = fetch_bars(token, cid, probe_start, probe_end)
            print(f"  Got {len(bars)} bars")
        except Exception as e:
            print(f"  Error: {e}")

    # Combine all data
    print("\n=== Combining with existing M26 data ===")
    import pandas as pd
    existing = pd.read_csv("data/mnq_2026_1min.csv")
    print(f"Existing M26 data: {len(existing)} bars")

    try:
        h26 = pd.read_csv("data/mnq_h26_1min.csv")
        print(f"New H26 data: {len(h26)} bars")
        combined = pd.concat([h26, existing]).drop_duplicates(subset=["datetime"]).sort_values("datetime")
        combined.to_csv("data/mnq_2026_1min.csv", index=False)
        print(f"Combined total: {len(combined)} bars")
        print(f"Range: {combined.datetime.min()} to {combined.datetime.max()}")
    except FileNotFoundError:
        print("No H26 data file found - H26 fetch may have failed")

if __name__ == "__main__":
    main()
