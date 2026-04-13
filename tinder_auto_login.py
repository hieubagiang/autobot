import argparse
import os
import re
import subprocess
import time

import undetected_chromedriver as uc
from selenium.common.exceptions import SessionNotCreatedException
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

DEFAULT_URL = "https://tinder.com/"
DEFAULT_CHROME_PATH = ""
DEFAULT_USER_DATA_DIR = os.path.join(os.path.dirname(__file__), ".chrome_profiles", "tinder")
DEFAULT_PROFILE_DIRECTORY = "Default"


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


def build_chrome_options(
    chrome_binary: str | None,
    headless: bool,
    user_data_dir: str | None,
    profile_directory: str,
) -> uc.ChromeOptions:
    options = uc.ChromeOptions()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,900")

    if user_data_dir:
        options.add_argument(f"--user-data-dir={user_data_dir}")
        options.add_argument(f"--profile-directory={profile_directory}")

    if headless:
        options.add_argument("--headless=new")

    if chrome_binary:
        options.binary_location = chrome_binary

    return options


def create_driver(
    chrome_path: str | None = None,
    headless: bool = False,
    driver_path: str | None = None,
    user_data_dir: str | None = DEFAULT_USER_DATA_DIR,
    profile_directory: str = DEFAULT_PROFILE_DIRECTORY,
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

    if user_data_dir:
        os.makedirs(user_data_dir, exist_ok=True)
        print(f"[DRIVER] Persisting Chrome session at: {user_data_dir}")
        print(f"[DRIVER] Using Chrome profile directory: {profile_directory}")

    def create_uc_instance(version_main: int | None) -> uc.Chrome:
        options = build_chrome_options(
            chrome_binary,
            headless,
            user_data_dir=user_data_dir,
            profile_directory=profile_directory,
        )
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


def click_first(driver: uc.Chrome, selectors: list[tuple[str, str]], timeout: int = 12) -> bool:
    wait = WebDriverWait(driver, timeout)
    for by, value in selectors:
        try:
            wait.until(EC.element_to_be_clickable((by, value))).click()
            return True
        except Exception:
            continue
    return False


def click_google_button_with_iframe_fallback(driver: uc.Chrome) -> bool:
    """Click Google login button in current DOM or inside iframes."""
    selectors = [
        (By.XPATH, "//button[contains(., 'Google') or contains(., 'Đăng nhập bằng Google')]"),
        (By.XPATH, "//div[@role='button'][contains(., 'Google')]"),
        (By.XPATH, "//span[contains(., 'Google')]/ancestor::button[1]"),
        (By.XPATH, "//span[contains(., 'Google')]/ancestor::div[@role='button'][1]"),
        (By.XPATH, "//*[contains(@aria-label, 'Google') and (self::button or self::div)]"),
        (By.XPATH, "//*[@role='button' and contains(., 'Tiếp tục sử dụng dịch vụ bằng Google')]"),
        (By.XPATH, "//*[@id='button-label' and contains(., 'Google')]/ancestor::*[@role='button'][1]"),
    ]

    if click_first(driver, selectors, timeout=3):
        return True

    frames = driver.find_elements(By.TAG_NAME, "iframe")
    for index, frame in enumerate(frames):
        try:
            driver.switch_to.frame(frame)
            if click_first(driver, selectors, timeout=2):
                print(f"[3] Clicked Google in iframe #{index}")
                driver.switch_to.default_content()
                return True
            driver.switch_to.default_content()
        except Exception:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass

    return False


def preview_clickable_texts(driver: uc.Chrome, limit: int = 20) -> list[str]:
    """Return a short snapshot of visible button-like texts for debugging selector drift."""
    texts: list[str] = []
    try:
        elements = driver.find_elements(By.XPATH, "//button|//a|//div[@role='button']")
        for element in elements:
            text = (element.text or "").strip()
            if not text:
                continue
            if text in texts:
                continue
            texts.append(text)
            if len(texts) >= limit:
                break
    except Exception:
        pass
    return texts


def switch_to_new_window(driver: uc.Chrome, before_handles: set[str], timeout: int = 15) -> bool:
    end_time = time.time() + timeout
    while time.time() < end_time:
        now_handles = set(driver.window_handles)
        new_handles = now_handles - before_handles
        if new_handles:
            driver.switch_to.window(new_handles.pop())
            return True
        time.sleep(0.3)
    return False


def wait_for_login_success(driver: uc.Chrome, timeout: int = 300) -> bool:
    deadline = time.time() + timeout
    checks = [
        (By.XPATH, "//a[contains(@href, '/app/profile') or contains(@href, '/app/settings')]"),
        (By.XPATH, "//*[contains(text(),'Matches') or contains(text(),'Tin nhắn') or contains(text(),'Messages')]"),
        (By.CSS_SELECTOR, "main[role='main']"),
    ]

    while time.time() < deadline:
        current = driver.current_url.lower()
        if "/app/" in current:
            return True

        for by, value in checks:
            try:
                driver.find_element(by, value)
                return True
            except Exception:
                pass

        time.sleep(1)

    return False


def login_tinder_with_google_and_sms(driver: uc.Chrome, url: str):
    wait = WebDriverWait(driver, 20)

    print(f"[1] Opening: {url}")
    driver.get(url)

    # Cookie/banner dialogs may block click actions.
    click_first(
        driver,
        [
            (By.XPATH, "//button[contains(., 'I accept') or contains(., 'Accept') or contains(., 'Đồng ý')]"),
            (By.XPATH, "//button[contains(., 'Only allow essential cookies') or contains(., 'Essential')]"),
        ],
        timeout=4,
    )

    print("[2] Clicking Tinder login entry...")
    opened_login = click_first(
        driver,
        [
            (By.XPATH, "//a[contains(., 'Log in') or contains(., 'Đăng nhập')]"),
            (By.XPATH, "//button[contains(., 'Log in') or contains(., 'Đăng nhập')]"),
            (By.CSS_SELECTOR, "a[href*='login'], button[aria-label*='Log in']"),
        ],
    )
    if not opened_login:
        raise RuntimeError("Cannot find Tinder 'Log in' button.")

    print("[3] Choosing 'Sign in with Google'...")
    before = set(driver.window_handles)

    # Tinder may hide Google behind a "More options" step.
    click_first(
        driver,
        [
            (By.XPATH, "//button[contains(., 'More options') or contains(., 'More login options') or contains(., 'Tùy chọn khác')]"),
            (By.XPATH, "//div[@role='button'][contains(., 'More options') or contains(., 'Tùy chọn khác')]"),
        ],
        timeout=4,
    )

    picked_google = click_google_button_with_iframe_fallback(driver)
    if not picked_google:
        print("[DEBUG] Visible login texts:", preview_clickable_texts(driver))
        raise RuntimeError("Cannot find 'Sign in with Google' option.")

    switched = switch_to_new_window(driver, before_handles=before, timeout=15)
    if switched:
        print("[4] Google popup detected. Please complete Google login in this popup.")
        input("[WAIT] After finishing Google account selection/password, press Enter here...")

        # If popup still alive, give user a bit more time for risk checks.
        time.sleep(1.5)
        try:
            if len(driver.window_handles) > 1:
                driver.close()
                driver.switch_to.window(driver.window_handles[0])
        except Exception:
            # If popup already closed by redirect flow, ignore.
            try:
                driver.switch_to.window(driver.window_handles[0])
            except Exception:
                pass
    else:
        print("[4] No popup found; Google flow may be embedded in current tab.")

    print("[5] Waiting for Tinder phone verification (SMS OTP) step...")
    print("    - If Tinder asks phone number/OTP, complete it in browser.")
    input("[WAIT] After entering SMS code and finishing verification, press Enter here...")

    print("[6] Verifying logged-in state...")
    try:
        wait.until(lambda d: "/app/" in d.current_url.lower())
        print(f"[OK] Login success, current URL: {driver.current_url}")
        return
    except TimeoutException:
        pass

    if wait_for_login_success(driver, timeout=20):
        print(f"[OK] Login likely successful, current URL: {driver.current_url}")
        return

    raise RuntimeError(
        "Could not verify Tinder login success. Please confirm manually in browser and rerun if needed."
    )


def main():
    parser = argparse.ArgumentParser(
        description="Launch Tinder and assist login via Google + phone SMS (manual OTP input)."
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="Target URL")
    parser.add_argument("--chrome-path", default=DEFAULT_CHROME_PATH, help="Path to chrome.exe")
    parser.add_argument("--driver-path", default="", help="Path to chromedriver.exe")
    parser.add_argument("--headless", action="store_true", help="Run in headless mode")
    parser.add_argument(
        "--user-data-dir",
        default=DEFAULT_USER_DATA_DIR,
        help="Chrome user data dir for persistent login session",
    )
    parser.add_argument(
        "--profile-directory",
        default=DEFAULT_PROFILE_DIRECTORY,
        help="Chrome profile directory name inside user data dir (Default, Profile 1, ...)",
    )
    parser.add_argument(
        "--auto-end",
        action="store_true",
        help="Close browser automatically after script finishes",
    )
    args = parser.parse_args()

    driver = create_driver(
        chrome_path=args.chrome_path,
        headless=args.headless,
        driver_path=(args.driver_path or None),
        user_data_dir=(args.user_data_dir or None),
        profile_directory=args.profile_directory,
    )

    try:
        login_tinder_with_google_and_sms(driver, args.url)
        if not args.auto_end:
            print("[INFO] Browser stays open by default. Press Ctrl+C to exit.")
            while True:
                time.sleep(1)
    finally:
        if args.auto_end:
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
