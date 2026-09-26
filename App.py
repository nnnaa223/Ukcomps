import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import datetime
import time
import urllib.parse
import re
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
            st.error("⚠️ Missing Supabase secrets! Please update Streamlit Cloud Settings -> Secrets.")
            return None
        return create_client(url, key)
    except Exception as e:
        st.error(f"⚠️ Failed to connect to Supabase: {e}")
        return None

supabase = init_supabase()

def mark_as_clicked(comp_id):
    if supabase:
        supabase.table("competitions").update({"clicked": True}).eq("id", comp_id).execute()

# --- 3. LIVE LINK & EXPIRY VALIDATOR ---
def is_link_valid(url):
    """Checks for 200 OK status and screens page text for closure signals."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=6)
        if resp.status_code != 200:
            return False
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        page_text = soup.get_text().lower()
        
        # Hard fail if page explicitly declares closure
        closed_signals = [
            "competition closed", "giveaway closed", "winner has been contacted", 
            "no longer accepting entries", "giveaway ended", "entries are now closed"
        ]
        if any(signal in page_text for signal in closed_signals):
            return False
            
        return True
    except Exception:
        return False

# --- 4. HIGH-PROBABILITY HEURISTIC SCORING ENGINE ---
def calculate_score(title, url):
    """
    Ranks probability:
    + Boosts niche UK sites, independent blogs, local domains, and tight deadlines.
    - Penalizes high-traffic national brands and mandatory media/tagging requirements.
    """
    score = 70.0
    text_lower = title.lower()
    url_lower = url.lower()

    # --- BOOSTS (Niche / Low Traffic / High Probability) ---
    # Independent blogs / local business site patterns
    if any(k in url_lower for k in [".co.uk/blog", "wordpress", "blogspot", "local", "independent"]):
        score += 25.0
    
    # Specific entry types requiring slightly more effort (fewer total entrants)
    if any(k in text_lower for k in ["answer a question", "fill in form", "email to enter", "comment below"]):
        score += 15.0

    # Short Expiry / Urgency (Less time for high entry counts)
    if any(k in text_lower for k in ["closes today", "ends tonight", "24 hours left", "ends tomorrow", "quick enter"]):
        score += 20.0

    # --- PENALTIES (High Traffic / Low Probability / Social Hassle) ---
    # Media/photo upload or friend tagging mandates
    if any(k in text_lower for k in ["tag a friend", "tag 3 friends", "upload photo", "submit video", "instagram"]):
        score -= 50.0

    # Mass-market / High-traffic national aggregators & brands
    high_traffic_domains = ["tesco", "itv", "cadbury", "sainsburys", "argos", "amazon", "asda", "mcdonalds", "hotukdeals"]
    for brand in high_traffic_domains:
        if brand in text_lower or brand in url_lower:
            score -= 35.0

    # Non-competition news noise filter
    junk_terms = ["police", "court", "council", "mayor", "sports match", "league", "accident", "review"]
    if any(junk in text_lower for junk in junk_terms):
        return 0.0

    return max(score, 0.0)

def extract_closing_date(text):
    match = re.search(r'(closes|ending|ends|entry by)\s*(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+|\d{1,2}/\d{1,2})', text, re.IGNORECASE)
    return match.group(0) if match else "Not Specified"

# --- 5. DIRECT UK WEB SCRAPER ---
def fetch_competitions():
    if not supabase:
        st.error("Cannot fetch: Supabase is not connected.")
        return

    new_comps = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    # Target direct UK sites while excluding social media networks & general news
    queries = [
        'site:.co.uk "win" "enter competition" -site:x.com -site:twitter.com -site:instagram.com -site:facebook.com -site:tiktok.com',
        'site:.co.uk/competitions "win" "closing date" -site:x.com -site:instagram.com -site:facebook.com',
        'site:.co.uk "giveaway" "fill in the form" -site:x.com -site:instagram.com -news',
        'site:.co.uk "blog giveaway" "win" "uk residents"'
    ]

    for query in queries:
        encoded_query = urllib.parse.quote(query)
        google_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-GB&gl=GB&ceid=GB:en"
        try:
            resp = requests.get(google_url, headers=headers, timeout=8)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, "xml")
                for item in soup.find_all("item")[:10]:
                    title = item.find("title").text if item.find("title") else "UK Competition Entry"
                    link = item.find("link").text if item.find("link") else ""
                    
                    # Sanity check: Exclude social media domains
                    if link and not any(social in link.lower() for social in ["x.com", "twitter.com", "instagram.com", "facebook.com", "tiktok.com"]):
                        score = calculate_score(title, link)
                        if score >= 30.0 and is_link_valid(link):
                            new_comps.append({
                                "title": title.strip(),
                                "url": link.strip(),
                                "source": "Direct Web Form",
                                "visibility_score": score,
                                "date_added": datetime.date.today().isoformat(),
                                "closing_date": extract_closing_date(title)
                            })
        except Exception:
            pass

    # UPSERT TO SUPABASE DB (Deduplicates on 'url')
    for comp in new_comps:
        try:
            supabase.table("competitions").upsert(comp, on_conflict="url").execute()
        except Exception:
            pass

# --- 6. USER INTERFACE ---
st.title("🏆 UK High-Probability Competition Finder")
st.caption("Scouring direct UK web sources. Prioritizing niche, low-entry competitions.")

if not supabase:
    st.warning("⚠️ Database connection missing. Check Streamlit Cloud Settings -> Secrets.")
else:
    if st.button("🔄 Fetch & Rank Live Competitions Now"):
        with st.spinner("Scouring direct UK web feeds, scoring probability, and updating database..."):
            fetch_competitions()
            st.success("Database updated with fresh opportunities!")
            time.sleep(1)
            st.rerun()

    # Query unclicked items ordered by highest score first
    try:
        response = supabase.table("competitions").select("*").eq("clicked", False).order("visibility_score", desc=True).execute()
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
            st.caption(f"Score: **{int(row['visibility_score'])} pts** (High Probability) | Added: {row['date_added']}{closing_text}")
            
            col1, col2 = st.columns([1, 1])
            with col1:
                st.markdown(f'<a href="{row["url"]}" target="_blank"><button style="width:100%; padding: 10px; background-color: #4CAF50; color: white; border: none; border-radius: 5px; font-size:16px;">Open Competition ↗</button></a>', unsafe_allow_html=True)
            with col2:
                if st.button("Mark Completed ✅", key=f"btn_{row['id']}"):
                    mark_as_clicked(row['id'])
                    st.rerun()
            st.divider()
