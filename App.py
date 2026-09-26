# --- HEURISTIC SCORE ENGINE ---
def calculate_score(title, source):
    """Calculates visibility & odds score based on user requirements."""
    score = 80.0
    text_lower = title.lower()

    # 1. DISCARD GENERAL NEWS & EDITORIAL ARTICLES
    news_junk_terms = [
        "says", "report", "police", "court", "council", "mayor", "minister", 
        "award", "won award", "wins award", "sports", "match", "league", 
        "accident", "crash", "investigation", "recap", "review", "opinion"
    ]
    for term in news_junk_terms:
        if term in text_lower:
            return 0.0

    # 2. MUST CONTAIN AN INTENT TO GIVE SOMETHING AWAY
    comp_intent_terms = ["win", "giveaway", "competition", "free entry", "prize", "enter to win"]
    if not any(intent in text_lower for intent in comp_intent_terms):
        return 0.0

    # NO. 2 PENALTIES: Avoid photo uploads or mandatory friend tagging
    avoid_terms = ["tag a friend", "tag 3 friends", "tag your friends", "upload a photo", "submit a video", "picture entry"]
    for term in avoid_terms:
        if term in text_lower:
            score -= 80.0

    # NO. 2 BOOSTS: Short expiry and high-potential platforms
    if any(term in text_lower for term in ["closes today", "ends tonight", "24 hours left", "ends tomorrow"]):
        score += 25.0
        
    if source in ["Instagram", "X (Twitter)", "Local Business UK"]:
        score += 20.0

    # High-traffic national brand penalty
    high_traffic_brands = ["tesco", "itv", "cadbury", "sainsburys", "argos", "amazon", "asda", "mcdonalds"]
    for brand in high_traffic_brands:
        if brand in text_lower:
            score -= 30.0

    return max(score, 0.0)

# --- SCRAPING & INGESTION ENGINES ---
def fetch_competitions():
    if not supabase:
        st.error("Cannot fetch: Supabase is not connected.")
        return

    new_comps = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    # EXCLUDE GENERAL NEWS TERMS IN GOOGLE SEARCH PARAMETERS
    negative_operators = "-news -court -police -match -award -league -report -council"

    # 1. LOCAL & REGIONAL BUSINESS ENGINE
    local_queries = [
        f'site:.co.uk "enter to win" OR "competition" ("London" OR "Manchester" OR "Birmingham" OR "Yorkshire") {negative_operators}',
        f'site:.co.uk "win" "giveaway" ("local business" OR "independent shop") {negative_operators}'
    ]
    
    for query in local_queries:
        encoded_query = urllib.parse.quote(query)
        google_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-GB&gl=GB&ceid=GB:en"
        try:
            resp = requests.get(google_url, headers=headers, timeout=8)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, "xml")
                for item in soup.find_all("item")[:8]:
                    title = item.find("title").text if item.find("title") else ""
                    link = item.find("link").text if item.find("link") else ""
                    if link and title and is_link_valid(link):
                        score = calculate_score(title, "Local Business UK")
                        if score > 20.0:
                            new_comps.append({
                                "title": f"📍 {title.strip()}",
                                "url": link.strip(),
                                "source": "Local Business UK",
                                "visibility_score": score,
                                "date_added": datetime.date.today().isoformat(),
                                "closing_date": extract_closing_date(title)
                            })
        except Exception:
            pass

    # 2. X & INSTAGRAM ENGINES
    social_queries = [
        (f'site:x.com "uk giveaway" OR "competition" "retweet" "uk only" {negative_operators}', "X (Twitter)", "𝕏: "),
        (f'site:instagram.com/p/ "uk giveaway" OR "win" "uk only" {negative_operators}', "Instagram", "📸 IG: ")
    ]

    for query, source_label, prefix in social_queries:
        encoded_query = urllib.parse.quote(query)
        google_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-GB&gl=GB&ceid=GB:en"
        try:
            resp = requests.get(google_url, headers=headers, timeout=8)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, "xml")
                for item in soup.find_all("item")[:8]:
                    title = item.find("title").text if item.find("title") else ""
                    link = item.find("link").text if item.find("link") else ""
                    if link and title and is_link_valid(link):
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
