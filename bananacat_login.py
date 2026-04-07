import argparse
import os
import re
import subprocess
import time

import undetected_chromedriver as uc
import requests
from selenium.common.exceptions import SessionNotCreatedException
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


DEFAULT_URL = "https://bananacat.store/staff/orders/boostings"
DEFAULT_LOGIN_URL = "https://bananacat.store/login"
DEFAULT_USERNAME = "Thanhdat2k5"
DEFAULT_PASSWORD = "123123321"
DEFAULT_CHROME_PATH = ""
CLAIM_ENDPOINT = "https://bananacat.store/staff/orders/boostings/claim"


def detect_chrome_major_version(chrome_path: str) -> int | None:
    """Return Chrome major version from `<chrome_path> --version`, or None if unavailable."""
    try:
        result = subprocess.run(
            [chrome_path, "--version"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except Exception:
        return None

    version_text = (result.stdout or result.stderr or "").strip()
    match = re.search(r"(\d+)\.\d+\.\d+\.\d+", version_text)
    if not match:
        return None

    return int(match.group(1))


def build_chrome_options(chrome_binary: str | None, headless: bool) -> uc.ChromeOptions:
    options = uc.ChromeOptions()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,800")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--ignore-ssl-errors")

    if headless:
        options.add_argument("--headless=new")

    if chrome_binary:
        options.binary_location = chrome_binary

    return options


def create_driver(
    chrome_path: str | None = None,
    headless: bool = False,
    driver_path: str | None = None,
) -> uc.Chrome:

    chrome_major = None
    chrome_binary = chrome_path if chrome_path and os.path.exists(chrome_path) else None
    if chrome_binary:
        chrome_major = detect_chrome_major_version(chrome_binary)
        if chrome_major is None:
            print("[DRIVER] Cannot read version from provided chrome path, fallback to system Chrome.")
            chrome_binary = None
    elif chrome_path:
        print(f"[DRIVER] Chrome path not found, fallback to system Chrome: {chrome_path}")

    if driver_path and not os.path.exists(driver_path):
        print(f"[DRIVER] Chromedriver path not found, ignore: {driver_path}")
        driver_path = None

    def create_uc_instance(version_main: int | None) -> uc.Chrome:
        options = build_chrome_options(chrome_binary, headless)
        uc_kwargs = {"options": options}
        if chrome_binary:
            uc_kwargs["browser_executable_path"] = chrome_binary
        if driver_path:
            uc_kwargs["driver_executable_path"] = driver_path
            print(f"[DRIVER] Using provided chromedriver: {driver_path}")
        if version_main is not None:
            uc_kwargs["version_main"] = version_main
            print(f"[DRIVER] Using Chrome major version: {version_main}")
        return uc.Chrome(**uc_kwargs)

    if chrome_binary:
        print(f"[DRIVER] Detected Chrome major version: {chrome_major}")
    else:
        print("[DRIVER] Could not detect Chrome version, using UC auto mode.")

    try:
        return create_uc_instance(chrome_major)
    except SessionNotCreatedException as e:
        err = str(e)
        match = re.search(r"Current browser version is\s+(\d+)\.", err)
        if not match:
            raise

        retry_major = int(match.group(1))
        print(f"[DRIVER] Retry with browser major from error: {retry_major}")
        return create_uc_instance(retry_major)


def bypass_privacy_error_if_needed(driver: uc.Chrome):
    """Handle Chrome TLS warning page (if certificate is invalid)."""
    if "chrome-error://chromewebdata" not in driver.current_url:
        return

    wait = WebDriverWait(driver, 5)
    print("[TLS] Privacy error detected, trying to proceed...")

    try:
        wait.until(EC.element_to_be_clickable((By.ID, "details-button"))).click()
        wait.until(EC.element_to_be_clickable((By.ID, "proceed-link"))).click()
        print("[TLS] Bypassed privacy warning.")
    except Exception as e:
        raise RuntimeError(f"Cannot bypass TLS warning: {e}") from e


def login_bananacat(driver: uc.Chrome, username: str, password: str, target_url: str):
    wait = WebDriverWait(driver, 20)

    print(f"[1] Opening: {target_url}")
    try:
        driver.get(target_url)
    except WebDriverException as e:
        if "ERR_CONNECTION_CLOSED" not in str(e):
            raise
        print(f"[1] Connection closed on target URL, fallback to login page: {DEFAULT_LOGIN_URL}")
        driver.get(DEFAULT_LOGIN_URL)

    bypass_privacy_error_if_needed(driver)

    print("[2] Waiting for login form...")
    username_input = wait.until(EC.presence_of_element_located((By.ID, "username")))
    password_input = wait.until(EC.presence_of_element_located((By.ID, "password")))

    print("[3] Filling credentials...")
    username_input.clear()
    username_input.send_keys(username)
    password_input.clear()
    password_input.send_keys(password)

    print("[3.5] Enabling 'remember account'...")
    driver.execute_script(
        """
        const cb = document.querySelector('#remember_me, input[name="remember"]');
        if (cb) {
            cb.checked = true;
            cb.dispatchEvent(new Event('change', { bubbles: true }));
        }
        return !!cb;
        """
    )

    print("[4] Submitting login...")
    wait.until(
        EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "button.btn.btn-dark.block.w-full.text-center.g-recaptcha")
        )
    ).click()

    print("[5] Verifying login result...")
    wait.until(EC.any_of(EC.url_contains("/staff/orders/boostings"), EC.url_contains("/staff")))

    if "/staff/orders/boostings" not in driver.current_url:
        driver.get(target_url)
        wait.until(EC.url_contains("/staff/orders/boostings"))

    time.sleep(1)

    if "/staff/orders/boostings" in driver.current_url:
        print("[OK] Login success:", driver.current_url)
        return

    raise RuntimeError(f"Login may have failed. Current URL: {driver.current_url}")


def get_csrf_token(driver: uc.Chrome) -> str:
    token = driver.execute_script(
        """
        return (
          (window.webData && window.webData.csrfToken) ||
          document.querySelector('meta[name="csrf-token"]')?.content ||
          document.querySelector('input[name="_token"]')?.value ||
          ''
        );
        """
    )
    return (token or "").strip()


def build_authenticated_session(driver: uc.Chrome) -> requests.Session:
    session = requests.Session()
    for cookie in driver.get_cookies():
        session.cookies.set(
            cookie["name"],
            cookie["value"],
            domain=(cookie.get("domain") or None),
            path=(cookie.get("path") or "/"),
        )

    csrf = get_csrf_token(driver)
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }
    if csrf:
        headers["X-CSRF-TOKEN"] = csrf

    session.headers.update(headers)
    return session


def extract_claim_ids(html: str) -> list[int]:
    ids: set[int] = set()

    for pattern in (
        r"claimOrder\((\d+)\)",
        r"onclick=[\'\"]claimOrder\((\d+)\)[\'\"]",
        r"data-order-id=[\'\"](\d+)[\'\"]",
        r"data-id=[\'\"](\d+)[\'\"]",
    ):
        for match in re.finditer(pattern, html, flags=re.I):
            try:
                ids.add(int(match.group(1)))
            except Exception:
                pass

    return sorted(ids)


def claim_order(session: requests.Session, order_id: int) -> dict:
    response = session.post(CLAIM_ENDPOINT, json={"id": order_id}, timeout=30)
    response.raise_for_status()
    try:
        return response.json()
    except Exception:
        return {"status": response.status_code, "message": response.text[:500]}


def scan_claimable_orders(driver: uc.Chrome, base_url: str, max_pages: int) -> list[int]:
    found: list[int] = []
    for page in range(1, max_pages + 1):
        url = base_url if page == 1 else f"{base_url}?claimed={page}"
        print(f"[SCAN] Loading page {page}: {url}")
        driver.get(url)
        time.sleep(1.5)
        ids = extract_claim_ids(driver.page_source)
        if ids:
            print(f"[SCAN] Found claimable ids on page {page}: {ids}")
            found.extend(ids)
        else:
            print(f"[SCAN] No claim ids found on page {page}")
    # de-duplicate while preserving order
    return list(dict.fromkeys(found))


def auto_claim_orders(driver: uc.Chrome, base_url: str, max_pages: int, dry_run: bool = False):
    session = build_authenticated_session(driver)
    order_ids = scan_claimable_orders(driver, base_url, max_pages)

    if not order_ids:
        print("[CLAIM] No claimable orders found in scanned pages.")
        return []

    results = []
    for order_id in order_ids:
        if dry_run:
            print(f"[CLAIM] DRY RUN => would claim order {order_id}")
            results.append({"id": order_id, "status": "DRY_RUN"})
            continue

        print(f"[CLAIM] Claiming order {order_id} ...")
        try:
            result = claim_order(session, order_id)
            results.append({"id": order_id, "status": "OK", "result": result})
            print(f"[CLAIM] ✓ Order {order_id}: {result}")
        except Exception as e:
            results.append({"id": order_id, "status": "FAIL", "error": str(e)})
            print(f"[CLAIM] ✗ Order {order_id}: {e}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Login to bananacat.store staff boosts page")
    parser.add_argument("--url", default=DEFAULT_URL, help="Target URL")
    parser.add_argument("--username", default=DEFAULT_USERNAME, help="Username")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Password")
    parser.add_argument("--chrome-path", default=DEFAULT_CHROME_PATH, help="Path to chrome.exe")
    parser.add_argument("--driver-path", default="", help="Path to chromedriver.exe")
    parser.add_argument("--headless", action="store_true", help="Run in headless mode")
    parser.add_argument("--auto-claim", action="store_true", help="Scan pages and claim every claimable order found")
    parser.add_argument("--scan-pages", type=int, default=3, help="Number of pages to scan for claimable orders when --auto-claim is enabled")
    parser.add_argument("--dry-run", action="store_true", help="Only print claimable order ids without sending claim requests")
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="Keep browser open after success for manual checks",
    )
    args = parser.parse_args()

    driver = create_driver(
        chrome_path=args.chrome_path,
        headless=args.headless,
        driver_path=(args.driver_path or None),
    )
    try:
        login_bananacat(driver, args.username, args.password, args.url)
        if args.auto_claim:
            print(f"[AUTO] Scanning and claiming orders (pages={args.scan_pages}) ...")
            auto_claim_orders(driver, args.url, args.scan_pages, dry_run=args.dry_run)
        if args.keep_open:
            print("[INFO] Browser is kept open. Press Ctrl+C to exit.")
            while True:
                time.sleep(1)
    finally:
        if not args.keep_open:
            try:
                driver.quit()
            except Exception:
                pass
            # UC may call quit again in __del__; make it a no-op to avoid WinError 6 noise.
            try:
                driver.quit = lambda *_, **__: None
            except Exception:
                pass


if __name__ == "__main__":
    main()
