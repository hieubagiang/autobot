# Cinema booking bot — architecture, deploy & operations

## Kiến trúc

```
cinema_booking/types.py            # Cinema/Showtime/Seat/SeatMap/LockResult dataclasses
cinema_booking/provider.py         # abstract CinemaProvider + FakeProvider test double
cinema_booking/providers/beta.py   # Beta Cinemas: login, search, seat map, lock/release
cinema_booking/scoring.py          # seat-position scoring, center/vertical preference
cinema_booking/state.py            # cinema_booking_state.json: watchlist (không commit)
cinema_booking/control.py          # camp loop, date/cinema ranking, per-provider instance cache
cinema_booking/telegram_bot.py     # bot 2 chiều: quản lý watchlist + bật/tắt camp
```

Xem `docs/superpowers/specs/2026-08-10-cinema-booking-bot-design.md` cho chi tiết thiết kế
đầy đủ (nguyên tắc không tự động vượt CAPTCHA, cơ chế thật của Beta Cinemas, v.v.).

## Vì sao bot này KHÔNG dùng chung service/bot với xeca

- **Bot Telegram riêng.** `cinema_booking/telegram_bot.py` đọc `CINEMA_TELEGRAM_BOT_TOKEN`/
  `CINEMA_TELEGRAM_CHAT_ID` — **PHẢI** là một bot khác (tạo mới qua @BotFather), không dùng
  lại `TELEGRAM_BOT_TOKEN` của xeca. Hai process cùng long-poll `getUpdates` với cùng 1 token
  sẽ bị Telegram trả `409 Conflict` và rớt update qua lại giữa 2 bên không kiểm soát được.
- **Cần trình duyệt thật (Playwright/Chromium), không phải chỉ HTTP request.** Beta Cinemas
  cần một session Facebook đã đăng nhập thật (không CAPTCHA — xem nguyên tắc cứng trong spec),
  lưu trong 1 Chromium profile bền (`.chrome_profiles/beta`, `headless=False`). Vì server không
  có màn hình vật lý, bot chạy trong 1 **virtual display riêng** (Xvfb `:99`), **tách biệt hoàn
  toàn** với desktop cá nhân đang chạy ở `:1` (TigerVNC, `vncuser`, port 5901) — không đụng vào
  session đó.

## Services trên server (`root@hieuit.top`, thư mục `/opt/autobot`)

- **cinema-booking-xvfb.service** — virtual display `:99` (`Xvfb :99 -screen 0 1280x1024x24`),
  luôn chạy, tự khởi động lại khi crash/reboot. Không có desktop environment nào chạy trên đó
  (không xfce, không panel) — chỉ đủ để Chromium có 1 display để render vào.
- **cinema-booking-bot.service** — bot 2 chiều Telegram, `Environment=DISPLAY=:99`,
  `Requires=cinema-booking-xvfb.service` (display phải lên trước). Chạy
  `python -m cinema_booking.telegram_bot` từ `/opt/autobot`.

Cả 2 đã được tạo (`systemctl enable`) nhưng **cinema-booking-bot.service CHƯA start** — thiếu
`CINEMA_TELEGRAM_BOT_TOKEN`/`CINEMA_TELEGRAM_CHAT_ID` trong `.env` và chưa qua bước đăng nhập
Facebook lần đầu (xem 2 bước dưới).

## Việc còn lại trước khi bot camp được (2 bước, cần bạn làm)

### 1. Lấy bot token mới từ @BotFather

Nhắn `/newbot` cho [@BotFather](https://t.me/BotFather) trên Telegram, đặt tên/username tuỳ
ý, lấy token dạng `123456789:AA...`. Thêm vào `.env` thật trên server (không commit vào git):

```bash
ssh root@hieuit.top "cat >> /opt/autobot/.env" <<'EOF'
CINEMA_TELEGRAM_BOT_TOKEN=<token thật>
CINEMA_TELEGRAM_CHAT_ID=<chat_id — có thể dùng lại đúng số của TELEGRAM_CHAT_ID hiện tại>
EOF
ssh root@hieuit.top "systemctl start cinema-booking-bot.service && systemctl status cinema-booking-bot.service --no-pager --lines=5"
```

### 2. Đăng nhập Facebook lần đầu trên display `:99` (qua VNC tunnel, không public)

Bot cần một session Facebook đã đăng nhập thật lưu trong profile Chromium — đây là bước THỦ
CÔNG duy nhất, không thể tự động (đúng nguyên tắc cứng: không tự động vượt xác minh danh
tính). `x11vnc` chỉ chạy tạm thời, tự tắt sau khi bạn ngắt kết nối (`-once`), và chỉ nhận kết
nối qua SSH tunnel (`-localhost`) — không mở port ra Internet:

```bash
# Trên server: chạy x11vnc tạm thời, đặt mật khẩu khi được hỏi
ssh root@hieuit.top "x11vnc -display :99 -localhost -once -rfbport 5999 -ask" &

# Máy của bạn: mở tunnel tới port đó
ssh -L 5999:localhost:5999 root@hieuit.top
```

Sau đó mở 1 VNC viewer (TigerVNC Viewer, RealVNC, v.v.) kết nối `localhost:5999`. Trong lúc đó
(ở một cửa sổ SSH khác), kích hoạt luồng đăng nhập Facebook trên chính display `:99`:

```bash
ssh root@hieuit.top "cd /opt/autobot && DISPLAY=:99 ./venv/bin/python -c '
from cinema_booking.providers.beta import BetaProvider
p = BetaProvider()
print(p.login_via_facebook())
'"
```

Cửa sổ Chromium sẽ hiện lên trong VNC viewer — làm theo màn hình "Tiếp tục dưới tên ..." của
Facebook như bình thường (không có CAPTCHA, chỉ 1 màn hình xác nhận). Sau khi đăng nhập xong,
session được lưu bền trong `.chrome_profiles/beta` trên server — không cần lặp lại bước này
trừ khi Facebook tự huỷ session hoặc profile bị xoá.

## Deploy / cập nhật code

```bash
./deploy.sh "mô tả thay đổi"
```
Giống xeca: commit + push (remote `autobot`) → SSH vào server → `git pull` → cài lại
`requests`/`beautifulsoup4`/`playwright` (rẻ, idempotent) → restart `xeca-watch`/`xeca-bot` →
restart `cinema-booking-bot` **CHỈ NẾU** `.env` đã có `CINEMA_TELEGRAM_BOT_TOKEN` (tránh
crash-loop vô ích trước khi bước 1 ở trên hoàn tất).

## Quản lý qua Telegram bot

Nhắn cho bot mới (không phải bot xeca cũ) — chỉ `CINEMA_TELEGRAM_CHAT_ID` đã cấu hình mới được
chấp nhận:

| Lệnh | Ý nghĩa |
|---|---|
| `/add <provider> "<tên phim>" <dd/mm/yyyy hoặc dd/mm/yyyy-dd/mm/yyyy>` | Thêm phim cần theo dõi |
| `/list` | Xem watchlist |
| `/remove <id>` | Xoá 1 mục |
| `/setcinemapriority <id> <rạp 1>, <rạp 2>, ...` | Đặt thứ tự ưu tiên rạp |
| `/setquantity <id> <n>` | Số ghế cần giữ |
| `/setsweetbox <id> on\|off` | Ưu tiên ghế sweetbox/đôi |
| `/listcinemas <provider>` | Danh sách rạp của 1 provider |
| `/instant <id> on\|off` | Bật/tắt camp liên tục |
| `/paid <id>` | Đánh dấu đã thanh toán (dừng auto-relock) |
| `/status` | Trạng thái watchlist |
| `/help` | Danh sách lệnh |

`/logs` (tail log service) chưa implement — chưa cần thiết cho scope hiện tại, có thể thêm
sau cùng cách `xeca-watch`/`xeca-bot` đã làm.

## Systemd unit thật (đã tạo trên server)

`/etc/systemd/system/cinema-booking-xvfb.service`:
```ini
[Unit]
Description=Virtual display for cinema_booking's Playwright Chromium (isolated from vncuser's :1 desktop)
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/Xvfb :99 -screen 0 1280x1024x24
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/cinema-booking-bot.service`:
```ini
[Unit]
Description=Cinema booking Telegram control bot (Beta Cinemas seat camping)
After=network.target cinema-booking-xvfb.service
Requires=cinema-booking-xvfb.service

[Service]
Type=simple
WorkingDirectory=/opt/autobot
Environment=PYTHONIOENCODING=utf-8
Environment=PYTHONUNBUFFERED=1
Environment=DISPLAY=:99
ExecStart=/opt/autobot/venv/bin/python -m cinema_booking.telegram_bot
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
