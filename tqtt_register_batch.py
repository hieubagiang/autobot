"""Phase 2b: submit the "Tổ Quốc Trong Tim" registration form for MULTIPLE people in
parallel, the moment it opens. Each submission is an independent POST /concert/submit
(no shared seat/order state, unlike a paid-ticket flow) — so firing them concurrently in
threads is safe and doesn't need any coordination between people.

People come from a JSON file (default data/registrants.json), one object per person with
the same fields as tqtt_register.py's .env vars (name, email, identifier, phone,
date_of_birth, living_area, ward, priority_group, agree_receive_info).

IMPORTANT: --dry-run (default) only resolves+prints every payload, it does NOT submit.
Submitting for real requires --confirm-real-submit.

Usage:
    python tqtt_register_batch.py --dry-run
    python tqtt_register_batch.py --once --confirm-real-submit      # try exactly once, now
    python tqtt_register_batch.py --confirm-real-submit             # poll until open, then submit all
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from tqtt_client import TqttClient, load_env_file
from tqtt_register import build_payload_from_raw, describe_payload

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


def load_people(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        people = json.load(f)
    if not isinstance(people, list) or not people:
        raise ValueError(f"{path} phải là 1 JSON array chứa ít nhất 1 người")
    return people


def resolve_all(people: list[dict]) -> list[dict]:
    """Resolves living_area/ward + validates every person up front, so a typo in one
    person's ward doesn't get discovered only after the form opens."""
    resolved = []
    for i, raw in enumerate(people):
        try:
            payload, province, ward = build_payload_from_raw(raw)
        except ValueError as e:
            print(f"[ERROR] Người #{i + 1} ({raw.get('name', '?')}): {e}")
            continue
        resolved.append({"payload": payload, "province": province, "ward": ward, "label": raw.get("name", f"#{i + 1}")})
    return resolved


def submit_one(entry: dict) -> tuple[str, bool, str]:
    """Own TqttClient/session per thread — simplest way to avoid any doubt about
    thread-safety of a shared requests.Session under real concurrency."""
    client = TqttClient()
    label = entry["label"]
    try:
        resp = client.submit(entry["payload"])
    except Exception as e:
        return label, False, f"lỗi network: {e}"

    if resp.ok:
        body = resp.json() if resp.content else {}
        if body.get("result"):
            return label, True, f"OK: {body}"
        return label, False, f"HTTP 200 nhưng result không truthy: {body}"

    note = f"HTTP {resp.status_code}: {resp.text}"
    if resp.status_code in (409, 429):
        note += " — không retry (đúng policy của frontend)"
    return label, False, note


def submit_all(resolved: list[dict], max_workers: int) -> list[tuple[str, bool, str]]:
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(submit_one, entry): entry for entry in resolved}
        for future in as_completed(futures):
            results.append(future.result())
    return results


def main():
    parser = argparse.ArgumentParser(description="Submit tqtt.vn registration for multiple people in parallel when it opens")
    parser.add_argument("--people-file", default="data/registrants.json")
    parser.add_argument("--interval", type=int, default=5, help="Chu kỳ poll (giây) khi chưa mở, mặc định 5s")
    parser.add_argument("--jitter", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true", default=True, help="(mặc định) chỉ resolve+in payload, KHÔNG submit")
    parser.add_argument("--confirm-real-submit", action="store_true", help="Bắt buộc để thực sự submit form thật cho tất cả")
    parser.add_argument("--once", action="store_true", help="Chỉ thử 1 lần rồi thoát (kể cả khi chưa mở)")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()
    if args.confirm_real_submit:
        args.dry_run = False

    load_env_file(args.env_file)

    try:
        people = load_people(args.people_file)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"[ERROR] Không đọc được {args.people_file}: {e}")
        return

    resolved = resolve_all(people)
    if not resolved:
        print("[ERROR] Không có người nào resolve hợp lệ, dừng.")
        return

    print(f"[INFO] {len(resolved)}/{len(people)} người hợp lệ:")
    for entry in resolved:
        print(f"--- {entry['label']} ---")
        print(describe_payload(entry["payload"], entry["province"], entry["ward"]))

    if args.dry_run:
        print("[DRY-RUN] Dừng ở đây, không gọi POST /concert/submit thật. Thêm --confirm-real-submit để submit thật.")
        return

    client = TqttClient()

    while True:
        capacity = client.get_capacity()
        if capacity.get("is_open"):
            print(f"[OPEN] is_open=true — bắn {len(resolved)} request song song ngay...")
            results = submit_all(resolved, max_workers=len(resolved))
            for label, ok, note in results:
                tag = "[SUCCESS]" if ok else "[FAILED]"
                print(f"{tag} {label}: {note}")
            break

        print("[WAIT] Chưa mở đăng ký (is_open=false)")
        if args.once:
            break
        sleep_for = args.interval + random.randint(0, max(args.jitter, 0))
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
