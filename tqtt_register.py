"""Phase 2: submit the "Tổ Quốc Trong Tim" registration form the moment it opens.

This is a FREE registration form, not a paid ticket — there's no seat lock / payment
step (see docs/tqtt_booking_mechanism.md). The whole flow is one POST /concert/submit.

Registrant info comes from .env (TQTT_NAME, TQTT_EMAIL, TQTT_IDENTIFIER, TQTT_PHONE,
TQTT_DOB, TQTT_LIVING_AREA, TQTT_WARD, TQTT_PRIORITY_GROUP, TQTT_AGREE_INFO) or CLI flags
(CLI overrides .env). TQTT_LIVING_AREA/TQTT_WARD accept either the raw API value or a
Vietnamese/English name (looked up against data/tqtt_provinces.json / tqtt_wards.json).

IMPORTANT: --dry-run (default) only resolves+prints the payload, it does NOT submit.
Submitting for real requires --confirm-real-submit, since it has a real-world side effect
(an actual registration record under someone's real name/CCCD/phone/email).

Usage:
    python tqtt_register.py --dry-run
    python tqtt_register.py --once --confirm-real-submit          # try exactly once, now
    python tqtt_register.py --confirm-real-submit                 # poll until open, then submit
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time

from tqtt_client import PRIORITY_GROUPS, TqttClient, find_province, find_ward, load_env_file

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


class SaleNotOpenError(RuntimeError):
    """Registration hasn't opened yet — retryable."""


def resolve_area(living_area_raw: str, ward_raw: str) -> tuple[dict, dict]:
    province = find_province(living_area_raw)
    if not province:
        raise ValueError(f"Không tìm thấy tỉnh/thành '{living_area_raw}' trong data/tqtt_provinces.json")
    ward = find_ward(ward_raw, province["code"])
    if not ward:
        raise ValueError(
            f"Không tìm thấy xã/phường '{ward_raw}' thuộc '{province['name_vi']}' "
            f"(code={province['code']}) trong data/tqtt_wards.json"
        )
    return province, ward


def build_payload_from_raw(raw: dict) -> tuple[dict, dict, dict]:
    """`raw` keys: name, email, identifier, phone, date_of_birth (or dob), living_area,
    ward, priority_group (optional), agree_receive_info (optional). Used both by the CLI
    (one person, args/.env-sourced) and by the batch runner (one dict per person, loaded
    from data/registrants.json)."""
    living_area_raw = raw.get("living_area")
    ward_raw = raw.get("ward")
    if not living_area_raw or not ward_raw:
        raise ValueError("Thiếu living_area/ward")

    province, ward = resolve_area(living_area_raw, ward_raw)

    priority_group = raw.get("priority_group") or None
    if priority_group and priority_group not in PRIORITY_GROUPS:
        raise ValueError(f"priority_group phải là một trong {PRIORITY_GROUPS} hoặc bỏ trống, nhận: {priority_group}")

    identifier = raw.get("identifier")
    if identifier and not (6 <= len(identifier) <= 12):
        raise ValueError(f"identifier phải dài 6-12 ký tự (CMND/CCCD), nhận {len(identifier)} ký tự")

    dob = raw.get("date_of_birth") or raw.get("dob")
    agree_raw = raw.get("agree_receive_info", "false")
    agree = str(agree_raw).strip().lower() in ("1", "true", "yes")

    payload = {
        "name": raw.get("name"),
        "email": raw.get("email"),
        "identifier": identifier,
        "phone": raw.get("phone"),
        "date_of_birth": str(dob) if dob else None,
        "living_area": province["value"],
        "ward": ward["value"],
        "priority_group": priority_group,
        "agree_receive_info": agree,
    }

    missing = [k for k in ("name", "email", "identifier", "phone", "date_of_birth") if not payload.get(k)]
    if missing:
        raise ValueError(f"Thiếu field bắt buộc: {', '.join(missing)}")

    return payload, province, ward


def build_payload(args) -> tuple[dict, dict, dict]:
    raw = {
        "name": args.name or os.environ.get("TQTT_NAME"),
        "email": args.email or os.environ.get("TQTT_EMAIL"),
        "identifier": args.identifier or os.environ.get("TQTT_IDENTIFIER"),
        "phone": args.phone or os.environ.get("TQTT_PHONE"),
        "date_of_birth": args.dob or os.environ.get("TQTT_DOB"),
        "living_area": args.living_area or os.environ.get("TQTT_LIVING_AREA"),
        "ward": args.ward or os.environ.get("TQTT_WARD"),
        "priority_group": args.priority_group or os.environ.get("TQTT_PRIORITY_GROUP"),
        "agree_receive_info": args.agree_receive_info if args.agree_receive_info is not None else os.environ.get("TQTT_AGREE_INFO", "false"),
    }
    return build_payload_from_raw(raw)


def describe_payload(payload: dict, province: dict, ward: dict) -> str:
    return (
        f"Tên: {payload['name']} | Email: {payload['email']} | SDT: {payload['phone']}\n"
        f"CMND/CCCD: {payload['identifier']} | Năm sinh: {payload['date_of_birth']}\n"
        f"Nơi ở: {province['name_vi']} (value={province['value']}) / {ward['name_vi']} (value={ward['value']})\n"
        f"Nhóm ưu tiên: {payload['priority_group']} | Đồng ý nhận tin: {payload['agree_receive_info']}"
    )


def try_submit(client: TqttClient, payload: dict):
    capacity = client.get_capacity()
    if not capacity.get("is_open"):
        raise SaleNotOpenError("Chưa mở đăng ký (is_open=false)")

    resp = client.submit(payload)
    return resp


def main():
    parser = argparse.ArgumentParser(description="Submit tqtt.vn registration when it opens")
    parser.add_argument("--name", default=None)
    parser.add_argument("--email", default=None)
    parser.add_argument("--identifier", default=None, help="Số CMND/CCCD (6-12 ký tự)")
    parser.add_argument("--phone", default=None)
    parser.add_argument("--dob", default=None, help="Năm sinh, ví dụ 1995")
    parser.add_argument("--living-area", default=None, help="Tỉnh/thành (tên hoặc value), ví dụ 'Ha_noi' hoặc 'Hà Nội'")
    parser.add_argument("--ward", default=None, help="Xã/phường (tên hoặc value id) thuộc tỉnh/thành đã chọn")
    parser.add_argument("--priority-group", default=None, choices=sorted(PRIORITY_GROUPS))
    parser.add_argument("--agree-receive-info", default=None, help="true/false")
    parser.add_argument("--interval", type=int, default=5, help="Chu kỳ poll (giây) khi chưa mở, mặc định 5s")
    parser.add_argument("--jitter", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true", default=True, help="(mặc định) chỉ resolve+in payload, KHÔNG submit")
    parser.add_argument("--confirm-real-submit", action="store_true", help="Bắt buộc để thực sự submit form thật")
    parser.add_argument("--once", action="store_true", help="Chỉ thử 1 lần rồi thoát (kể cả khi chưa mở)")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()
    if args.confirm_real_submit:
        args.dry_run = False

    load_env_file(args.env_file)

    try:
        payload, province, ward = build_payload(args)
    except ValueError as e:
        print(f"[ERROR] {e}")
        return

    print("[PAYLOAD]\n" + describe_payload(payload, province, ward))

    if args.dry_run:
        print("[DRY-RUN] Dừng ở đây, không gọi POST /concert/submit thật. Thêm --confirm-real-submit để submit thật.")
        return

    client = TqttClient()

    while True:
        try:
            resp = try_submit(client, payload)
            if resp.ok:
                body = resp.json() if resp.content else {}
                if body.get("result"):
                    print("[SUCCESS] Đăng ký thành công:", body)
                else:
                    print("[WARN] Server trả 200 nhưng result không truthy:", body)
            else:
                # Mirror the frontend's own retry policy: never retry 409/429.
                print(f"[FAILED] HTTP {resp.status_code}: {resp.text}")
                if resp.status_code in (409, 429):
                    print("[STOP] 409/429 — không retry (hết chỗ hoặc bị rate-limit), dừng theo đúng policy của frontend.")
            break
        except SaleNotOpenError as e:
            print(f"[WAIT] {e}")
            if args.once:
                break
            sleep_for = args.interval + random.randint(0, max(args.jitter, 0))
            time.sleep(sleep_for)


if __name__ == "__main__":
    main()
