import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import datetime
import time
import re
from concurrent.futures import ThreadPoolExecutor
from supabase import create_client, Client

# --- 1. PAGE CONFIG & LAYOUT ---
st.set_page_config(page_title="UK Live Competition Tracker", layout="wide")

# --- 2. WIDGET PLATFORM BLOCKLIST ---
WIDGET_PLATFORM_BLOCKLIST = [
    "gleam.io", "rafflecopter", "promosimple", "woobox", 
    "kingsumo", "viralsweep", "wishpond", "shortstack", "vyper"
]

# --- 3. SUPABASE INITIALIZATION ---
@st.cache_resource
def init_supabase():
    try:
        url = st.secrets.get("SUPABASE_URL", "").strip().rstrip("/")
        key = st.secrets.get("SUPABASE_KEY", "").strip()
        if not url or not key:
            st.error("⚠️ Missing Supabase secrets! Update Streamlit Cloud Settings -> Secrets.")
            return None
        return create_client(url, key)
    except Exception as e:
        st.error(f"⚠️ Failed to connect to Supabase: {e}")
        return None

supabase = init_supabase()

def mark_as_clicked(comp_id):
    if supabase:
        supabase.table("competitions").update({"clicked": True}).eq("id", comp_id).execute()

def mark_as_dismissed(comp_id):
    if supabase:
        supabase.table("competitions").update({"dismissed": True}).eq("id", comp_id).execute()

# --- 4. CLOSING DATE EXTRACTION HELPER ---
def extract_closing_date(text):
    match = re.search(r'(closes|ending|ends|entry by|closing date)\s*:\?\s*(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+|\d{1,2}/\d{1,2}(?:/\d{2,4})?)', text, re.IGNORECASE)
    if not match:
        return "Not Specified"
    
    raw_date = match.group(2)
    if "/" in raw_date:
        parts = raw_date.split("/")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            day, month = int(parts[0]), int(parts[1])
            year = datetime.date.today().year
            return f"{year}-{month:02d}-{day:02d}"
    
    return raw_date

# --- 5. HARD EXCLUSIONS & FORM-VALIDATION ENGINE ---
def is_valid_competition_entry(url, title):
    """
    Evaluates page structure.
    Returns: (is_valid: bool, closing_date_or_reason: str)
    """
    text_lower = title.lower()
    url_lower = url.lower()

    # Block Widget Platforms
    if any(platform in url_lower for platform in WIDGET_PLATFORM_BLOCKLIST):
        return False, "Widget Platform Blocked"

    # Reject T&Cs / Privacy / Rules
    tc_paths_and_terms = [
        "/terms", "/terms-and-conditions", "/terms-conditions", "/rules", 
        "/t-and-cs", "/tc", "/tcs", "terms & conditions", "terms and conditions",
        "competition rules", "privacy policy", "/privacy", "/legal"
    ]
    if any(tc in url_lower or tc in text_lower for tc in tc_paths_and_terms):
        return False, "Terms & Conditions Page"

    # Reject News / Articles
    article_paths = ["/news/", "/article/", "/story/", "/sport/", "/community/", "/features/", "/press-release/", "/awards/"]
    if any(path in url_lower for path in article_paths):
        return False, "Article/News URL Path"

    # Reject Past Winner Stories
    winner_story_terms = [
        r"\bwins\b", r"\bwon\b", r"\bwinner\b", r"\bwinners\b", r"\bawarded\b", 
        r"\bscoops\b", r"\bclaims prize\b", r"\btakes home\b", r"\bcrowned\b", 
        r"\bcelebrates\b", r"\bbags\b", r"\bhanded\b", r"\breceives\b", r"\bcongratulations to\b"
    ]
    if any(re.search(pattern, text_lower) for pattern in winner_story_terms):
        return False, "Winner Announcement / Past Tense"

    # Reject Paywalls & Quote Traps
    paid_or_trap_terms = [
        "ticket required", "buy a ", "entry fee", "per ticket", "raffle ticket", 
        "subscription required", "get a quote", "complete offer", "part 1 of 5", 
        "marketing survey", "insurance quote", "paid entry"
    ]
    if any(trap in text_lower for trap in paid_or_trap_terms):
        return False, "Paywall or Lead-Gen Trap"

    # Must contain entry intent in title
    active_intent_terms = ["enter", "win a", "win this", "giveaway", "competition", "free entry", "prize draw"]
    if not any(term in text_lower for term in active_intent_terms):
        return False, "Missing Entry Intent in Title"

    # Instagram Bypass for Raw HTML Checks
    if "instagram.com" in url_lower:
        return True, "Not Specified"

    # Live HTTP Inspection
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code != 200:
            return False, f"HTTP Error ({resp.status_code})"
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        page_text = soup.get_text().lower()
        
        if any(platform in resp.text.lower() for platform in WIDGET_PLATFORM_BLOCKLIST):
            return False, "Widget Platform in Page HTML"

        closed_signals = [
            "competition closed", "giveaway closed", "winner has been contacted", 
            "no longer accepting entries", "giveaway ended", "entries are now closed",
            "this competition has now ended", "sorry, this competition is over", "congratulations to our winner"
        ]
        if any(signal in page_text for signal in closed_signals):
            return False, "Competition Closed / Ended"

        if any(trap in page_text for trap in ["complete survey to enter", "purchase necessary", "£ per entry"]):
            return False, "Survey or Paid Requirement in Body"

        has_form = bool(soup.find("form"))
        has_input = bool(soup.find("input", {"type": re.compile(r"email|text|submit|radio|checkbox", re.I)}))
        has_entry_phrases = any(phrase in page_text for phrase in [
            "fill in", "enter details", "enter below", "complete the form", 
            "to enter", "leave a comment", "comment below", "tag a friend"
        ])

        if not (has_form or has_input or has_entry_phrases):
            return False, "No Form or Entry Instructions Detected"

        closing_date = extract_closing_date(page_text)
        return True, closing_date
    except Exception as e:
        return False, f"Connection Failed ({type(e).__name__})"

# --- 6. PARALLEL SCRAPER ENGINE ---
def process_single_search_result(item):
    title = item.get("title", "UK Competition Entry")
    final_url = item.get("link", "")

    if not final_url:
        return None, None

    if any(social in final_url.lower() for social in ["x.com", "twitter.com", "facebook.com", "tiktok.com"]):
        return None, {
            "title": title.strip(),
            "url": final_url.strip(),
            "reason": "Social Network Blocked (X/FB/TikTok)",
            "date_added": datetime.date.today().isoformat()
        }

    is_valid, date_or_reason = is_valid_competition_entry(final_url, title)
    if is_valid:
        return {
            "title": title.strip(),
            "url": final_url.strip(),
            "source": "SerpApi Direct",
            "visibility_score": 100.0,
            "date_added": datetime.date.today().isoformat(),
            "closing_date": date_or_reason
        }, None
    else:
        return None, {
            "title": title.strip(),
            "url": final_url.strip(),
            "reason": date_or_reason,
            "date_added": datetime.date.today().isoformat()
        }

def fetch_competitions():
    if not supabase:
        st.error("Cannot fetch: Supabase is not connected.")
        return

    serpapi_key = st.secrets.get("SERPAPI_KEY", "").strip()
    if not serpapi_key:
        st.error("⚠️ Missing SerpApi Key! Please set SERPAPI_KEY in Streamlit Cloud Secrets.")
        return

    queries = [
        'site:.co.uk competition "enter" -terms',
        'site:.co.uk giveaway "win" -terms',
        'site:.co.uk/competitions "win" "closing date" -terms',
        'site:instagram.com "uk giveaway" OR "uk competition" "win"'
    ]

    stats = {
        "queries_run": 0,
        "api_errors": 0,
        "raw_candidates": 0,
        "rejected": 0,
        "passed_validation": 0,
        "db_errors": 0
    }

    raw_items = []
    for query in queries:
        stats["queries_run"] += 1
        params = {
            "engine": "google",
            "q": query,
            "gl": "gb",
            "hl": "en",
            "tbs": "qdr:m2",
            "api_key": serpapi_key
        }
        try:
            resp = requests.get("https://serpapi.com/search", params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("organic_results", [])
                raw_items.extend(results)
                stats["raw_candidates"] += len(results)
            else:
                stats["api_errors"] += 1
        except Exception:
            stats["api_errors"] += 1

    unique_items = {item.get("link"): item for item in raw_items if item.get("link")}.values()

    passed_comps = []
    rejected_comps = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(process_single_search_result, unique_items)
        for valid_item, rejected_item in results:
            if valid_item:
                passed_comps.append(valid_item)
                stats["passed_validation"] += 1
            elif rejected_item:
                rejected_comps.append(rejected_item)
                stats["rejected"] += 1

    # Save passed competitions
    for comp in passed_comps:
        try:
            supabase.table("competitions").upsert(comp, on_conflict="url").execute()
        except Exception:
            stats["db_errors"] += 1

    # Save rejected items for inspection
    for rej in rejected_comps:
        try:
            supabase.table("rejections").upsert(rej, on_conflict="url").execute()
        except Exception:
            pass

    st.info(
        f"**Fetch Completed!** Diagnostics:\n"
        f"- Queries Run: {stats['queries_run']} | API Errors: {stats['api_errors']}\n"
        f"- Raw Candidates (Past 2 Months): {stats['raw_candidates']}\n"
        f"- Rejected (Logged for Inspection): {stats['rejected']}\n"
        f"- Passed & Saved to DB: {stats['passed_validation']} (DB Errors: {stats['db_errors']})"
    )

# --- 7. USER INTERFACE ---
header_col1, header_col2 = st.columns([3, 1])

with header_col1:
    st.title("🏆 UK Live Competition Finder")
    st.caption("Active giveaways & competitions from blogs, sites, and Instagram (past 2 months).")

with header_col2:
    completed_count = 0
    if supabase:
        try:
            res = supabase.table("competitions").select("id", count="exact").eq("clicked", True).execute()
            completed_count = res.count if res.count is not None else len(res.data)
        except Exception:
            completed_count = 0
    st.metric(label="✅ Completed Comps", value=completed_count)

if not supabase:
    st.warning("⚠️ Database connection missing. Check Streamlit Cloud Settings -> Secrets.")
else:
    if st.button("🔄 Fetch & Rank Live Competitions Now"):
        with st.spinner("Searching for live UK competitions from the past 2 months..."):
            fetch_competitions()
            time.sleep(1)
            st.rerun()

    try:
        response = supabase.table("competitions").select("*").eq("clicked", False).or_("dismissed.is.null,dismissed.eq.false").order("date_added", desc=True).execute()
        data = response.data
    except Exception as e:
        st.error(f"Error querying Supabase: {e}")
        data = []

    st.subheader(f"Available Opportunities ({len(data)})")

    if not data:
        st.info("No unclicked competitions available. Tap 'Fetch & Rank Live Competitions Now' above.")
    else:
        for row in data:
            st.markdown(f"### [{row['title']}]({row['url']})")
            closing_text = f" | ⏳ **Closing:** {row['closing_date']}" if row['closing_date'] != "Not Specified" else ""
            st.caption(f"Added: **{row['date_added']}**{closing_text}")
            
            col1, col2, col3 = st.columns([2, 1, 1])
            with col1:
                st.link_button("Open Competition ↗", row["url"], use_container_width=True)
            with col2:
                if st.button("Completed ✅", key=f"btn_complete_{row['id']}", use_container_width=True):
                    mark_as_clicked(row['id'])
                    st.rerun()
            with col3:
                if st.button("Not Interested ❌", key=f"btn_dismiss_{row['id']}", use_container_width=True):
                    mark_as_dismissed(row['id'])
                    st.rerun()
            st.divider()

    # --- REJECTIONS MANUAL RESCUE UI ---
    st.write("---")
    with st.expander("🔍 Inspect Recently Rejected Candidates (Manual Rescue)"):
        try:
            rej_response = supabase.table("rejections").select("*").order("id", desc=True).limit(30).execute()
            rej_data = rej_response.data
            
            if not rej_data:
                st.write("No rejected items logged yet.")
            else:
                st.caption("Review items filtered out by your validation engine. If any look legitimate, click Rescue to add them to your active list.")
                for row in rej_data:
                    col_a, col_b = st.columns([3, 1])
                    with col_a:
                        st.markdown(f"**[{row['title']}]({row['url']})**")
                        st.caption(f"Reason: `{row['reason']}` | Added: {row['date_added']}")
                    with col_b:
                        if st.button("Rescue & Move to Live ↗", key=f"btn_rescue_{row['id']}", use_container_width=True):
                            # Move to competitions table
                            supabase.table("competitions").upsert({
                                "title": row["title"],
                                "url": row["url"],
                                "source": "Manual Rescue",
                                "visibility_score": 100.0,
                                "date_added": row["date_added"],
                                "closing_date": "Not Specified"
                            }, on_conflict="url").execute()
                            # Delete from rejections
                            supabase.table("rejections").delete().eq("id", row["id"]).execute()
                            st.rerun()
                    st.divider()
        except Exception as e:
            st.info("Rejections table not ready yet. Ensure you ran the SQL query in Supabase!")
