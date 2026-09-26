import streamlit as st
import pandas as pd
import sqlite3
import requests
from bs4 import BeautifulSoup
import datetime
import time
import urllib.parse
import re

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
            closing_date TEXT,
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

def extract_closing_date(text):
    """Simple regex parser to detect closing dates in post titles/captions."""
    match = re.search(r'(closes|ending|ends|entry by)\s*(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+|\d{1,2}/\d{1,2})', text, re.IGNORECASE)
    if match:
        return match.group(0)
    return "Not Specified"

# --- MULTI-SOURCE ENGINE (IG, X/TWITTER, WEBSITES, FORUMS) ---
def fetch_competitions():
    new_comps = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }

    # 1. X (TWITTER) SEARCH QUERIES
    x_queries = [
        'site:x.com "uk giveaway" OR "win" "retweet" "uk only"',
        'site:x.com "competition" "RT to win" "uk"'
    ]

    for query in x_queries:
        encoded_query = urllib.parse.quote(query)
        google_x_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-GB&gl=GB&ceid=GB:en"
        
        try:
            resp = requests.get(google_x_url, headers=headers, timeout=8)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, "xml")
                items = soup.find_all("item")
                
                for item in items[:10]:
                    title = item.find("title").text if item.find("title") else "X UK Giveaway"
                    link = item.find("link").text if item.find("link") else ""

                    if link:
                        clean_title = f"𝕏: {title.replace(' - X', '').replace(' - Twitter', '')}"
                        closing_info = extract_closing_date(clean_title)
                        
                        # High visibility score (99 pts) due to low entry volume on direct X posts
                        new_comps.append((
                            clean_title.strip(),
                            link.strip(),
                            "X (Twitter)",
                            99.0,
                            datetime.date.today().isoformat(),
                            closing_info
                        ))
        except Exception:
            pass

    # 2. INSTAGRAM SEARCH QUERIES
    ig_queries = [
        'site:instagram.com/p/ "uk giveaway" OR "win" "uk only"',
        'site:instagram.com/p/ "competition" "enter to win"'
    ]

    for query in ig_queries:
        encoded_query = urllib.parse.quote(query)
        google_ig_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-GB&gl=GB&ceid=GB:en"
        
        try:
            resp = requests.get(google_ig_url, headers=headers, timeout=8)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, "xml")
                items = soup.find_all("item")
                
                for item in items[:10]:
                    title = item.find("title").text if item.find("title") else "Instagram UK Giveaway"
                    link = item.find("link").text if item.find("link") else ""

                    if link:
                        clean_title = f"📸 IG: {title.replace(' - Instagram', '')}"
                        closing_info = extract_closing_date(clean_title)
                        
                        new_comps.append((
                            clean_title.strip(),
                            link.strip(),
                            "Instagram",
                            98.0,
                            datetime.date.today().isoformat(),
                            closing_info
                        ))
        except Exception:
            pass

    # 3. NICHE WEB SEARCH FEEDS
    web_queries = [
        'site:.co.uk "win" "competition" "terms and conditions"',
        'site:.co.uk "giveaway" "enter to win"'
    ]

    for query in web_queries:
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
                        closing_info = extract_closing_date(title)
                        new_comps.append((
                            title.strip(),
                            link.strip(),
                            "Niche Web Search",
                            90.0,
                            datetime.date.today().isoformat(),
                            closing_info
                        ))
        except Exception:
            pass

    # 4. FORUM & AGGREGATOR FEEDS
    rss_sources = [
        {"name": "ThePrizeFinder", "url": "https://www.theprizefinder.com/feeds/new-competitions"},
        {"name": "Loquax UK", "url": "https://www.loquax.co.uk/forums/forums/-/index.rss"},
        {"name": "MSE Forum", "url": "https://forums.moneysavingexpert.com/categories/competitions/feed.rss"}
    ]

    for source in rss_sources:
        try:
            resp = requests.get(source["url"], headers=headers, timeout=8)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, "xml")
                items = soup.find_all("item")
                
                for item in items[:10]:
                    title = item.find("title").text if item.find("title") else "UK Giveaway"
                    link = item.find("link").text if item.find("link") else ""
                    
                    if not link:
                        continue

                    score = 75.0
                    high_traffic_brands = ["tesco", "itv", "cadbury", "sainsburys", "argos", "amazon", "asda", "morrisons"]
                    for brand in high_traffic_brands:
                        if brand in title.lower():
                            score -= 30.0

                    closing_info = extract_closing_date(title)

                    new_comps.append((
                        title.strip(),
                        link.strip(),
                        source["name"],
                        score,
                        datetime.date.today().isoformat(),
                        closing_info
                    ))
        except Exception:
            pass

    # Save to SQLite DB
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    for comp in new_comps:
        c.execute('''
            INSERT OR IGNORE INTO competitions (title, url, source, visibility_score, date_added, closing_date)
            VALUES (?, ?, ?, ?, ?, ?)
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
    with st.spinner("Scouring X (Twitter), Instagram, blogs, and UK feeds..."):
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
