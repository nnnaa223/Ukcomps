# --- HEURISTIC SCORE ENGINE ---
def calculate_score(title, source):
    """Calculates visibility & odds score based on user requirements."""
    score = 80.0
    text_lower = title.lower()

    # ALCOHOL FILTER: Instantly drop score to 0 to exclude alcohol prizes
    alcohol_terms = [
        "wine", "beer", "gin", "whisky", "whiskey", "vodka", "rum", 
        "prosecco", "champagne", "cider", "ale", "brewery", "distillery", 
        "cocktail", "liquor", "spirits", "bottle of", "booze", "IPA"
    ]
    for term in alcohol_terms:
        # Use word boundaries via regex so "cabinet" or "ginger" aren't accidentally flagged
        if re.search(r'\b' + re.escape(term) + r'\b', text_lower):
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
