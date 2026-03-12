import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import threading
import time

# ===================== CONFIG =====================
CHROME_PATH = r"D:\Data\Downloads\Compressed\chrome-win64\chrome.exe"
MAX_WORKERS = 5  # Số Chrome chạy song song cùng lúc
# ==================================================

# Lock để serialize việc khởi tạo driver (tránh xung đột patch chromedriver.exe)
_driver_lock = threading.Lock()


DONE_PREFIX = "DONE|"


def load_accounts(path: str) -> list[dict]:
    """Dọc file accounts, bỏ qua dòng đã được đánh dấu DONE."""
    accounts = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(DONE_PREFIX):
                continue
            if "|" in line:
                email, password = line.split("|", 1)
                accounts.append({"email": email.strip(), "password": password.strip()})
    return accounts


def mark_done(path: str, email: str, lock: threading.Lock):
    """Thêm prefix DONE| vào dòng chứa email trong file — thread-safe."""
    with lock:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith(DONE_PREFIX) and stripped.startswith(email + "|"):
                    f.write(DONE_PREFIX + stripped + "\n")
                else:
                    f.write(line)


def create_driver():
    """Khởi tạo undetected ChromeDriver (serialize để tránh race condition khi patch)."""
    with _driver_lock:
        options = uc.ChromeOptions()
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1280,800")
        options.add_argument("--disable-background-timer-throttling")
        options.add_argument("--disable-backgrounding-occluded-windows")
        options.add_argument("--disable-renderer-backgrounding")
        options.add_argument("--disable-features=CalculateNativeWinOcclusion")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.binary_location = CHROME_PATH
        driver = uc.Chrome(options=options, browser_executable_path=CHROME_PATH)
        time.sleep(1)  # Cho phép chromedriver.exe được giải phóng trước khi thread kế tiếp tạo
    return driver


def login_google_on_itviec(driver: uc.Chrome, email: str, password: str):
    wait = WebDriverWait(driver, 20)

    # 1. Mở trang chủ itviec
    print("[1] Đang mở itviec.com ...")
    driver.get("https://itviec.com/story-hub/that-nghiep-cung-dang-so-nhung-dang-so-hon-la-khong-biet-minh-dang-o-dau-va-chap-nhan-minh-that-nghiep-2917?utm_id=97758_v0")

    # 2. Click nút Sign In
    print("[2] Đang click Sign In ...")
    wait.until(
        EC.element_to_be_clickable((By.LINK_TEXT, "Sign in/Sign up"))
    ).click()

    # 3. Click Login with Google
    print("[3] Đang click Login with Google ...")
    wait.until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, ".ps-2"))
    ).click()

    # 4. Chuyển sang tab Google login (nếu mở tab mới)
    print("[4] Chuyển sang tab Google login ...")
    time.sleep(2)
    driver.switch_to.window(driver.window_handles[-1])

    # 5. Nhập email Google
    print("[5] Đang nhập email ...")
    email_input = wait.until(
        EC.presence_of_element_located((By.ID, "identifierId"))
    )
    email_input.clear()
    email_input.send_keys(email)

    wait.until(
        EC.element_to_be_clickable((By.ID, "identifierNext"))
    ).click()

    # 6. Nhập mật khẩu
    print("[6] Đang nhập mật khẩu ...")
    password_input = wait.until(
        EC.element_to_be_clickable((By.NAME, "Passwd"))
    )
    password_input.clear()
    password_input.send_keys(password)

    wait.until(
        EC.element_to_be_clickable((By.ID, "passwordNext"))
    ).click()

    # 6.5. Xử lý popup "I understand" (tài khoản mới - Google ToS)
    try:
        print("[6.5] Kiểm tra popup 'I understand' ...")
        i_understand_btn = WebDriverWait(driver, 8).until(
            EC.element_to_be_clickable((By.XPATH, "//div[@id='gaplustosNext']//button"))
        )
        i_understand_btn.click()
        print("[6.5] Đã click 'I understand'")
    except Exception:
        print("[6.5] Không có popup 'I understand', tiếp tục ...")

    # 6.6. Xử lý nút "Continue" sau I understand
    try:
        print("[6.6] Kiểm tra nút 'Continue' ...")
        continue_btn = WebDriverWait(driver, 8).until(
            EC.element_to_be_clickable((By.XPATH, "//span[contains(.,'Continue')]"))
        )
        continue_btn.click()
        print("[6.6] Đã click 'Continue'")
    except Exception:
        print("[6.6] Không có nút 'Continue', tiếp tục ...")

    # 7. Chờ redirect về itviec
    print("[7] Đang chờ redirect về itviec ...")
    time.sleep(5)

    # 8. Quay về tab itviec
    driver.switch_to.window(driver.window_handles[0])
    print("[✓] Đăng nhập Google thành công!")


def handle_onboarding(driver: uc.Chrome):
    """Xử lý onboarding modal itviec sau login lần đầu."""
    wait = WebDriverWait(driver, 10)

    # Step 1: Click Next
    try:
        print("[OB-1] Click 'Next' onboarding ...")
        wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, ".button_to > .ibtn-primary"))
        ).click()
    except Exception:
        print("[OB-1] Không có nút Next, bỏ qua ...")
        return

    # Step 2: Click Skip
    try:
        print("[OB-2] Click 'Skip' ...")
        wait.until(
            EC.element_to_be_clickable((By.XPATH, "//label[contains(.,'Skip')]"))
        ).click()
    except Exception:
        print("[OB-2] Không có nút Skip ...")

    # Step 3: Click Not now
    try:
        print("[OB-3] Click 'Not now' ...")
        wait.until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Not now')]"))
        ).click()
    except Exception:
        print("[OB-3] Không có nút Not now ...")

    # Step 4: Click Start exploring
    try:
        print("[OB-4] Click 'Start exploring' ...")
        wait.until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Start exploring')]"))
        ).click()
        print("[OB-4] Hoàn thành onboarding!")
    except Exception:
        print("[OB-4] Không có nút Start exploring ...")


def do_like(driver: uc.Chrome):
    """Click like button trên bài viết và verify đã like thành công."""
    wait = WebDriverWait(driver, 15)

    # Kiểm tra nếu đã like rồi thì bỏ qua (tránh click nhầm → unlike)
    try:
        driver.find_element(By.CSS_SELECTOR, "#reaction-button .icon-wrapper.reaction-icon.clicked")
        print("[LIKE] Đã like trước đó, bỏ qua ...")
        return
    except Exception:
        pass

    print("[LIKE] Đang click like ...")
    # Dùng :not(.clicked) để chắc chắn chỉ click khi chưa like
    like_btn = wait.until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "#reaction-button .icon-wrapper.reaction-icon:not(.clicked)"))
    )
    like_btn.click()

    # Chờ button chuyển sang trạng thái "clicked" (đã like)
    print("[LIKE] Đang chờ xác nhận like ...")
    wait.until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "#reaction-button .icon-wrapper.reaction-icon.clicked"))
    )
    print("[LIKE] ✓ Like thành công!")


def sign_out(driver: uc.Chrome):
    """Đăng xuất khỏi itviec."""
    wait = WebDriverWait(driver, 10)
    print("[LOGOUT] Đang đăng xuất ...")

    # Click avatar để mở dropdown
    wait.until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "#header-user-avatar > .user-avatar"))
    ).click()

    # Click Sign Out
    wait.until(
        EC.element_to_be_clickable((By.LINK_TEXT, "Sign Out"))
    ).click()
    print("[LOGOUT] Đã đăng xuất!")


def run_account(account: dict, index: int, total: int, accounts_file: str, lock: threading.Lock) -> dict:
    email = account["email"]
    password = account["password"]

    # Stagger: mỗi worker chờ lệch nhau 3s để tránh các Chrome crash đồng loạt
    time.sleep((index - 1) % 5 * 3)

    print(f"\n{'='*50}")
    print(f"[{index}/{total}] Bắt đầu: {email}")
    print(f"{'='*50}")

    driver = create_driver()
    try:
        login_google_on_itviec(driver, email, password)
        handle_onboarding(driver)
        do_like(driver)
        sign_out(driver)
        mark_done(accounts_file, email, lock)
        print(f"[{index}/{total}] ✓ Hoàn thành & đã đánh dấu DONE: {email}")
        return {"email": email, "status": "OK", "error": ""}
    except Exception as e:
        msg = str(e)
        print(f"[{index}/{total}] ✗ Lỗi [{email}]: {msg}")
        return {"email": email, "status": "FAIL", "error": msg}
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="Auto like itviec.com")
    parser.add_argument(
        "accounts_file",
        nargs="?",
        default="accounts.txt",
        help="Đường dẫn file accounts (default: accounts.txt)"
    )
    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=MAX_WORKERS,
        help=f"Số Chrome chạy song song (default: {MAX_WORKERS})"
    )
    args = parser.parse_args()

    accounts = load_accounts(args.accounts_file)
    total = len(accounts)
    workers = args.workers
    print(f"\n🚀 File: {args.accounts_file} | Tài khoản chưa done: {total} | Parallel: {workers}")

    if total == 0:
        print("✅ Tất cả account đã được xử lý (DONE). Không có gì để chạy.")
        return

    file_lock = threading.Lock()
    results: list[dict] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(run_account, account, i, total, args.accounts_file, file_lock): account["email"]
            for i, account in enumerate(accounts, start=1)
        }
        for future in as_completed(futures):
            email = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                results.append({"email": email, "status": "FAIL", "error": str(e)})

    # ── Report ─────────────────────────────────────────
    ok   = [r for r in results if r["status"] == "OK"]
    fail = [r for r in results if r["status"] == "FAIL"]

    print(f"\n{'='*55}")
    print(f"  📊 REPORT — {args.accounts_file}")
    print(f"{'='*55}")
    print(f"  ✅ Đã like thành công : {len(ok)}/{total}")
    for r in ok:
        print(f"     ✓ {r['email']}")
    if fail:
        print(f"  ❌ Lỗi          : {len(fail)}/{total}")
        for r in fail:
            print(f"     ✗ {r['email']} — {r['error'][:80]}")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()
