import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import datetime
import time
import urllib.parse
import re
from supabase import create_client, Client

# --- ALWAYS SET PAGE CONFIG FIRST ---
st.set_page_config(page_title="UK Low-Entry Competition Dashboard", layout="wide")

# --- SAFE SUPABASE INIT ---
@st.cache_resource
def init_supabase():
    try:
        url = st.secrets.get("SUPABASE_URL", "").strip().rstrip("/")
        key = st.secrets.get("SUPABASE_KEY", "").strip()
        if not url or not key:
            st.error("Missing Supabase secrets! Please check Streamlit Cloud Settings -> Secrets.")
            return None
        return create_client(url, key)
    except Exception as e:
        st.error(f"Failed to connect to Supabase: {e}")
        return None

supabase = init_supabase()

def mark_as_clicked(comp_id):
    if supabase:
        supabase.table("competitions").update({"clicked": True}).eq("id", comp_id).execute()

# --- VALIDATION ENGINE (DEAD & CLOSED LINKS) ---
def is_link_valid(url):
    """Verifies link returns 200 OK and is not marked as closed on page."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code != 200:
            return False
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        page_text = soup.get_text().lower()
        
        closed_signals = ["competition closed", "winner has been contacted", "no longer accepting entries", "giveaway ended"]
        for signal in closed_signals:
            if signal in page_text:
                return False
        return True
    except Exception:
        return False

# --- HEURISTIC SCORE ENGINE ---
def calculate_score(title, source):
    """Calculates visibility & odds score based on user requirements."""
    score = 80.0
    text_lower = title.lower()

    # Avoid photo/media uploads or mandatory friend tagging
    avoid_terms = ["tag a friend", "tag 3 friends", "tag your friends", "upload a photo", "submit a video", "picture entry"]
    for term in avoid_terms:
        if term in text_lower:
            score -= 80.0

    # Short expiry boosts
    if any(term in text_lower for term in ["closes today", "ends tonight", "24 hours left", "ends tomorrow"]):
        score += 25.0
        
    if source in ["Instagram", "X (Twitter)"]:
        score += 20.0

    # High-traffic national brand penalty
    high_traffic_brands = ["tesco", "itv", "cadbury", "sainsburys", "argos", "amazon", "asda", "mcdonalds"]
    for brand in high_traffic_brands:
        if brand in text_lower:
            score -= 30.0

    return max(score, 0.0)

def extract_closing_date(text):
    match = re.search(r'(closes|ending|ends|entry by)\s*(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+|\d{1,2}/\d{1,2})', text, re.IGNORECASE)
    return match.group(0) if match else "Not Specified"

# --- SCRAPING ENGINE ---
def fetch_competitions():
    if not supabase:
        st.error("Cannot fetch: Supabase is not connected.")
        return

    new_comps = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    # X & INSTAGRAM SEARCH FEEDS
    social_queries = [
        ('site:x.com "uk giveaway" OR "win" "retweet" "uk only"', "X (Twitter)", "𝕏: "),
        ('site:instagram.com/p/ "uk giveaway" OR "win" "uk only"', "Instagram", "📸 IG: ")
    ]

    for query, source_label, prefix in social_queries:
        encoded_query = urllib.parse.quote(query)
        google_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-GB&gl=GB&ceid=GB:en"
        try:
            resp = requests.get(google_url, headers=headers, timeout=8)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, "xml")
                for item in soup.find_all("item")[:10]:
                    title = item.find("title").text if item.find("title") else "Giveaway"
                    link = item.find("link").text if item.find("link") else ""
                    if link and is_link_valid(link):
                        score = calculate_score(title, source_label)
                        if score > 20.0:
                            new_comps.append({
                                "title": f"{prefix}{title.strip()}",
                                "url": link.strip(),
                                "source": source_label,
                                "visibility_score": score,
                                "date_added": datetime.date.today().isoformat(),
                                "closing_date": extract_closing_date(title)
                            })
        except Exception:
            pass

    # UPSERT TO SUPABASE CLOUD DB
    for comp in new_comps:
        try:
            supabase.table("competitions").upsert(comp, on_conflict="url").execute()
        except Exception:
            pass

# --- DASHBOARD UI ---
st.title("🏆 UK Low-Entry Competition Finder")
st.caption("Connected to Supabase Cloud.")

if not supabase:
    st.warning("⚠️ Database connection missing. Check Streamlit Cloud Settings -> Secrets.")
else:
    if st.button("🔄 Fetch & Validate Competitions Now"):
        with st.spinner("Scouring feeds, checking links, and updating database..."):
            fetch_competitions()
            st.success("Updated & Saved!")
            time.sleep(1)
            st.rerun()

    try:
        response = supabase.table("competitions").select("*").eq("clicked", False).order("visibility_score", desc=True).execute()
        data = response.data
    except Exception as e:
        st.error(f"Error querying Supabase: {e}")
        data = []

    st.subheader(f"Available High-Probability Competitions ({len(data)})")

    if not data:
        st.info("No unclicked competitions available. Tap 'Fetch & Validate Competitions Now' above.")
    else:
        for row in data:
            st.markdown(f"### [{row['title']}]({row['url']})")
            closing_text = f" ⏳ **Closing:** {row['closing_date']}" if row['closing_date'] != "Not Specified" else ""
            st.caption(f"Source: **{row['source']}** | Added: {row['date_added']} | Score: **{int(row['visibility_score'])} pts**{closing_text}")
            
            col1, col2 = st.columns([1, 1])
            with col1:
                st.markdown(f'<a href="{row["url"]}" target="_blank"><button style="width:100%; padding: 10px; background-color: #4CAF50; color: white; border: none; border-radius: 5px; font-size:16px;">Open Competition ↗</button></a>', unsafe_allow_html=True)
            with col2:
                if st.button("Mark Completed ✅", key=f"btn_{row['id']}"):
                    mark_as_clicked(row['id'])
                    st.rerun()
            st.divider()
