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
st.set_page_config(page_title="UK High-Odds Competition Tracker", layout="wide")

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
    """
    Extracts date phrases from HTML/text content and formats to ISO or descriptive text.
    """
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
    Validates page structure and returns (is_valid: bool, closing_date: str).
    """
    text_lower = title.lower()
    url_lower = url.lower()

    # Reject widget platforms uniformly
    if any(platform in url_lower for platform in WIDGET_PLATFORM_BLOCKLIST):
        return False, "Not Specified"

    # A. REJECT NEWS/ARTICLE URL PATHS
    article_paths = ["/news/", "/article/", "/story/", "/sport/", "/community/", "/features/", "/press-release/", "/awards/"]
    if any(path in url_lower for path in article_paths):
        return False, "Not Specified"

    # B. REJECT PAST-TENSE & WINNER ANNOUNCEMENT PATTERNS
    winner_story_terms = [
        r"\bwins\b", r"\bwon\b", r"\bwinner\b", r"\bwinners\b", r"\bawarded\b", 
        r"\bscoops\b", r"\bclaims prize\b", r"\btakes home\b", r"\bcrowned\b", 
        r"\bcelebrates\b", r"\bbags\b", r"\bhanded\b", r"\breceives\b", r"\bcongratulations to\b"
    ]
    if any(re.search(pattern, text_lower) for pattern in winner_story_terms):
        return False, "Not Specified"

    # C. REJECT PAYWALLS, TICKETING & LEAD-GEN TRAPS
    paid_or_trap_terms = [
        "ticket required", "buy a ", "entry fee", "per ticket", "raffle ticket", 
        "subscription required", "get a quote", "complete offer", "part 1 of 5", 
        "marketing survey", "insurance quote", "paid entry"
    ]
    if any(trap in text_lower for trap in paid_or_trap_terms):
        return False, "Not Specified"

    # D. MUST CONTAIN ACTIVE ENTRY INTENT
    active_intent_terms = ["enter", "win a", "win this", "giveaway", "competition", "free entry", "prize draw"]
    if not any(term in text_lower for term in active_intent_terms):
        return False, "Not Specified"

    # E. CHECK YEAR AGE
    current_year = datetime.date.today().year
    past_years = [str(y) for y in range(2020, current_year)]
    if any(year in title for year in past_years):
        return False, "Not Specified"

    # F. SPECIAL HANDLING FOR INSTAGRAM LINKS
    # Instagram URLs return login skeletons via requests; bypass raw HTML form check for IG domains
    if "instagram.com" in url_lower:
        return True, "Not Specified"

    # G. LIVE HTTP & HTML FORM INSPECTION
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code != 200:
            return False, "Not Specified"
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        page_text = soup.get_text().lower()
        
        # Check widget blocklist against full response text
        if any(platform in resp.text.lower() for platform in WIDGET_PLATFORM_BLOCKLIST):
            return False, "Not Specified"

        # Hard signals that a page is closed
        closed_signals = [
            "competition closed", "giveaway closed", "winner has been contacted", 
            "no longer accepting entries", "giveaway ended", "entries are now closed",
            "this competition has now ended", "sorry, this competition is over", "congratulations to our winner"
        ]
        if any(signal in page_text for signal in closed_signals):
            return False, "Not Specified"

        if any(trap in page_text for trap in ["complete survey to enter", "purchase necessary", "£ per entry"]):
            return False, "Not Specified"

        # RELAXED FORM CHECK (Step 4): Check forms, inputs, OR common text instructions
        has_form = bool(soup.find("form"))
        has_input = bool(soup.find("input", {"type": re.compile(r"email|text|submit|radio|checkbox", re.I)}))
        has_entry_phrases = any(phrase in page_text for phrase in [
            "fill in", "enter details", "enter below", "complete the form", 
            "to enter", "leave a comment", "comment below", "tag a friend"
        ])

        if not (has_form or has_input or has_entry_phrases):
            return False, "Not Specified"

        # Extract closing date from complete page text
        closing_date = extract_closing_date(page_text)

        return True, closing_date
    except Exception:
        return False, "Not Specified"

# --- 6. HIGH-PROBABILITY HEURISTIC SCORING ENGINE ---
def calculate_score(title, url):
    score = 70.0
    text_lower = title.lower()
    url_lower = url.lower()

    if any(k in url_lower for k in [".co.uk/blog", "wordpress", "blogspot", "local", "independent"]):
        score += 25.0
    
    if any(k in text_lower for k in ["answer a question", "fill in form", "email to enter", "comment below"]):
        score += 15.0

    if any(k in text_lower for k in ["closes today", "ends tonight", "24 hours left", "ends tomorrow", "quick enter"]):
        score += 20.0

    return max(score, 0.0)

# --- 7. PARALLEL SCRAPER ENGINE (SERPAPI) ---
def process_single_search_result(item):
    """
    Worker function executed in parallel threads to evaluate SerpApi organic results.
    """
    title = item.get("title", "UK Competition Entry")
    final_url = item.get("link", "")

    if not final_url:
        return None, "rejected"

    # Social exclusions (Twitter, Facebook, TikTok are blocked; Instagram is ALLOWED)
    if any(social in final_url.lower() for social in ["x.com", "twitter.com", "facebook.com", "tiktok.com"]):
        return None, "rejected"

    is_valid, closing_date = is_valid_competition_entry(final_url, title)
    if is_valid:
        score = calculate_score(title, final_url)
        if score >= 30.0:
            return {
                "title": title.strip(),
                "url": final_url.strip(),
                "source": "SerpApi Direct",
                "visibility_score": score,
                "date_added": datetime.date.today().isoformat(),
                "closing_date": closing_date
            }, "passed"

    return None, "rejected"

def fetch_competitions():
    if not supabase:
        st.error("Cannot fetch: Supabase is not connected.")
        return

    serpapi_key = st.secrets.get("SERPAPI_KEY", "").strip()
    if not serpapi_key:
        st.error("⚠️ Missing SerpApi Key! Please set SERPAPI_KEY in Streamlit Cloud Secrets.")
        return

    # STEP 3: Relaxed and broadened SerpApi queries including explicit Instagram coverage
    queries = [
        'site:.co.uk competition "enter"',
        'site:.co.uk giveaway "win"',
        'site:.co.uk/competitions "closing date"',
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

    # Deduplicate raw items by URL before processing
    unique_items = {item.get("link"): item for item in raw_items if item.get("link")}.values()

    new_comps = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(process_single_search_result, unique_items)
        for comp, status in results:
            if comp:
                new_comps.append(comp)
                stats["passed_validation"] += 1
            else:
                stats["rejected"] += 1

    # Save verified entries to Supabase
    for comp in new_comps:
        try:
            supabase.table("competitions").upsert(comp, on_conflict="url").execute()
        except Exception:
            stats["db_errors"] += 1

    # Render execution summary stats to UI
    st.info(
        f"**Fetch Completed!** Diagnostics:\n"
        f"- Queries Run: {stats['queries_run']} | API Errors: {stats['api_errors']}\n"
        f"- Raw Candidates Discovered: {stats['raw_candidates']}\n"
        f"- Rejected (Failed Form/Platform Validation): {stats['rejected']}\n"
        f"- Passed & Saved to DB: {stats['passed_validation']} (DB Errors: {stats['db_errors']})"
    )

# --- 8. USER INTERFACE ---
header_col1, header_col2 = st.columns([3, 1])

with header_col1:
    st.title("🏆 UK High-Probability Competition Finder")
    st.caption("Filters out paywalls, quote traps, articles, widget platforms, and pages without active entry forms.")

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
        with st.spinner("Executing SerpApi searches and inspecting entry pages..."):
            fetch_competitions()
            time.sleep(1)
            st.rerun()

    try:
        response = supabase.table("competitions").select("*").eq("clicked", False).or_("dismissed.is.null,dismissed.eq.false").order("visibility_score", desc=True).execute()
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
            st.caption(f"Score: **{int(row['visibility_score'])} pts** | Added: {row['date_added']}{closing_text}")
            
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
