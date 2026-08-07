# TQTT.VN automation — architecture, deploy & operations

## Kiến trúc

```
tqtt_client.py           # API client (capacity/submit) + tra tỉnh/xã (nguồn dùng chung)
tqtt_watch.py             # Phase 1: poll /concert/capacity, báo Telegram khi is_open=true
tqtt_register.py          # Phase 2: submit form cho 1 người (CLI flags hoặc TQTT_* trong .env)
tqtt_register_batch.py    # Phase 2b: submit song song (thread) cho NHIỀU người, từ data/registrants.json
data/tqtt_provinces.json  # 34 tỉnh/thành (trích từ bundle JS, public data, có commit)
data/tqtt_wards.json      # 3321 xã/phường (trích từ bundle JS, public data, có commit)
data/registrants.json     # Danh sách người đăng ký THẬT (CCCD/SĐT/email) — KHÔNG commit, xem .gitignore
data/registrants.example.json  # Template (data giả) — có commit, để biết format
.env                       # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TQTT_* (1 người, dùng cho tqtt_register.py)
```

Khác với Xeca (mua vé xe, có giữ ghế + thanh toán VNPay), đây là **form đăng ký miễn phí, không
giữ chỗ/thanh toán** — xem `docs/tqtt_booking_mechanism.md`. Vì vậy không cần bot Telegram 2 chiều
hay state máy phức tạp: chỉ cần 1 lần poll-rồi-submit, xong là xong (không có trạng thái
`pending_payment` như Xeca).

## ⚠️ Dữ liệu cá nhân — không commit

`data/registrants.json` chứa **CCCD, số điện thoại, email thật** của người đăng ký —
đã thêm vào `.gitignore`. `deploy.sh` chạy `git add -A`, nên **luôn kiểm tra `git status`
trước khi deploy** để chắc file này không lỡ bị track. Nếu cần đồng bộ giữa máy cá nhân và
server, dùng `scp` trực tiếp (như cách `.env` được xử lý), không qua git:
```bash
scp data/registrants.json root@hieuit.top:/opt/autobot/data/registrants.json
```

## Chạy local (khuyến nghị cho việc này — sự kiện chỉ mở 1 lần, không cần server 24/7)

```bash
pip install -r requirements.txt      # cần thêm "requests"

# 1 người, qua .env:
python3 tqtt_register.py --dry-run                 # kiểm tra payload trước
python3 tqtt_register.py --confirm-real-submit      # tự poll rồi submit khi mở

# Nhiều người cùng lúc, qua data/registrants.json:
python3 tqtt_register_batch.py --dry-run
python3 tqtt_register_batch.py --confirm-real-submit
```

Chạy trực tiếp trên máy cá nhân (mở terminal, cắm điện, để máy không sleep) là đủ vì đây
chỉ là chờ 1 sự kiện xảy ra 1 lần rồi thoát — không cần deploy lên server trừ khi bạn muốn
chắc chắn máy không tắt/mất mạng đúng lúc mở.

## Deploy lên server (tuỳ chọn, nếu muốn chạy 24/7 không phụ thuộc máy cá nhân)

Tận dụng server Xeca đã có (`root@hieuit.top`, `/opt/autobot`, cùng repo git). Code
`tqtt_*.py` nằm chung repo nên `git pull` một lần lấy về cả hai. Riêng `data/registrants.json`
phải `scp` tay (không nằm trong git).

```bash
git add -A && git commit -m "..." && git push autobot main:main
ssh root@hieuit.top "cd /opt/autobot && git pull && ./venv/bin/pip install --quiet requests"
scp data/registrants.json root@hieuit.top:/opt/autobot/data/registrants.json
```

Tạo `/etc/systemd/system/tqtt-watch.service` (chỉ báo Telegram khi mở, không tự submit —
an toàn để chạy `Restart=always` vô hạn):
```ini
[Unit]
Description=TQTT.VN registration watcher (Telegram notify)
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/autobot
Environment=PYTHONIOENCODING=utf-8
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/autobot/venv/bin/python tqtt_watch.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Nếu muốn server **tự submit thật** ngay khi mở (không cần bạn bấm gì), dùng
`tqtt-register.service` — **CHỈ chạy `Restart=on-failure`, KHÔNG `Restart=always`**: script
tự poll nội bộ cho tới khi mở rồi submit đúng 1 lần và thoát (exit code 0); nếu để
`Restart=always`, service sẽ khởi động lại sau khi thành công và **submit lại lần nữa** —
gây đăng ký trùng.
```ini
[Unit]
Description=TQTT.VN auto-submit (fires once when registration opens)
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/autobot
Environment=PYTHONIOENCODING=utf-8
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/autobot/venv/bin/python tqtt_register_batch.py --confirm-real-submit --priority-name "PHẠM THỊ THỤC CHINH"
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Ưu tiên 1 người gọi API trước (`--priority-name`)
Khi bắn song song cho nhiều người, thứ tự request tới được server không đảm bảo nếu chỉ dựa
vào thread scheduling. `--priority-name "<tên>"` (khớp gần đúng, không phân biệt hoa/thường)
đưa người đó lên đầu danh sách VÀ cho request của họ một khoảng đầu (`PRIORITY_HEAD_START_SECONDS`
= 0.05s trong `tqtt_register_batch.py`) trước khi bắn phần còn lại — đảm bảo request của họ
thực sự rời máy trước, không chỉ "may rủi" theo lịch trình thread.

```bash
ssh root@hieuit.top "systemctl daemon-reload && systemctl enable --now tqtt-watch tqtt-register"
```

Xem log: `ssh root@hieuit.top "journalctl -u tqtt-register -f"`.

Sau khi sự kiện đã đăng ký xong (thành công hoặc xác nhận thất bại), tắt hẳn để tránh giữ
service treo vô ích:
```bash
ssh root@hieuit.top "systemctl disable --now tqtt-watch tqtt-register"
```
