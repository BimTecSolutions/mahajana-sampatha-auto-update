import requests
from bs4 import BeautifulSoup
import re
import time
import json
import argparse
import sys
from typing import Dict, Optional, List
import firebase_admin
from firebase_admin import credentials, firestore
import os

COLLECTION_NAME = "NLB_Results_All"
SUBCOLLECTION_NAME = "mahajana-sampatha"


class MahajanaSampathaScraper:
    """
    Specialized scraper for Mahajana Sampatha lottery results.
    """
    BASE_URL = "https://www.nlb.lk"
    LOTTERY_SLUG = "mahajana-sampatha"
    LOTTERY_NAME = "Mahajana Sampatha"
    RESULTS_URL = f"{BASE_URL}/results/{LOTTERY_SLUG}"

    def __init__(self, use_selenium: bool = True):
        self.use_selenium = use_selenium
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._driver = None

    def _get_driver(self):
        """Initialize and return Selenium driver if needed."""
        if self._driver is None and self.use_selenium:
            try:
                from selenium import webdriver
                from selenium.webdriver.chrome.options import Options
                options = Options()
                options.add_argument('--headless=new')
                options.add_argument('--no-sandbox')
                options.add_argument('--disable-dev-shm-usage')
                options.add_argument('--disable-blink-features=AutomationControlled')
                options.add_experimental_option("excludeSwitches", ["enable-automation"])
                options.add_experimental_option('useAutomationExtension', False)
                self._driver = webdriver.Chrome(options=options)
            except Exception as e:
                print(f"⚠️ Selenium init error: {e}")
                self.use_selenium = False
        return self._driver

    def fetch_page(self, url: str) -> Optional[str]:
        """Fetch page content using Selenium or requests."""
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
            resp = self.session.get(url, timeout=30)
            if resp.status_code == 200 and len(resp.text) > 1000:
                return resp.text
        except Exception as e:
            print(f"⚠️ Request error: {e}")
        return None

    def extract_winning_numbers(self, text: str, draw_number: str) -> Dict:
        """
        Extract winning numbers for a specific draw number.
        The format is: "W 3 7 4 5 3 7" (space-separated)
        """
        result = {
            "symbol": None,
            "numbers": [],
            "found": False
        }

        draw_pattern = rf'(?:Draw|Lottery)\s*(?:No|Number)?\.?\s*:?\s*{draw_number}'
        draw_match = re.search(draw_pattern, text, re.I)

        if not draw_match:
            return result

        start = draw_match.end()
        section = text[start:start + 300]

        pattern = r'\b([A-Z])\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\b'
        matches = re.findall(pattern, section, re.I)

        if matches:
            for match in matches:
                symbol = match[0]
                numbers = list(match[1:7])
                numbers = [n for n in numbers if 0 <= int(n) <= 99]
                if len(numbers) == 6:
                    result["symbol"] = symbol
                    result["numbers"] = [f"{int(n):02d}" for n in numbers]
                    result["found"] = True
                    return result

        symbol_pattern = r'([A-Z])\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)(?:\s+(\d+))?'
        matches = re.findall(symbol_pattern, section, re.I)

        if matches:
            for match in matches:
                symbol = match[0]
                if symbol in ['D', 'N', 'F', 'S', 'T']:
                    symbol_pos = section.find(symbol)
                    if symbol_pos > 0:
                        before = section[symbol_pos-3:symbol_pos]
                        if before and before.strip() in ['Dat', 'Dra', 'Num', 'Pri']:
                            continue

                numbers = list(match[1:])
                numbers = [n for n in numbers if 0 <= int(n) <= 99]
                if numbers and len(numbers) >= 5:
                    result["symbol"] = symbol
                    result["numbers"] = [f"{int(n):02d}" for n in numbers[:6]]
                    result["found"] = True
                    return result

        draw_pos = draw_match.start()
        next_draw = re.search(r'(?:Draw|Lottery)\s*(?:No|Number)?\.?\s*:?\s*\d+', text[draw_pos+1:], re.I)
        if next_draw:
            section = text[draw_pos:draw_pos + 1 + next_draw.start()]
        else:
            section = text[draw_pos:draw_pos + 500]

        words_to_skip = ['DATE', 'DRAW', 'NO', 'NUMBER', 'PRIZE', 'WINNER', 'TOTAL', 'LETTER']
        for match in re.finditer(r'\b([A-Z])\s+', section, re.I):
            symbol = match.group(1)
            if symbol in ['D', 'N', 'P', 'T', 'F', 'S']:
                start_pos = match.start()
                if start_pos > 0:
                    prev_char = section[start_pos-1] if start_pos > 0 else ''
                    if prev_char.isalpha():
                        continue
                    word_start = section.rfind(' ', 0, start_pos) + 1
                    word = section[word_start:start_pos+1].upper()
                    if any(w in word for w in words_to_skip):
                        continue

            after_letter = section[match.end():match.end()+100]
            numbers = re.findall(r'\b(\d+)\b', after_letter)
            numbers = [n for n in numbers if 0 <= int(n) <= 99]
            if numbers and len(numbers) >= 5:
                result["symbol"] = symbol
                result["numbers"] = [f"{int(n):02d}" for n in numbers[:6]]
                result["found"] = True
                return result

        return result

    def extract_draw_date(self, text: str) -> Optional[str]:
        """Extract the draw date from the page."""
        date_patterns = [
            r'(?:Date|Draw Date)\s*:?\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4})',
            r'([A-Za-z]+\s+\d{1,2},?\s+\d{4})',
        ]

        for pattern in date_patterns:
            match = re.search(pattern, text, re.I)
            if match:
                return match.group(1)
        return None

    def get_result(self, draw_number: str) -> Dict:
        """Fetch and parse results for a specific draw number."""
        url = f"{self.BASE_URL}/results/{self.LOTTERY_SLUG}/{draw_number}"

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

        soup = BeautifulSoup(html, 'html.parser')
        all_text = soup.get_text(separator=" ", strip=True)

        result["date"] = self.extract_draw_date(all_text)

        extracted = self.extract_winning_numbers(all_text, draw_number)

        if extracted["found"]:
            result["symbol"] = extracted["symbol"]
            result["numbers"] = extracted["numbers"]
            result["found"] = True

        return result

    def get_latest_draw_number(self) -> Optional[str]:
        """
        Fetch NLB's results landing page for this lottery (no draw number
        in the URL). NLB shows the most recent draw there, so we pull the
        draw number out of that page's text.

        NOTE: NLB's markup changes from time to time. If this stops
        finding a draw number, open RESULTS_URL in a browser, view
        source, and adjust `patterns` below to match the current label
        text around the draw number.
        """
        html = self.fetch_page(self.RESULTS_URL)
        if not html:
            return None

        soup = BeautifulSoup(html, 'html.parser')
        text = soup.get_text(separator=" ", strip=True)

        patterns = [
            r'Draw\s*No\.?\s*:?\s*(\d{3,6})',
            r'Draw\s*Number\s*:?\s*(\d{3,6})',
            r'Lottery\s*No\.?\s*:?\s*(\d{3,6})',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                return match.group(1)
        return None

    def close(self):
        """Clean up resources."""
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None


def initialize_firebase():
    """Initialize Firebase with service account credentials."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        cred_path = os.path.join(script_dir, "serviceAccountKey.json")

        if not os.path.exists(cred_path):
            print(f"❌ Firebase credentials not found at: {cred_path}")
            print("   Please place 'serviceAccountKey.json' in the project root directory.")
            return None

        if not firebase_admin._apps:
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            print("✅ Firebase initialized successfully")

        return firestore.client()

    except Exception as e:
        print(f"❌ Firebase initialization error: {e}")
        return None


def _results_collection(db):
    return (
        db.collection(COLLECTION_NAME)
        .document(SUBCOLLECTION_NAME)
        .collection(SUBCOLLECTION_NAME)
    )


def get_latest_uploaded_draw(db) -> Optional[int]:
    """
    Return the draw_number of the most recently uploaded document
    (ordered by upload timestamp, not by draw_number value — this avoids
    issues if older documents stored draw_number as a different type).
    """
    try:
        docs = (
            _results_collection(db)
            .order_by("timestamp", direction=firestore.Query.DESCENDING)
            .limit(1)
            .stream()
        )
        for doc in docs:
            data = doc.to_dict()
            draw_number = data.get("draw_number")
            if draw_number is not None:
                return int(draw_number)
        return None
    except Exception as e:
        print(f"⚠️ Could not read latest draw from Firestore: {e}")
        return None


def draw_exists_in_firestore(db, draw_number: str) -> bool:
    """Direct existence check on the doc id, as a second safety net."""
    doc_id = f"mahajana-sampatha-{draw_number}"
    doc_ref = _results_collection(db).document(doc_id)
    return doc_ref.get().exists


def upload_to_firestore(results: List[Dict], db) -> bool:
    """Upload results to Firestore collection."""
    if not db:
        print("❌ Firebase not initialized. Cannot upload data.")
        return False

    try:
        print(f"\n📤 Uploading {len(results)} result(s) to Firebase...")
        print(f"   Collection: {COLLECTION_NAME}/{SUBCOLLECTION_NAME}")

        uploaded_count = 0

        for result in results:
            draw_number = result.get("draw_number", "unknown")
            doc_id = f"mahajana-sampatha-{draw_number}"

            firestore_data = {
                "lottery": result.get("lottery", "Mahajana Sampatha"),
                "draw_number": result.get("draw_number"),
                "date": result.get("date", ""),
                "symbol": result.get("symbol", ""),
                "numbers": result.get("numbers", []),
                "found": result.get("found", False),
                "url": result.get("url", ""),
                "timestamp": firestore.SERVER_TIMESTAMP,
                "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }

            if result.get("error"):
                firestore_data["error"] = result.get("error")

            doc_ref = _results_collection(db).document(doc_id)
            doc_ref.set(firestore_data, merge=True)

            uploaded_count += 1
            print(f"   ✅ Uploaded: {doc_id}")

        print(f"\n✅ Successfully uploaded {uploaded_count} document(s) to Firestore!")
        return True

    except Exception as e:
        print(f"❌ Firestore upload error: {e}")
        return False


def fetch_draw_range(start_draw: int, end_draw: int, delay: float = 0.5) -> List[Dict]:
    """Fetch results for a range of draw numbers (kept for manual backfills)."""
    scraper = MahajanaSampathaScraper(use_selenium=True)
    results = []

    try:
        print("\n" + "="*70)
        print(f"🔍 FETCHING MAHAJANA SAMPATHA DRAWS {start_draw} TO {end_draw}".center(70))
        print("="*70)
        print(f"\nTotal draws to fetch: {end_draw - start_draw + 1}\n")

        for draw_num in range(start_draw, end_draw + 1):
            draw_str = str(draw_num)
            print(f"  Fetching draw {draw_str}...", end=" ", flush=True)

            result = scraper.get_result(draw_str)

            if result.get("found"):
                symbol = result.get("symbol", "")
                numbers = ", ".join(result.get("numbers", []))
                date = result.get("date", "Unknown")
                print(f"✅ {symbol} {numbers} ({date})")
            else:
                error = result.get("error", "Not found")
                print(f"❌ {error}")

            results.append(result)

            if draw_num < end_draw:
                time.sleep(delay)

    finally:
        scraper.close()

    return results


def run_backfill(start_draw: int, end_draw: int):
    db = initialize_firebase()
    results = fetch_draw_range(start_draw, end_draw)
    if results and db:
        upload_to_firestore(results, db)


def run_daily_check():
    """
    Core logic for the scheduled job:
    1. Find the latest draw number published on the NLB site.
    2. Find the latest draw number already stored in Firestore.
    3. If the site has a newer draw, scrape and upload just that one.
    4. Otherwise, do nothing (no duplicate uploads).
    """
    print("\n" + "="*70)
    print(f"🔍 Checking latest {MahajanaSampathaScraper.LOTTERY_NAME} draw — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)

    db = initialize_firebase()
    if not db:
        print("❌ Firebase not initialized. Aborting run.")
        sys.exit(1)

    scraper = MahajanaSampathaScraper(use_selenium=True)
    try:
        latest_site_draw = scraper.get_latest_draw_number()
        if not latest_site_draw:
            print("❌ Could not determine the latest draw number from the NLB site.")
            print("   The site's HTML may have changed — see the note in get_latest_draw_number().")
            sys.exit(1)

        print(f"🌐 Latest draw on site:      {latest_site_draw}")

        latest_uploaded = get_latest_uploaded_draw(db)
        print(f"🗄️  Latest draw in Firestore: {latest_uploaded}")

        if latest_uploaded is not None and int(latest_site_draw) <= latest_uploaded:
            print("✅ Firestore already has the latest draw. Nothing to do.")
            return

        if draw_exists_in_firestore(db, latest_site_draw):
            print(f"✅ Draw {latest_site_draw} already exists in Firestore. Skipping.")
            return

        print(f"🆕 New draw detected: {latest_site_draw}. Fetching full result...")
        result = scraper.get_result(latest_site_draw)

        if not result.get("found"):
            print(f"⚠️ Found draw {latest_site_draw} but could not parse its numbers "
                  f"({result.get('error', 'parse failed')}). Not uploading a partial result.")
            sys.exit(1)

        print(f"✅ Parsed: {result['symbol']} {', '.join(result['numbers'])} ({result['date']})")

        if not upload_to_firestore([result], db):
            sys.exit(1)

    finally:
        scraper.close()

    print("\n" + "="*70)
    print("✅ COMPLETE".center(70))
    print("="*70)


def main():
    parser = argparse.ArgumentParser(description="Mahajana Sampatha result fetcher")
    parser.add_argument("--backfill", nargs=2, type=int, metavar=("START", "END"),
                         help="One-off: fetch and upload a range of past draw numbers")
    args = parser.parse_args()

    if args.backfill:
        run_backfill(args.backfill[0], args.backfill[1])
    else:
        run_daily_check()


if __name__ == "__main__":
    main()
