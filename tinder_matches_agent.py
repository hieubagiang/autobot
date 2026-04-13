import argparse
import time
from typing import Any

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from tinder_auto_login import create_driver

DEFAULT_URL = "https://tinder.com/app/matches"


def safe_text(element: WebElement) -> str:
    try:
        return (element.text or "").strip()
    except Exception:
        return ""


def wait_for_matches_page(driver, timeout: int = 40) -> None:
    wait = WebDriverWait(driver, timeout)
    wait.until(lambda d: "/app/" in d.current_url.lower())


def get_match_cards(driver) -> list[WebElement]:
    selectors = [
        (By.CSS_SELECTOR, "a[href*='/app/messages/']"),
        (By.CSS_SELECTOR, "a[href*='/app/matches/']"),
        (By.XPATH, "//a[contains(@href,'/app/messages/') or contains(@href,'/app/matches/')]"),
    ]
    cards: list[WebElement] = []
    for by, value in selectors:
        try:
            found = driver.find_elements(by, value)
            if found:
                cards = found
                break
        except Exception:
            pass

    # De-duplicate by href while preserving order.
    deduped: list[WebElement] = []
    seen: set[str] = set()
    for card in cards:
        href = (card.get_attribute("href") or "").strip()
        if not href or href in seen:
            continue
        seen.add(href)
        deduped.append(card)

    return deduped


def extract_match_name(card: WebElement) -> str:
    text = safe_text(card)
    if not text:
        return ""
    first_line = text.splitlines()[0].strip()
    # Remove leading emojis or match counters from card label.
    return first_line.strip(" .:-")


def click_card(driver, card: WebElement) -> bool:
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", card)
        time.sleep(0.2)
        card.click()
        return True
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", card)
            return True
        except Exception:
            return False


def find_message_input(driver, timeout: int = 10) -> Any | None:
    wait = WebDriverWait(driver, timeout)
    candidates = [
        (By.CSS_SELECTOR, "textarea"),
        (By.CSS_SELECTOR, "div[contenteditable='true']"),
        (By.XPATH, "//textarea | //div[@contenteditable='true']"),
    ]
    for by, value in candidates:
        try:
            element = wait.until(EC.presence_of_element_located((by, value)))
            if element.is_displayed():
                return element
        except Exception:
            continue
    return None


def write_draft_message(input_el, message: str, overwrite: bool = False) -> bool:
    try:
        input_el.click()
        if overwrite:
            input_el.send_keys(Keys.CONTROL, "a")
            input_el.send_keys(Keys.DELETE)

        current_text = (input_el.get_attribute("value") or input_el.text or "").strip()
        if current_text and not overwrite:
            return False

        input_el.send_keys(message)
        return True
    except Exception:
        return False


def run_agent(driver, args: argparse.Namespace) -> None:
    print(f"[1] Opening: {args.url}")
    driver.get(args.url)
    wait_for_matches_page(driver)

    print("[2] Waiting for match list...")
    time.sleep(3)

    rounds = max(1, args.rounds)
    visited = 0

    for round_idx in range(1, rounds + 1):
        cards = get_match_cards(driver)
        if not cards:
            print(f"[ROUND {round_idx}] No match cards found.")
            time.sleep(args.delay)
            continue

        if args.limit > 0:
            cards = cards[: args.limit]

        print(f"[ROUND {round_idx}] Found {len(cards)} cards")

        for i, card in enumerate(cards, start=1):
            name = extract_match_name(card) or "em"
            href = (card.get_attribute("href") or "").strip()
            if not click_card(driver, card):
                print(f"[SKIP] #{i} cannot click card: {href}")
                continue

            visited += 1
            print(f"[OPEN] #{i} {name} -> {href}")
            time.sleep(args.delay)

            if args.message:
                msg = args.message.replace("{name}", name)
                input_el = find_message_input(driver, timeout=8)
                if not input_el:
                    print(f"[WRITE] No input found for {name}")
                else:
                    wrote = write_draft_message(input_el, msg, overwrite=args.overwrite)
                    if wrote:
                        print(f"[WRITE] Drafted message for {name}")
                    else:
                        print(f"[WRITE] Skip {name} (input already has text)")

            if args.pause_each > 0:
                time.sleep(args.pause_each)

    print(f"[DONE] visited={visited}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Tinder browser agent: auto next match cards and optionally write draft messages."
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="Target matches URL")
    parser.add_argument("--chrome-path", default="", help="Path to chrome.exe")
    parser.add_argument("--driver-path", default="", help="Path to chromedriver.exe")
    parser.add_argument("--headless", action="store_true", help="Run in headless mode")
    parser.add_argument(
        "--user-data-dir",
        default=".chrome_profiles/tinder",
        help="Chrome user data dir for persistent Tinder session",
    )
    parser.add_argument(
        "--profile-directory",
        default="Default",
        help="Chrome profile directory inside user data dir",
    )
    parser.add_argument("--limit", type=int, default=20, help="Max cards per round (0 = all)")
    parser.add_argument("--rounds", type=int, default=1, help="How many passes over the list")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay (seconds) after opening each card")
    parser.add_argument("--pause-each", type=float, default=0.0, help="Extra pause after each card")
    parser.add_argument(
        "--message",
        default="",
        help="Optional draft message template, supports {name}, and does not auto-send",
    )
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing input text")
    parser.add_argument("--auto-end", action="store_true", help="Close browser when finished")
    args = parser.parse_args()

    driver = create_driver(
        chrome_path=args.chrome_path,
        headless=args.headless,
        driver_path=(args.driver_path or None),
        user_data_dir=(args.user_data_dir or None),
        profile_directory=args.profile_directory,
    )

    try:
        run_agent(driver, args)
        if not args.auto_end:
            print("[INFO] Browser stays open. Press Ctrl+C to exit.")
            while True:
                time.sleep(1)
    finally:
        if args.auto_end:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    main()
