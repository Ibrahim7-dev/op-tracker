"""
generate.py — One Piece TCG Price Guide Updater
Fetches live prices from TCGCSV and injects them into index.html.

Usage:
    python3 generate.py

The script reads index.html, updates the PRICE_HISTORY, TOP_CARDS, SETS
price fields, and LAST_UPDATED, then writes index.html back.
"""

import requests
import time
import json
import re
import sys
from datetime import datetime, timezone

# ─── CONFIG ───────────────────────────────────────────────────────────────────

SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'OnePieceTCGPriceGuide/1.0'})
BASE = "https://tcgcsv.com/tcgplayer"
TOP_N = 10  # how many cards to fetch per set

BOX_IDS = {
    "OP01-W1": 450086, "OP01-W2": 557280, "OP02": 455866,
    "OP03":    477176,  "OP04":   485833,  "OP05": 498734,
    "OP06":    515080,  "OP07":   532106,  "OP08": 542504,
    "OP09":    563834,  "OP10":   586671,  "OP11": 620180,
    "OP12":    628346,  "OP13":   628352,  "OP14": 665598,
    "OP15":    682057,  "OP16":   689336,  "EB01": 521161,
    "EB02":    594069,  "EB03":   666891,  "PRB01": 545399,
    "PRB02":   628452,
}

CARD_GROUPS = {
    "OP01-W1": 3188,  "OP01-W2": 3188,  "OP02": 17698,
    "OP03":    22890,  "OP04":   23024,  "OP05": 23213,
    "OP06":    23272,  "OP07":   23387,  "OP08": 23462,
    "OP09":    23589,  "OP10":   23766,  "OP11": 24241,
    "OP12":    24302,  "OP13":   24303,  "OP14": 24537,
    "OP15":    24637,  "OP16":   24664,  "EB01": 23333,
    "EB02":    23834,  "EB03":   24545,  "PRB01": 23496,
    "PRB02":   24305,
}

# Manual overrides: productId -> { price, note }
# Add entries here when TCGCSV market price is null/unreliable.
MANUAL_OVERRIDES = {
    597065: {
        "price": 4500,
        "note": "Last sale 03/12/26 — market price null, low is single-seller listing $30K"
    },
}

# Products to exclude from card rankings (sealed product names)
EXCLUDE_PATTERN = re.compile(
    r'booster box|booster pack|booster case|display|^pack$', re.IGNORECASE
)

# ─── FETCH ────────────────────────────────────────────────────────────────────

def fetch(url, retries=3):
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"  ⚠ Attempt {attempt+1} failed for {url}: {e}")
            time.sleep(1)
    raise RuntimeError(f"Failed to fetch {url} after {retries} attempts")

def get_category_id():
    print("Fetching One Piece category ID...")
    data = fetch(f"{BASE}/categories")
    for cat in data.get("results", []):
        if "one piece" in (cat.get("name") or "").lower():
            print(f"  ✓ Found categoryId: {cat['categoryId']}")
            return cat["categoryId"]
    raise RuntimeError("One Piece category not found in TCGCSV")

def fetch_group(cat_id, group_id):
    products_data = fetch(f"{BASE}/{cat_id}/{group_id}/products")
    time.sleep(0.25)
    prices_data = fetch(f"{BASE}/{cat_id}/{group_id}/prices")
    time.sleep(0.25)

    products = {p["productId"]: p["name"] for p in products_data.get("results", [])}
    prices   = {p["productId"]: p for p in prices_data.get("results", [])}
    return products, prices

# ─── MAIN FETCH LOGIC ─────────────────────────────────────────────────────────

def fetch_all_data():
    cat_id = get_category_id()
    unique_groups = list(set(CARD_GROUPS.values()))

    print(f"Fetching {len(unique_groups)} set groups...")
    all_products = {}  # group_id -> {productId: name}
    all_prices   = {}  # productId -> price object (global, first-write-wins)

    for i, gid in enumerate(unique_groups):
        print(f"  [{i+1}/{len(unique_groups)}] Group {gid}...")
        try:
            products, prices = fetch_group(cat_id, gid)
            all_products[gid] = products
            for pid, p in prices.items():
                if pid not in all_prices:
                    all_prices[pid] = p
        except RuntimeError as e:
            print(f"  ✗ Skipping group {gid}: {e}")

    return cat_id, all_products, all_prices

# ─── BUILD PER-SET DATA ───────────────────────────────────────────────────────

def build_set_data(all_products, all_prices):
    results = {}

    for code in BOX_IDS:
        box_id  = BOX_IDS[code]
        gid     = CARD_GROUPS[code]
        box_p   = all_prices.get(box_id, {})
        box_market = box_p.get("marketPrice")
        box_low    = box_p.get("lowPrice")

        # Top N cards for this group
        group_products = all_products.get(gid, {})
        cards = []
        for pid, name in group_products.items():
            if EXCLUDE_PATTERN.search(name):
                continue
            override = MANUAL_OVERRIDES.get(pid)
            p = all_prices.get(pid, {})
            if override:
                cards.append({
                    "id":         pid,
                    "name":       name,
                    "price":      override["price"],
                    "manual":     True,
                    "manualNote": override["note"],
                })
            elif p.get("marketPrice") and p["marketPrice"] > 0:
                cards.append({
                    "id":    pid,
                    "name":  name,
                    "price": p["marketPrice"],
                })

        cards.sort(key=lambda c: c["price"], reverse=True)
        top10 = cards[:TOP_N]

        sum5  = sum(c["price"] for c in top10[:5])
        sum10 = sum(c["price"] for c in top10)

        results[code] = {
            "boxMarket": box_market,
            "boxLow":    box_low,
            "top10":     top10,
            "sum5":      round(sum5,  2),
            "sum10":     round(sum10, 2),
        }

        status = f"${box_market:.2f}" if box_market else "N/A"
        print(f"  {code}: box={status}, top10 cards={len(top10)}")

    return results

# ─── INJECT INTO HTML ─────────────────────────────────────────────────────────

def build_top_cards_js(set_data):
    """Build the TOP_CARDS JS object string."""
    lines = ["const TOP_CARDS = {"]
    for code, d in set_data.items():
        lines.append(f'  "{code}": [')
        for card in d["top10"]:
            name = card["name"].replace("'", "\\'").replace('"', '\\"')
            manual = card.get("manual", False)
            note   = card.get("manualNote", "")
            if manual:
                note_escaped = note.replace("'", "\\'")
                lines.append(
                    f'    {{ name:"{name}", code:"{card["id"]}", price:{card["price"]}, '
                    f'manual:true, manualNote:"{note_escaped}" }},'
                )
            else:
                lines.append(f'    {{ name:"{name}", code:"{card["id"]}", price:{card["price"]} }},')
        lines.append("  ],")
    lines.append("};")
    return "\n".join(lines)

def build_sets_js(set_data, existing_sets_js):
    """Update only the price field in each SETS entry."""
    def replace_price(match):
        set_id = match.group(1)
        old_price = match.group(2)
        if set_id in set_data and set_data[set_id]["boxMarket"]:
            new_price = round(set_data[set_id]["boxMarket"], 2)
            return match.group(0).replace(f'price:{old_price}', f'price:{new_price}')
        return match.group(0)

    # Match each set entry's id and price
    updated = re.sub(
        r'(\{[^}]*?id:"([^"]+)"[^}]*?price:([\d.]+)[^}]*?\})',
        lambda m: m.group(0).replace(
            f'price:{m.group(3)}',
            f'price:{round(set_data[m.group(2)]["boxMarket"], 2)}'
            if m.group(2) in set_data and set_data[m.group(2)]["boxMarket"]
            else f'price:{m.group(3)}'
        ),
        existing_sets_js,
        flags=re.DOTALL
    )
    return updated

def build_history_entry(date_str, set_data):
    """Build one PRICE_HISTORY entry as a JS object string."""
    lines = [f'  {{']
    lines.append(f'    date: "{date_str}",')
    lines.append(f'    data: {{')
    for code, d in set_data.items():
        price  = d["boxMarket"] or 0
        sum5   = d["sum5"]
        sum10  = d["sum10"]
        lines.append(f'      "{code}": {{ price:{price}, sum5:{sum5}, sum10:{sum10} }},')
    lines.append(f'    }}')
    lines.append(f'  }}')
    return "\n".join(lines)

def inject_into_html(html, set_data, date_str):
    # 1. Update LAST_UPDATED
    html = re.sub(
        r'const LAST_UPDATED = "[^"]*";',
        f'const LAST_UPDATED = "{date_str}";',
        html
    )

    # 2. Replace TOP_CARDS block
    new_top_cards = build_top_cards_js(set_data)
    html = re.sub(
        r'const TOP_CARDS = \{.*?\};',
        new_top_cards,
        html,
        flags=re.DOTALL
    )

    # 3. Update SETS prices
    # Find SETS block and update price fields
    def update_set_price(m):
        block = m.group(0)
        # Extract the id
        id_match = re.search(r'id:"([^"]+)"', block)
        if not id_match:
            return block
        sid = id_match.group(1)
        if sid not in set_data or not set_data[sid]["boxMarket"]:
            return block
        new_price = round(set_data[sid]["boxMarket"], 2)
        return re.sub(r'price:[\d.]+', f'price:{new_price}', block, count=1)

    html = re.sub(
        r'\{[^{}]*?id:"[^"]*?"[^{}]*?\}',
        update_set_price,
        html
    )

    # 4. Append new PRICE_HISTORY entry (avoid duplicates by checking date)
    new_entry = build_history_entry(date_str, set_data)
    if date_str in html:
        print(f"  ℹ History entry for {date_str} already exists — skipping.")
    else:
        # Insert before the closing ]; of PRICE_HISTORY
        html = re.sub(
            r'(const PRICE_HISTORY = \[)(.*?)(\];)',
            lambda m: m.group(1) + m.group(2) + ",\n" + new_entry + "\n" + m.group(3),
            html,
            flags=re.DOTALL
        )
        print(f"  ✓ Added history entry for {date_str}")

    return html

# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("One Piece TCG Price Guide — Auto Updater")
    print("=" * 50)

    # Fetch all data
    _, all_products, all_prices = fetch_all_data()
    set_data = build_set_data(all_products, all_prices)

    # Date string
    date_str = datetime.now(timezone.utc).strftime("%b %-d, %Y")
    print(f"\nDate: {date_str}")

    # Read index.html
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            html = f.read()
        print("✓ Read index.html")
    except FileNotFoundError:
        print("✗ index.html not found — make sure generate.py is in the same directory")
        sys.exit(1)

    # Inject data
    print("Injecting updated data into index.html...")
    html = inject_into_html(html, set_data, date_str)

    # Write back
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("✓ index.html updated successfully")
    print("=" * 50)

if __name__ == "__main__":
    main()
