import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import datetime
import time
import urllib.parse
import re
from concurrent.futures import ThreadPoolExecutor
from supabase import create_client, Client

# --- 1. PAGE CONFIG & LAYOUT ---
st.set_page_config(page_title="UK High-Odds Competition Tracker", layout="wide")

# --- 2. SUPABASE INITIALIZATION ---
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

# --- 3. URL UN-SHORTENER HELPER ---
def resolve_final_url(url):
    """
    Follows redirects to expand Google News RSS links into true target URLs.
    """
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.head(url, headers=headers, allow_redirects=True, timeout=4)
        return resp.url if resp.status_code == 200 else url
    except Exception:
        return url

# --- 4. HARD EXCLUSIONS & FORM-VALIDATION ENGINE ---
def is_valid_competition_entry(url, title):
    text_lower = title.lower()
    url_lower = url.lower()

    # Reject Gleam links completely
    if "gleam.io" in url_lower:
        return False

    # A. REJECT NEWS/ARTICLE URL PATHS
    article_paths = ["/news/", "/article/", "/story/", "/sport/", "/community/", "/features/", "/press-release/", "/awards/"]
    if any(path in url_lower for path in article_paths):
        return False

    # B. REJECT PAST-TENSE & WINNER ANNOUNCEMENT PATTERNS
    winner_story_terms = [
        r"\bwins\b", r"\bwon\b", r"\bwinner\b", r"\bwinners\b", r"\bawarded\b", 
        r"\bscoops\b", r"\bclaims prize\b", r"\btakes home\b", r"\bcrowned\b", 
        r"\bcelebrates\b", r"\bbags\b", r"\bhanded\b", r"\breceives\b", r"\bcongratulations to\b"
    ]
    if any(re.search(pattern, text_lower) for pattern in winner_story_terms):
        return False

    # C. REJECT PAYWALLS, TICKETING & LEAD-GEN TRAPS
    paid_or_trap_terms = [
        "ticket required", "buy a ", "entry fee", "per ticket", "raffle ticket", 
        "subscription required", "get a quote", "complete offer", "part 1 of 5", 
        "marketing survey", "insurance quote", "paid entry"
    ]
    if any(trap in text_lower for trap in paid_or_trap_terms):
        return False

    # D. MUST CONTAIN ACTIVE ENTRY INTENT
    active_intent_terms = ["enter", "win a", "win this", "giveaway", "competition", "free entry", "prize draw"]
    if not any(term in text_lower for term in active_intent_terms):
        return False

    # E. CHECK YEAR AGE
    current_year = datetime.date.today().year
    past_years = [str(y) for y in range(2020, current_year)]
    if any(year in title for year in past_years):
        return False

    # F. LIVE HTTP & HTML FORM INSPECTION
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code != 200:
            return False
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        page_text = soup.get_text().lower()
        
        # Hard signals that a page is closed
        closed_signals = [
            "competition closed", "giveaway closed", "winner has been contacted", 
            "no longer accepting entries", "giveaway ended", "entries are now closed",
            "this competition has now ended", "sorry, this competition is over", "congratulations to our winner"
        ]
        if any(signal in page_text for signal in closed_signals):
            return False

        if any(trap in page_text for trap in ["complete survey to enter", "purchase necessary", "£ per entry"]):
            return False

        # Structural form presence check
        has_form = bool(soup.find("form"))
        has_input = bool(soup.find("input", {"type": re.compile(r"email|text|submit|radio|checkbox", re.I)}))
        has_widget = any(w in resp.text.lower() for w in ["rafflecopter", "promosimple", "iframe", "glisser"])

        if not (has_form or has_input or has_widget):
            return False

        return True
    except Exception:
        return False

# --- 5. HIGH-PROBABILITY HEURISTIC SCORING ENGINE ---
def calculate_score(title, url):
    score = 70.0
    text_lower = title.lower()
    url_lower = url.lower()

    if any(k in url_lower for k in [".co.uk/blog", "wordpress", "blogspot", "local", "independent"]):
        score += 25.0

    if "rafflecopter" in url_lower:
        score += 30.0
    
    if any(k in text_lower for k in ["answer a question", "fill in form", "email to enter", "comment below"]):
        score += 15.0

    if any(k in text_lower for k in ["closes today", "ends tonight", "24 hours left", "ends tomorrow", "quick enter"]):
        score += 20.0

    if any(k in text_lower for k in ["tag a friend", "tag 3 friends", "upload photo", "submit video", "instagram"]):
        score -= 50.0

    high_traffic_domains = ["tesco", "itv", "cadbury", "sainsburys", "argos", "amazon", "asda", "mcdonalds", "hotukdeals"]
    for brand in high_traffic_domains:
        if brand in text_lower or brand in url_lower:
            score -= 35.0

    return max(score, 0.0)

def extract_closing_date(text):
    """
    Extracts text date phrases and attempts to convert them to ISO format (YYYY-MM-DD).
    """
    match = re.search(r'(closes|ending|ends|entry by)\s*(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+|\d{1,2}/\d{1,2})', text, re.IGNORECASE)
    if not match:
        return "Not Specified"
    
    raw_date = match.group(2)
    # Basic ISO conversion for DD/MM
    if "/" in raw_date:
        parts = raw_date.split("/")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            day, month = int(parts[0]), int(parts[1])
            year = datetime.date.today().year
            return f"{year}-{month:02d}-{day:02d}"
    
    return raw_date

# --- 6. PARALLEL SCRAPER ENGINE ---
def process_single_feed_item(item):
    """
    Worker function executed in parallel threads.
    """
    title = item.find("title").text if item.find("title") else "UK Competition Entry"
    raw_link = item.find("link").text if item.find("link") else ""

    if not raw_link or any(social in raw_link.lower() for social in ["x.com", "twitter.com", "instagram.com", "facebook.com", "tiktok.com", "gleam.io"]):
        return None

    # Resolve Google RSS redirect link to original URL
    final_url = resolve_final_url(raw_link)

    if is_valid_competition_entry(final_url, title):
        score = calculate_score(title, final_url)
        if score >= 30.0:
            return {
                "title": title.strip(),
                "url": final_url.strip(),
                "source": "Direct Web Entry",
                "visibility_score": score,
                "date_added": datetime.date.today().isoformat(),
                "closing_date": extract_closing_date(title)
            }
    return None

def fetch_competitions():
    if not supabase:
        st.error("Cannot fetch: Supabase is not connected.")
        return

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    negative_terms = "-news -court -police -match -award -league -report -council -wins -won -winner -article -ticket -buy -site:gleam.io"
    
    queries = [
        f'site:.co.uk "enter competition" OR "win a" -site:x.com -site:twitter.com -site:instagram.com {negative_terms}',
        f'site:.co.uk/competitions "win" "closing date" -site:x.com -site:instagram.com {negative_terms}',
        f'site:.co.uk "giveaway" "fill in the form" -site:x.com -site:instagram.com {negative_terms}',
        f'site:.co.uk "blog giveaway" "win" "uk residents" {negative_terms}'
    ]

    raw_items = []
    for query in queries:
        encoded_query = urllib.parse.quote(query)
        google_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-GB&gl=GB&ceid=GB:en"
        try:
            resp = requests.get(google_url, headers=headers, timeout=8)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, "xml")
                raw_items.extend(soup.find_all("item")[:15])
        except Exception:
            pass

    # Execute feed validation across 10 parallel threads
    new_comps = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(process_single_feed_item, raw_items)
        new_comps = [r for r in results if r is not None]

    # Save unique entries to database
    for comp in new_comps:
        try:
            supabase.table("competitions").upsert(comp, on_conflict="url").execute()
        except Exception:
            pass

# --- 7. USER INTERFACE ---
header_col1, header_col2 = st.columns([3, 1])

with header_col1:
    st.title("🏆 UK High-Probability Competition Finder")
    st.caption("Filters out paywalls, quote traps, articles, and pages without active entry forms.")

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
        with st.spinner("Scanning feeds in parallel for active HTML entry forms..."):
            fetch_competitions()
            st.success("Database updated with verified active entry pages!")
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
