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
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import os

from tqtt_client import (
    TqttClient,
    is_in_tight_window,
    load_env_file,
    next_poll_interval,
    parse_target_time,
    refresh_field_keys,
    remap_payload,
    send_telegram_message,
)
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


def submit_one(entry: dict, client: TqttClient) -> tuple[str, bool, str]:
    """Takes a pre-built, pre-warmed TqttClient (one per person, never shared across
    threads) rather than creating one here — see `warm_up_clients()`: building a fresh
    Session at the moment of submission would pay a full TCP+TLS handshake exactly when
    speed matters most."""
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


PRIORITY_HEAD_START_SECONDS = 0.05


def reorder_priority(resolved: list[dict], priority_name: str | None) -> list[dict]:
    """Moves every entry whose name matches `priority_name` (case-insensitive substring)
    to the front, preserving the relative order of everyone else. Used to make sure a
    specific person's request is the one that actually reaches the server first when
    capacity is limited (see PRIORITY_HEAD_START_SECONDS in submit_all)."""
    if not priority_name:
        return resolved
    needle = priority_name.strip().lower()
    priority = [e for e in resolved if needle in (e["payload"].get("name") or "").lower()]
    rest = [e for e in resolved if needle not in (e["payload"].get("name") or "").lower()]
    if not priority:
        print(f"[WARN] --priority-name '{priority_name}' không khớp ai trong danh sách — bỏ qua.")
        return resolved
    return priority + rest


def warm_up_clients(clients: list[TqttClient], skip: TqttClient | None = None) -> None:
    """Sends one cheap GET /concert/capacity through each client's Session, establishing
    (and keeping alive) the TCP+TLS connection to api.tqtt.vn ahead of time — so the actual
    POST /concert/submit at the critical moment doesn't pay handshake latency. `skip` lets
    the poll loop avoid re-warming the session it just used a moment ago for the capacity
    check itself. Failures here are non-fatal (just means that one client warms up cold
    at submit time instead)."""
    for c in clients:
        if c is skip:
            continue
        try:
            c.get_capacity()
        except Exception:
            pass


def submit_all(resolved: list[dict], clients: list[TqttClient], has_priority: bool = False) -> list[tuple[str, bool, str]]:
    results = []
    with ThreadPoolExecutor(max_workers=len(resolved)) as pool:
        if has_priority:
            # Fire the priority person's request alone first and give it a brief head
            # start on the wire before anyone else's connection even opens — submission
            # order alone (without this) only weakly favors whoever goes first.
            priority_future = pool.submit(submit_one, resolved[0], clients[0])
            time.sleep(PRIORITY_HEAD_START_SECONDS)
            futures = {priority_future: resolved[0]}
            futures.update({
                pool.submit(submit_one, entry, clients[i]): entry
                for i, entry in enumerate(resolved[1:], start=1)
            })
        else:
            futures = {
                pool.submit(submit_one, entry, clients[i]): entry
                for i, entry in enumerate(resolved)
            }
        for future in as_completed(futures):
            results.append(future.result())
    return results


def main():
    parser = argparse.ArgumentParser(description="Submit tqtt.vn registration for multiple people in parallel when it opens")
    parser.add_argument("--people-file", default="data/registrants.json")
    parser.add_argument("--priority-name", default=None,
                         help="Tên (khớp gần đúng, không phân biệt hoa/thường) được gọi API TRƯỚC những người còn lại")
    parser.add_argument("--interval", type=int, default=5, help="Chu kỳ poll (giây) khi chưa mở, mặc định 5s")
    parser.add_argument("--jitter", type=int, default=2)
    parser.add_argument("--target-time", default=None,
                         help="Giờ dự kiến mở đăng ký, định dạng HH:MM hoặc HH:MM:SS (giờ máy chủ). "
                              "Quanh giờ này script tự chuyển sang poll dồn dập (0.4s/lần) "
                              "thay vì chờ --interval như bình thường.")
    parser.add_argument("--dry-run", action="store_true", default=True, help="(mặc định) chỉ resolve+in payload, KHÔNG submit")
    parser.add_argument("--confirm-real-submit", action="store_true", help="Bắt buộc để thực sự submit form thật cho tất cả")
    parser.add_argument("--once", action="store_true", help="Chỉ thử 1 lần rồi thoát (kể cả khi chưa mở)")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()
    if args.confirm_real_submit:
        args.dry_run = False

    target_ts = parse_target_time(args.target_time) if args.target_time else None
    if target_ts:
        print(f"[INFO] Target time: {args.target_time} — sẽ tự poll dồn dập (0.4s/lần) quanh giờ này.")

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

    resolved = reorder_priority(resolved, args.priority_name)
    if args.priority_name:
        print(f"[INFO] Ưu tiên gọi API trước cho: {resolved[0]['label']}")

    print(f"[INFO] {len(resolved)}/{len(people)} người hợp lệ:")
    for entry in resolved:
        print(f"--- {entry['label']} ---")
        print(describe_payload(entry["payload"], entry["province"], entry["ward"]))

    if args.dry_run:
        print("[DRY-RUN] Dừng ở đây, không gọi POST /concert/submit thật. Thêm --confirm-real-submit để submit thật.")
        return

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    def notify(text: str):
        print(text)
        if not (token and chat_id):
            print("[WARN] TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID chưa cấu hình, chỉ in console.")
            return
        try:
            send_telegram_message(token, chat_id, text)
        except Exception as e:
            # Never let a Telegram hiccup look like the registration itself failed.
            print(f"[WARN] Gửi Telegram thất bại (không ảnh hưởng kết quả đăng ký): {e}")

    # One dedicated, pre-warmed TqttClient per person — see warm_up_clients()/submit_one().
    clients = [TqttClient() for _ in resolved]
    warm_up_clients(clients)
    print(f"[INFO] Đã pre-warm {len(clients)} kết nối tới api.tqtt.vn.")
    poll_client = clients[0]

    field_keys = None
    was_tight = False
    while True:
        tight_now = is_in_tight_window(target_ts)
        if tight_now and not was_tight:
            print("[INFO] Đã vào khung giờ gần target-time — chuyển sang poll dồn dập (0.4s/lần).")
        elif was_tight and not tight_now:
            print("[INFO] Đã ra khỏi khung giờ target-time mà vẫn chưa mở — quay lại poll bình thường.")
        was_tight = tight_now

        try:
            field_keys = refresh_field_keys(notify=notify)
        except Exception as e:
            print(f"[WARN] Không refresh được field key mapping ({e}); dùng mapping cũ nếu có.")
            if field_keys is None:
                print("[WAIT] Chưa có field key mapping nào, chưa thể submit an toàn, thử lại sau.")
                if args.once:
                    break
                time.sleep(next_poll_interval(args.interval, args.jitter, target_ts))
                continue

        capacity = poll_client.get_capacity()
        if capacity.get("is_open"):
            notify(f"🎉 tqtt.vn đã mở đăng ký — đang gửi {len(resolved)} yêu cầu song song ngay...")
            wire_resolved = [{**e, "payload": remap_payload(e["payload"], field_keys)} for e in resolved]
            results = submit_all(wire_resolved, clients, has_priority=bool(args.priority_name))

            lines = []
            for label, ok, note in results:
                tag = "✅ THÀNH CÔNG" if ok else "❌ THẤT BẠI"
                line = f"{tag} — {label}: {note}"
                print(line)
                lines.append(line)
            ok_count = sum(1 for _, ok, _ in results if ok)
            summary = f"📋 Kết quả đăng ký TQTT ({ok_count}/{len(results)} thành công):\n\n" + "\n".join(lines)
            notify(summary)
            break

        print("[WAIT] Chưa mở đăng ký (is_open=false)")
        if args.once:
            break
        # Keep everyone else's connection warm too while waiting (poll_client's is already
        # fresh from the capacity check above) — cheap: len(clients)-1 extra GETs per cycle.
        # Skip re-warming during the tight window — pointless overhead when we're about to
        # loop back around in 0.4s anyway.
        if not tight_now:
            warm_up_clients(clients, skip=poll_client)
        time.sleep(next_poll_interval(args.interval, args.jitter, target_ts))


if __name__ == "__main__":
    main()
