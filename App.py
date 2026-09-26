import streamlit as st
import pandas as pd
import sqlite3
import requests
from bs4 import BeautifulSoup
import datetime
import time

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

# --- SCRAPING & AGGREGATION ENGINE ---
def fetch_competitions():
    """Scrapes active competitions from open UK feeds and estimates visibility score."""
    new_comps = []
    
    # 1. Fetch from RSS / UK Giveaway Feeds (Example: Loquax RSS feed)
    feed_url = "https://www.loquax.co.uk/blog/feed/"
    try:
        resp = requests.get(feed_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(resp.content, "xml")
        items = soup.find_all("item")
        
        for item in items:
            title = item.find("title").text if item.find("title") else "Unknown Giveaway"
            link = item.find("link").text if item.find("link") else ""
            
            # Low-Visibility Scoring Logic:
            # High-profile national brands get penalized because they get thousands of entries
            high_traffic_brands = ["tesco", "itv", "cadbury", "sainsburys", "argos", "amazon", "asda", "morrisons"]
            score = 100.0
            
            for brand in high_traffic_brands:
                if brand in title.lower():
                    score -= 40.0  # High visibility brand, lower priority
            
            # Shorter, niche competitions get a boosted score
            if len(title) < 50:
                score += 15.0
                
            if link:
                new_comps.append((
                    title,
                    link,
                    "Loquax Feed",
                    score,
                    datetime.date.today().isoformat()
                ))
    except Exception as e:
        st.warning(f"Error fetching feed: {e}")

    # 2. Insert into local database (ignoring duplicates)
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

# Top controls
col1, col2 = st.columns([1, 4])
with col1:
    if st.button("🔄 Fetch New Competitions Now"):
        with st.spinner("Scouring internet feeds..."):
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
    st.info("No unclicked competitions available right now. Click 'Fetch New Competitions Now' above.")
else:
    for idx, row in df.iterrows():
        c1, c2, c3, c4 = st.columns([4, 2, 2, 2])
        
        with c1:
            st.markdown(f"**[{row['title']}]({row['url']})**")
            st.caption(f"Source: {row['source']} | Added: {row['date_added']}")
            
        with c2:
            st.metric(label="Low-Visibility Rank", value=f"{int(row['visibility_score'])} pts")
            
        with c3:
            st.write("") # spacing
            st.markdown(f'<a href="{row["url"]}" target="_blank" style="text-decoration:none;"><button style="padding: 8px 16px; background-color: #4CAF50; color: white; border: none; border-radius: 4px; cursor: pointer;">Open Competition ↗</button></a>', unsafe_allow_html=True)
            
        with c4:
            st.write("") # spacing
            if st.button("Mark Completed ✅", key=f"btn_{row['id']}"):
                mark_as_clicked(row['id'])
                st.rerun()
        st.divider()
