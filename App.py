import streamlit as st
import pandas as pd
import sqlite3
import requests
from bs4 import BeautifulSoup
import datetime
import time
import urllib.parse

# --- DATABASE SETUP ---
DB_FILE = "competitions.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS competitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            url TEXT UNIQUE,
            source TEXT,
            visibility_score REAL,
            date_added TEXT,
            clicked INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

def mark_as_clicked(comp_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE competitions SET clicked = 1 WHERE id = ?", (comp_id,))
    conn.commit()
    conn.close()

# --- MULTI-SOURCE NICHE SCRAPING ENGINE ---
def fetch_competitions():
    """Fetches competitions from multiple niche feeds and search engines."""
    new_comps = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }

    # 1. RSS FEEDS (MSE + ThePrizeFinder + Loquax)
    rss_sources = [
        {"name": "MSE Forum", "url": "https://forums.moneysavingexpert.com/categories/competitions/feed.rss"},
        {"name": "ThePrizeFinder", "url": "https://www.theprizefinder.com/feeds/new-competitions"},
        {"name": "Loquax UK", "url": "https://www.loquax.co.uk/forums/forums/-/index.rss"}
    ]

    for source in rss_sources:
        try:
            resp = requests.get(source["url"], headers=headers, timeout=8)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, "xml")
                items = soup.find_all("item")
                
                for item in items[:15]:  # Take top 15 from each
                    title = item.find("title").text if item.find("title") else "UK Giveaway"
                    link = item.find("link").text if item.find("link") else ""
                    
                    if not link:
                        continue

                    # Scoring Logic (Higher score = lower visibility / better opportunity)
                    score = 80.0
                    
                    # Bonus points for smaller/niche platforms
                    if source["name"] != "MSE Forum":
                        score += 15.0
                    
                    # Demote massive UK brands that get thousands of entries
                    high_traffic_brands = ["tesco", "itv", "cadbury", "sainsburys", "argos", "amazon", "asda", "morrisons", "mcdonalds"]
                    for brand in high_traffic_brands:
                        if brand in title.lower():
                            score -= 35.0

                    new_comps.append((
                        title.strip(),
                        link.strip(),
                        source["name"],
                        score,
                        datetime.date.today().isoformat()
                    ))
        except Exception:
            pass

    # 2. GOOGLE NEWS / SEARCH RSS (Pulls fresh blog/niche giveaway posts in the UK)
    search_queries = [
        'site:.co.uk "win" "competition" "terms and conditions"',
        'site:.co.uk "giveaway" "enter to win" "closing date"'
    ]

    for query in search_queries:
        encoded_query = urllib.parse.quote(query)
        google_rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-GB&gl=GB&ceid=GB:en"
        
        try:
            resp = requests.get(google_rss_url, headers=headers, timeout=8)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, "xml")
                items = soup.find_all("item")
                
                for item in items[:10]:
                    title = item.find("title").text if item.find("title") else "UK Blog Giveaway"
                    link = item.find("link").text if item.find("link") else ""

                    if link:
                        # Direct niche search results get the highest default visibility priority
                        new_comps.append((
                            title.strip(),
                            link.strip(),
                            "Niche Web Search",
                            95.0,
                            datetime.date.today().isoformat()
                        ))
        except Exception:
            pass

    # Save all found records to SQLite DB
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    for comp in new_comps:
        c.execute('''
            INSERT OR IGNORE INTO competitions (title, url, source, visibility_score, date_added)
            VALUES (?, ?, ?, ?, ?)
        ''', comp)
    conn.commit()
    conn.close()

# --- STREAMLIT DASHBOARD UI ---
st.set_page_config(page_title="UK Low-Entry Competition Dashboard", layout="wide")

init_db()

st.title("🏆 UK Competitions Finder & Tracker")
st.caption("Auto-refreshed daily. Ranked by low visibility (highest potential for low entries).")

# Fetch button
if st.button("🔄 Fetch New Competitions Now"):
    with st.spinner("Scouring internet feeds, blogs, and niche sources..."):
        fetch_competitions()
        st.success("Updated!")
        time.sleep(1)
        st.rerun()

# Load Data
conn = sqlite3.connect(DB_FILE)
df = pd.read_sql_query("SELECT * FROM competitions WHERE clicked = 0 ORDER BY visibility_score DESC, date_added DESC", conn)
conn.close()

st.subheader(f"Available Competitions ({len(df)} Unclicked)")

if df.empty:
    st.info("No unclicked competitions available right now. Tap 'Fetch New Competitions Now' above to load new listings.")
else:
    for idx, row in df.iterrows():
        st.markdown(f"### [{row['title']}]({row['url']})")
        st.caption(f"Source: **{row['source']}** | Added: {row['date_added']} | Score: **{int(row['visibility_score'])} pts**")
        
        col1, col2 = st.columns([1, 1])
        with col1:
            st.markdown(f'<a href="{row["url"]}" target="_blank"><button style="width:100%; padding: 10px; background-color: #4CAF50; color: white; border: none; border-radius: 5px; font-size:16px;">Open Competition ↗</button></a>', unsafe_allow_html=True)
        with col2:
            if st.button("Mark Completed ✅", key=f"btn_{row['id']}"):
                mark_as_clicked(row['id'])
                st.rerun()
        st.divider()
