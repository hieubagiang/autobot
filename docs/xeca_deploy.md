# Xeca automation — architecture, deploy & operations

## Kiến trúc

```
xeca_client.py        # API client + seat/direction/business logic (nguồn dùng chung)
xeca_state.py          # state.json: watchlist [{id, direction, depart_date, quantity, status, ...}]
xeca_control.py         # tầng điều khiển dùng chung: CRUD watchlist, systemctl, trigger booking
                         #   -> đây là chỗ một web UI (React) tương lai sẽ gọi vào, thay vì
                         #      viết lại logic trong bot/CLI.
xeca_ticket_watch.py    # Phase 1: service chạy nền, poll watchlist, báo Telegram khi mở bán
xeca_auto_book.py       # Phase 2: chọn chuyến/ghế, tạo đơn, lấy link thanh toán VNPay
xeca_telegram_bot.py    # Bot 2 chiều: nhận lệnh Telegram để quản lý watchlist + trigger đặt vé
state.json               # watchlist hiện tại (không commit — xem .gitignore)
.env                     # secrets: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, XECA_PASSENGER_*
```

Services trên server (`root@hieuit.top`, thư mục `/opt/autobot`):
- **xeca-watch.service** — chỉ *gửi* thông báo (không gọi Telegram getUpdates), poll watchlist
  mỗi ~5 phút, tự khởi động lại khi crash/reboot.
- **xeca-bot.service** — bot 2 chiều, long-poll Telegram getUpdates, chỉ chấp nhận lệnh từ
  `TELEGRAM_CHAT_ID` đã cấu hình. Không conflict với xeca-watch vì watch không gọi getUpdates.

Repo GitHub: `git@github.com:hieubagiang/autobot.git` (remote tên `autobot` trong git local).
Server pull code qua deploy key riêng (`/root/.ssh/autobot_deploy_key`), không dùng SSH key cá
nhân của bạn.

## Deploy / cập nhật code

```bash
./deploy.sh "mô tả thay đổi"
```
Script này: commit + push lên GitHub → SSH vào server → `git pull` → cài lại `requests` (rẻ,
idempotent) → restart cả 2 service.

Nếu muốn làm tay từng bước:
```bash
git add -A && git commit -m "..." && git push autobot main:main
ssh root@hieuit.top "cd /opt/autobot && git pull && systemctl restart xeca-watch xeca-bot"
```

## Setup lần đầu trên server mới (tham khảo / disaster recovery)

1. Tạo deploy key riêng cho repo trên server:
   ```bash
   ssh root@hieuit.top "ssh-keygen -t ed25519 -f /root/.ssh/autobot_deploy_key -N '' -C autobot-deploy"
   ```
   Copy public key, vào https://github.com/hieubagiang/autobot/settings/keys → Add deploy key
   (không cần write access).
2. Clone + venv:
   ```bash
   ssh root@hieuit.top "
     GIT_SSH_COMMAND='ssh -i /root/.ssh/autobot_deploy_key -o IdentitiesOnly=yes' \
       git clone git@github.com:hieubagiang/autobot.git /opt/autobot
     cd /opt/autobot && git config core.sshCommand 'ssh -i /root/.ssh/autobot_deploy_key -o IdentitiesOnly=yes'
     python3 -m venv venv && ./venv/bin/pip install requests
   "
   ```
3. Copy `.env` thật sang server (không nằm trong git):
   ```bash
   scp .env root@hieuit.top:/opt/autobot/.env
   ssh root@hieuit.top "chmod 600 /opt/autobot/.env"
   ```
4. Tạo 2 systemd unit (`/etc/systemd/system/xeca-watch.service` và `xeca-bot.service`) —
   nội dung mẫu ở cuối file này.
5. `systemctl daemon-reload && systemctl enable --now xeca-watch xeca-bot`

## Quản lý qua Telegram bot

Nhắn cho bot (chỉ chat_id đã cấu hình mới được chấp nhận):

| Lệnh | Ý nghĩa |
|---|---|
| `/add <HN-HT\|HT-HN> <dd/mm/yyyy> [số lượng=1]` | Thêm vé cần theo dõi vào watchlist |
| `/setpickup <id> <tên điểm đón>` | Ghi đè điểm đón mặc định của chiều |
| `/setdropoff <id> <tên điểm trả>` | Ghi đè điểm trả mặc định của chiều |
| `/list` | Xem watchlist |
| `/remove <id>` | Xoá 1 mục |
| `/status` | Trạng thái service + kiểm tra mở bán trực tiếp (real-time) |
| `/book <id>` | Xem trước kế hoạch đặt (chuyến/ghế/giá), cần `/confirm` để đặt thật |
| `/confirm <mã>` | Xác nhận đặt vé THẬT (mã có hiệu lực 2 phút) — **tốn tiền thật** |
| `/start` `/stop` `/restart` | Điều khiển service xeca-watch |
| `/logs [n]` | n dòng log gần nhất của xeca-watch |
| `/help` | Danh sách lệnh |

`xeca-watch.service` tự động báo khi 1 mục "pending" chuyển sang mở bán, kèm gợi ý
`/book <id>`. Sau khi `/book` xong (thành công hoặc thất bại), mục đó không tự gửi lại
thông báo nữa (status khác `pending`).

## Chiều tuyến (direction) đã cấu hình

- `HN-HT` (Hà Nội → Hà Tĩnh): đón "493 Nguyễn Trãi", trả "VP THẠCH HÀ - HT" (tuyến ven biển:
  trả "XANH ĐỎ THẠCH LONG - HT").
- `HT-HN` (Hà Tĩnh → Hà Nội): đón "VP THẠCH HÀ - HT" (tuyến ven biển: đón
  "XANH ĐỎ THẠCH LONG - HT"), trả "Số 275 Nguyễn Trãi".

Xem `docs/xeca_booking_mechanism.md` để biết chi tiết kỹ thuật, gồm một giả định **chưa
verify** (field `custArriveZone` khi điểm trả là home-pickup-zone) — nên `--dry-run` trước khi
tin tưởng đặt thật ở chiều `HT-HN`.

## Định hướng tương lai (chưa làm)

- **Web UI (React)**: sẽ gọi vào `xeca_control.py` (không phải viết lại logic) — cần thêm một
  lớp API mỏng (vd FastAPI) bọc quanh các hàm `add_ticket_request/list_ticket_requests/
  remove_ticket_request/get_status/service_control/run_booking`.
- Xác thực chỗ đứng cho web UI (không thể whitelist theo chat_id như Telegram) — cần bàn riêng
  khi tới lúc làm.

## Systemd unit mẫu

`/etc/systemd/system/xeca-watch.service`:
```ini
[Unit]
Description=Xeca ticket sale watcher (Telegram notify)
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/autobot
Environment=PYTHONIOENCODING=utf-8
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/autobot/venv/bin/python xeca_ticket_watch.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/xeca-bot.service`:
```ini
[Unit]
Description=Xeca Telegram control bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/autobot
Environment=PYTHONIOENCODING=utf-8
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/autobot/venv/bin/python xeca_telegram_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
