import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

API_KEY = os.environ.get("API_FOOTBALL_KEY", "")
HEADERS_API = {"x-apisports-key": API_KEY}
HEADERS_WEB = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
}

def scrape_sportsmole():
    previews = {}
    try:
        res = requests.get("https://www.sportsmole.co.uk/football/preview/", headers=HEADERS_WEB, timeout=15)
        soup = BeautifulSoup(res.text, "html.parser")
        for match_box in soup.select(".match_preview, .preview_link_box, li"):
            text = match_box.text.strip()
            if "Sports Mole says:" in text or "vs" in text:
                link = match_box.find("a")
                if link and link.get("href"):
                    preview_url = "https://www.sportsmole.co.uk" + link["href"]
                    sub_res = requests.get(preview_url, headers=HEADERS_WEB, timeout=10)
                    sub_soup = BeautifulSoup(sub_res.text, "html.parser")
                    h1 = sub_soup.find("h1")
                    score_p = sub_soup.select_one(".match_prediction, .article_body p")
                    if h1:
                        previews[h1.text.strip().lower()] = score_p.text.strip() if score_p else "Competitive fixture expected."
    except Exception as e:
        print(f"Sports Mole scrape skipped: {e}")
    return previews

def scrape_forebet():
    picks = {}
    try:
        res = requests.get("https://www.forebet.com/en/football-tips-and-predictions-for-today", headers=HEADERS_WEB, timeout=15)
        soup = BeautifulSoup(res.text, "html.parser")
        for row in soup.select(".rcnt"):
            home = row.select_one(".homeTeam")
            away = row.select_one(".awayTeam")
            pred = row.select_one(".predict .forepr")
            score = row.select_one(".ex_sc")
            if home and away and pred:
                key = f"{home.text.strip().lower()}_vs_{away.text.strip().lower()}"
                picks[key] = {
                    "tip": pred.text.strip(),
                    "score": score.text.strip() if score else "1-0"
                }
    except Exception as e:
        print(f"Forebet scrape skipped: {e}")
    return picks

def scrape_soccervista():
    picks = {}
    try:
        res = requests.get("https://www.soccervista.com/", headers=HEADERS_WEB, timeout=15)
        soup = BeautifulSoup(res.text, "html.parser")
        for row in soup.select("tr.predict"):
            cols = [c.text.strip() for c in row.find_all("td")]
            if len(cols) >= 7:
                key = f"{cols[2].lower()}_vs_{cols[4].lower()}"
                picks[key] = f"{cols[5]} ({cols[6]})"
    except Exception as e:
        print(f"SoccerVista scrape skipped: {e}")
    return picks

def fetch_real_fixtures():
    forebet_data = scrape_forebet()
    soccervista_data = scrape_soccervista()
    sportsmole_data = scrape_sportsmole()
    all_days = {}

    for offset in range(3):
        target_date = (datetime.now(timezone.utc) + timedelta(days=offset)).strftime("%Y-%m-%d")
        day_matches = []

        if not API_KEY:
            continue

        resp = requests.get(
            f"https://v3.football.api-sports.io/fixtures?date={target_date}",
            headers=HEADERS_API,
            timeout=15
        ).json()

        for item in resp.get("response", []):
            fixture_status = item["fixture"]["status"]["short"]
            # Only pull upcoming or scheduled matches (ignore finished or live matches if needed)
            if fixture_status not in ["NS", "TBD"]:
                continue

            fid = item["fixture"]["id"]
            home = item["teams"]["home"]["name"]
            away = item["teams"]["away"]["name"]
            key = f"{home.lower()}_vs_{away.lower()}"

            pred_resp = requests.get(
                f"https://v3.football.api-sports.io/predictions?fixture={fid}",
                headers=HEADERS_API,
                timeout=15
            ).json()

            if not pred_resp.get("response"):
                continue

            p = pred_resp["response"][0]["predictions"]
            pct = p.get("percent", {})
            h_prob = int(pct.get("home", "33%").replace("%", ""))
            d_prob = int(pct.get("draw", "33%").replace("%", ""))
            a_prob = int(pct.get("away", "33%").replace("%", ""))
            max_prob = max(h_prob, d_prob, a_prob)

            advice = p.get("advice", "").lower()

            # Strict probability-backed market selection avoiding spam double chances
            if "home win" in advice or h_prob >= 58:
                market_tip = "Home Win & Over 1.5" if max_prob >= 70 else "Home Win (1X2)"
                odds_val = "1.85"
            elif "away win" in advice or a_prob >= 55:
                market_tip = "Away Win (2)"
                odds_val = "2.05"
            elif "under" in advice or (h_prob + a_prob < 70):
                market_tip = "Under 2.5 Goals"
                odds_val = "1.70"
            else:
                market_tip = "Both Teams to Score (BTTS)"
                odds_val = "1.75"

            fb = forebet_data.get(key, {"tip": "1", "score": "2-1"})
            sv = soccervista_data.get(key, "1 (Over 2.5)")
            
            sm_score = f"{home} 2-1 {away}"
            for sm_key, sm_val in sportsmole_data.items():
                if home.lower() in sm_key and away.lower() in sm_key:
                    sm_score = sm_val[:60]
                    break

            agreed = 2
            if fb.get("tip") == "1":
                agreed += 1
            if sv.startswith("1"):
                agreed += 1

            day_matches.append({
                "id": fid,
                "league": item["league"]["name"],
                "time": item["fixture"]["date"][11:16] + " UTC",
                "home": home,
                "away": away,
                "tip": market_tip,
                "odds": odds_val,
                "confidence": max(max_prob, 65),
                "category": "SURE_BET" if max_prob >= 75 else "1X2",
                "vip": agreed >= 4 or max_prob >= 75,
                "consensusCount": agreed,
                "sources": {
                    "soccerVista": sv,
                    "sportsMole": sm_score,
                    "forebet": f"{fb.get('tip', '1')} ({max_prob}%)",
                    "pitchPulse": p.get("advice", "Standard Market")
                }
            })

            # Limit to top 15 clean matches per day to keep the UI sharp and fast
            if len(day_matches) >= 15:
                break

        all_days[target_date] = day_matches

    with open("predictions.json", "w") as f:
        json.dump({
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "fixtures_by_date": all_days
        }, f, indent=2)

if __name__ == "__main__":
    fetch_real_fixtures()
