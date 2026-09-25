import requests
from bs4 import BeautifulSoup
import re
import time
import json
from typing import Dict, Optional, List
import firebase_admin
from firebase_admin import credentials, firestore
from google.api_core.exceptions import AlreadyExists
import os
from datetime import datetime


class MahajanaSampathaScraper:
    """
    Specialized scraper for Mahajana Sampatha lottery results.
    """

    BASE_URL = "https://www.nlb.lk"
    LOTTERY_SLUG = "mahajana-sampatha"
    LOTTERY_NAME = "Mahajana Sampatha"

    def __init__(self, use_selenium: bool = True):
        self.use_selenium = use_selenium

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })

        self._driver = None

    def _get_driver(self):
        """Initialize Selenium Chrome driver."""
        if self._driver is None and self.use_selenium:
            try:
                from selenium import webdriver
                from selenium.webdriver.chrome.options import Options

                options = Options()
                options.add_argument("--headless")
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                options.add_argument("--disable-gpu")
                options.add_argument("--window-size=1920,1080")

                self._driver = webdriver.Chrome(options=options)
            except Exception as e:
                print(f"⚠️ Selenium initialization error: {e}")
                self.use_selenium = False

        return self._driver

    def fetch_page(self, url: str) -> Optional[str]:
        """Fetch page using Selenium or Requests."""
        if self.use_selenium:
            driver = self._get_driver()
            if driver:
                try:
                    driver.get(url)
                    time.sleep(2)

                    from selenium.webdriver.common.by import By
                    from selenium.webdriver.support.ui import WebDriverWait
                    from selenium.webdriver.support import expected_conditions as EC

                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.TAG_NAME, "body"))
                    )

                    return driver.page_source
                except Exception as e:
                    print(f"⚠️ Selenium error: {e}")

        try:
            response = self.session.get(url, timeout=30)

            if response.status_code == 200 and len(response.text) > 1000:
                return response.text

            print(
                f"⚠️ Request returned status "
                f"{response.status_code}, "
                f"HTML size: {len(response.text)}"
            )
        except Exception as e:
            print(f"⚠️ Request error: {e}")

        return None

    def extract_winning_numbers(self, text: str, draw_number: str) -> Dict:
        result = {
            "symbol": None,
            "numbers": [],
            "found": False
        }

        draw_pattern = (
            rf"(?:Draw|Lottery)"
            rf"\s*(?:No|Number)?\.?\s*:?\s*"
            rf"{draw_number}"
        )

        draw_match = re.search(draw_pattern, text, re.I)

        if not draw_match:
            return result

        start = draw_match.end()
        section = text[start:start + 500]

        # Preferred pattern:
        # W 3 7 4 5 3 7
        pattern = (
            r"\b([A-Z])\s+"
            r"(\d+)\s+"
            r"(\d+)\s+"
            r"(\d+)\s+"
            r"(\d+)\s+"
            r"(\d+)\s+"
            r"(\d+)\b"
        )

        matches = re.findall(pattern, section, re.I)

        for match in matches:
            symbol = match[0]
            numbers = list(match[1:7])
            numbers = [n for n in numbers if 0 <= int(n) <= 99]

            if len(numbers) == 6:
                result["symbol"] = symbol
                result["numbers"] = [f"{int(n):02d}" for n in numbers]
                result["found"] = True
                return result

        # Alternative pattern
        symbol_pattern = (
            r"([A-Z])\s+"
            r"(\d+)\s+"
            r"(\d+)\s+"
            r"(\d+)\s+"
            r"(\d+)\s+"
            r"(\d+)"
            r"(?:\s+(\d+))?"
        )

        matches = re.findall(symbol_pattern, section, re.I)

        for match in matches:
            symbol = match[0]
            numbers = list(match[1:])
            numbers = [n for n in numbers if n and 0 <= int(n) <= 99]

            if len(numbers) >= 5:
                result["symbol"] = symbol
                result["numbers"] = [f"{int(n):02d}" for n in numbers[:6]]
                result["found"] = True
                return result

        return result

    def extract_draw_date(self, text: str) -> Optional[str]:
        date_patterns = [
            r"(?:Date|Draw Date)"
            r"\s*:?\s*"
            r"([A-Za-z]+\s+\d{1,2},?\s+\d{4})",

            r"([A-Za-z]+\s+\d{1,2},?\s+\d{4})",
        ]

        for pattern in date_patterns:
            match = re.search(pattern, text, re.I)
            if match:
                return match.group(1)

        return None

    def convert_date_to_iso(self, date_str: str) -> str:
        """Convert website date like 'September 25, 2026' to '2026-09-25'."""
        if not date_str:
            return ""
        try:
            dt = datetime.strptime(date_str.strip(), "%B %d, %Y")
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return ""

    def get_result(self, draw_number: str) -> Dict:
        url = (
            f"{self.BASE_URL}/results/"
            f"{self.LOTTERY_SLUG}/"
            f"{draw_number}"
        )

        result = {
            "lottery": self.LOTTERY_NAME,
            "draw_number": draw_number,
            "date": None,
            "symbol": None,
            "numbers": [],
            "found": False,
            "url": url
        }

        html = self.fetch_page(url)

        if not html:
            result["error"] = "Failed to fetch page"
            return result

        soup = BeautifulSoup(html, "html.parser")
        all_text = soup.get_text(separator=" ", strip=True)

        result["date"] = self.extract_draw_date(all_text)
        result["date_iso"] = self.convert_date_to_iso(result["date"])

        extracted = self.extract_winning_numbers(all_text, draw_number)

        if extracted["found"]:
            result["symbol"] = extracted["symbol"]
            result["numbers"] = extracted["numbers"]
            result["found"] = True

        return result

    def close(self):
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None


# ==========================================================
# FIREBASE
# ==========================================================

def initialize_firebase():
    try:
        if not firebase_admin._apps:
            cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

            if not cred_path:
                print("❌ GOOGLE_APPLICATION_CREDENTIALS environment variable not found.")
                return None

            if not os.path.exists(cred_path):
                print(f"❌ Firebase credentials file not found: {cred_path}")
                return None

            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)

            print("✅ Firebase initialized successfully")

        return firestore.client()

    except Exception as e:
        print(f"❌ Firebase initialization error: {e}")
        return None


# ==========================================================
# FIRESTORE COLLECTION
# ==========================================================

def get_results_collection(db):
    return (
        db.collection("NLB_Results_All")
        .document("mahajana-sampatha")
        .collection("mahajana-sampatha")
    )


# ==========================================================
# GET LATEST FIREBASE DRAW
# ==========================================================

def get_latest_firebase_draw(db):
    try:
        collection = get_results_collection(db)

        # Fast path: ordered query (relies on an index AND on every doc
        # having "draw_number_int" set). Firestore's order_by SILENTLY
        # drops any document missing that field, so this can come back
        # empty even when the collection has data.
        docs = list(
            collection
            .order_by("draw_number_int", direction=firestore.Query.DESCENDING)
            .limit(1)
            .stream()
        )

        if docs:
            latest_data = docs[0].to_dict()
            latest_draw = latest_data.get("draw_number_int")

            if latest_draw is None:
                latest_draw = int(latest_data.get("draw_number"))

            print(f"📌 Latest Firebase draw: {latest_draw}")
            return int(latest_draw)

        # Ordered query returned nothing. Check whether the collection is
        # actually empty, or whether it just has docs lacking the field.
        any_doc = list(collection.limit(1).stream())

        if not any_doc:
            print("ℹ️ No previous draws found in Firebase.")
            return None

        # Collection has data but the field-based ordering missed it —
        # fall back to a full scan and compute the max in Python.
        print(
            "⚠️ Ordered query returned no results but the collection "
            "has data (likely missing/null draw_number_int on some "
            "docs). Falling back to a full scan."
        )

        max_draw = None

        for doc in collection.stream():
            data = doc.to_dict()

            draw_num = data.get("draw_number_int")

            if draw_num is None:
                raw = data.get("draw_number")
                try:
                    draw_num = int(raw)
                except (TypeError, ValueError):
                    continue

            if max_draw is None or draw_num > max_draw:
                max_draw = draw_num

        if max_draw is None:
            print("ℹ️ No valid draw numbers found in Firebase.")
            return None

        print(f"📌 Latest Firebase draw (via full scan): {max_draw}")
        return max_draw

    except Exception as e:
        print(f"❌ Error getting latest Firebase draw: {e}")
        return None


# ==========================================================
# CHECK IF DRAW ALREADY EXISTS
# ==========================================================

def draw_exists_in_firebase(db, draw_number: int):
    collection = get_results_collection(db)
    doc_id = f"mahajana-sampatha-{draw_number}"
    doc = collection.document(doc_id).get()
    return doc.exists


# ==========================================================
# UPLOAD ONLY IF NOT EXISTS
# ==========================================================

def upload_new_result(result: Dict, db):
    if not result.get("found"):
        print("⚠️ Result was not found. Skipping Firebase upload.")
        return False

    draw_number = int(result["draw_number"])
    collection = get_results_collection(db)
    doc_id = f"mahajana-sampatha-{draw_number}"
    doc_ref = collection.document(doc_id)

    firestore_data = {
        "lottery": result.get("lottery", "Mahajana Sampatha"),
        "draw_number": str(draw_number),
        # IMPORTANT: Used for sorting latest draw
        "draw_number_int": draw_number,
        "date": result.get("date", ""),
        "date_iso": result.get("date_iso", ""),
        "symbol": result.get("symbol", ""),
        "numbers": result.get("numbers", []),
        "found": True,
        "url": result.get("url", ""),
        "timestamp": firestore.SERVER_TIMESTAMP,
        "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    try:
        # create() will FAIL if document already exists
        # This guarantees no duplicates.
        doc_ref.create(firestore_data)
        print(f"🎉 NEW DRAW UPLOADED: {draw_number}")
        return True

    except AlreadyExists:
        print(f"ℹ️ Draw {draw_number} already exists. No upload required.")
        return False

    except Exception as e:
        print(f"❌ Firebase upload error: {e}")
        return False


# ==========================================================
# CHECK FOR NEW DRAWS
# ==========================================================

def check_for_new_draws(db, latest_draw: int, check_range: int = 5):
    print()
    print("=" * 70)
    print("🔍 CHECKING NLB FOR NEW DRAWS")
    print("=" * 70)

    scraper = MahajanaSampathaScraper(use_selenium=True)
    uploaded = False

    try:
        # Check: latest Firebase draw + 1
        # Example: Firebase latest = 6295
        # Check: 6296, 6297, 6298, etc.
        for draw_number in range(latest_draw + 1, latest_draw + 1 + check_range):
            print()
            print(f"🔎 Checking NLB draw {draw_number}...")

            result = scraper.get_result(str(draw_number))

            if not result.get("found"):
                print(f"⏳ Draw {draw_number} is not available yet.")
                continue

            print("🎯 NEW RESULT FOUND!")
            print(f"Draw: {draw_number}")
            print(f"Symbol: {result.get('symbol')}")
            print(f"Numbers: {' '.join(result.get('numbers', []))}")
            print(f"Date: {result.get('date')}")

            if draw_exists_in_firebase(db, draw_number):
                print("ℹ️ Already exists in Firebase.")
                continue

            success = upload_new_result(result, db)

            if success:
                uploaded = True

            time.sleep(1)

    finally:
        scraper.close()

    return uploaded


# ==========================================================
# MAIN
# ==========================================================

def main():
    print()
    print("=" * 70)
    print("🏆 NLB MAHAJANA SAMPATHA AUTO UPDATER")
    print("=" * 70)

    # ---------------------------------------------
    # INITIALIZE FIREBASE
    # ---------------------------------------------
    print("\n🔌 Connecting to Firebase...")
    db = initialize_firebase()

    if not db:
        print("❌ Firebase connection failed.")
        return

    # ---------------------------------------------
    # GET LATEST FIREBASE DRAW
    # ---------------------------------------------
    latest_draw = get_latest_firebase_draw(db)

    # ---------------------------------------------
    # IMPORTANT: First time only
    # ---------------------------------------------
    if latest_draw is None:
        print("\n⚠️ Firebase has no previous draw.")
        print("Set FIRST_DRAW manually for the first run.")

        FIRST_DRAW = 6295
        latest_draw = FIRST_DRAW - 1

    # ---------------------------------------------
    # CHECK NLB
    # ---------------------------------------------
    uploaded = check_for_new_draws(db=db, latest_draw=latest_draw, check_range=5)

    print()
    print("=" * 70)

    if uploaded:
        print("🎉 NEW DRAW(S) WERE UPLOADED")
    else:
        print("ℹ️ No new draws available.")

    print("=" * 70)


if __name__ == "__main__":
    main()
