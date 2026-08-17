# Crypto signals listener — kiến trúc, deploy & vận hành

## Kiến trúc

```
crypto_signals/
├── state.py           # crypto_signals_state.json: channels[], signals[] (lock file)
├── parser.py            # parse_message(text, channel_kind) -> dict, pure function
├── control.py             # CRUD channel, ghép update vào signal, systemctl/journalctl
├── format.py               # outcome -> text gửi Telegram, pure function
├── env.py                   # .env loader (độc lập, không import xeca_client)
├── telegram_api.py            # sendMessage dùng chung giữa listener + bot
├── listener.py                  # Telethon client, nghe realtime, chỉ GỬI
└── telegram_bot.py                # bot 2 chiều, quản lý danh sách kênh

crypto_signals_state.json  # không commit — xem .gitignore
.env                        # secrets: TELEGRAM_API_ID/HASH, CRYPTO_SIGNALS_TELEGRAM_*
crypto_signals_session.session  # Telethon session (không commit) — xem bước đăng nhập bên dưới
```

Services trên server (`root@hieuit.top`, thư mục `/opt/autobot`):
- **crypto-signals-listen.service** — chỉ *gửi* thông báo (không gọi Telegram getUpdates).
- **crypto-signals-bot.service** — bot 2 chiều, long-poll Telegram getUpdates, chỉ chấp
  nhận lệnh từ `CRYPTO_SIGNALS_TELEGRAM_CHAT_ID` đã cấu hình.

## Đăng nhập Telethon lần đầu (bắt buộc làm ở máy có TTY, không phải qua deploy.sh)

Telethon (tài khoản cá nhân, không phải Bot API) cần đăng nhập số điện thoại + mã OTP một
lần duy nhất, y hệt `telegram-tools/telegram_bot_episode_grabber.py`:

```bash
python -c "
from telethon.sync import TelegramClient
import os
from crypto_signals.env import load_env_file
load_env_file('.env')
client = TelegramClient('crypto_signals_session', int(os.environ['TELEGRAM_API_ID']), os.environ['TELEGRAM_API_HASH'])
client.start()
print('Đăng nhập xong, session đã lưu vào crypto_signals_session.session')
"
```

Chạy lệnh trên **ở máy local** (không phải SSH vào server, vì cần nhập OTP tương tác), rồi
copy file session thật lên server:

```bash
scp crypto_signals_session.session root@hieuit.top:/opt/autobot/crypto_signals_session.session
```

`deploy.sh` chỉ restart 2 service này nếu `crypto_signals_session.session` đã tồn tại trên
server — tránh crash-loop vô ích khi chưa đăng nhập.

## Quản lý qua Telegram bot

Nhắn cho bot (chỉ `CRYPTO_SIGNALS_TELEGRAM_CHAT_ID` đã cấu hình mới được chấp nhận):

| Lệnh | Ý nghĩa |
|---|---|
| `/addchannel <username> [signal\|commentary]` | Thêm kênh cần nghe (mặc định `signal`) |
| `/removechannel <username>` | Bỏ nghe 1 kênh |
| `/listchannels` | Danh sách kênh đang nghe |
| `/open` | Danh sách signal đang mở (chưa closed) |
| `/status` | Trạng thái `crypto-signals-listen.service` |
| `/logs [n]` | n dòng log gần nhất |
| `/help` | Danh sách lệnh |

Sau `/addchannel`/`/removechannel`, restart tay listener để áp dụng danh sách kênh mới:

```bash
ssh root@hieuit.top "systemctl restart crypto-signals-listen.service"
```

(Chưa tự động restart từ trong bot ở Phase 1 — xem "Định hướng tương lai" bên dưới.)

## Kênh khởi tạo

- `crypto_vulture_signals` (`kind=signal`) — Entry/Target/SL có cấu trúc (2 khuôn mẫu:
  "SCALP TRADE" một dòng, và "Entries/Targets/Stop Loss" đánh số).
- `CryptoVIPsignalTA` (`kind=commentary`) — nhận định thị trường bằng văn xuôi, không có
  Entry/TP/SL — chỉ trích coin được nhắc tới (xem `parser.extract_commentary_coins`).

## Định hướng tương lai (chưa làm — xem spec)

- Bot tự `systemctl restart` listener ngay sau `/addchannel`/`/removechannel` thay vì cần
  restart tay.
- Tổng hợp "xu hướng"/"ghép cặp" xuyên kênh (đối chiếu signal có cấu trúc với commentary
  cùng coin) — xem mục "Ngoài phạm vi Phase 1" trong
  `docs/superpowers/specs/2026-08-17-crypto-signals-design.md`.

## Systemd unit mẫu

`/etc/systemd/system/crypto-signals-listen.service`:
```ini
[Unit]
Description=Crypto signals Telethon listener (Telegram notify)
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/autobot
Environment=PYTHONIOENCODING=utf-8
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/autobot/venv/bin/python -m crypto_signals.listener
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/crypto-signals-bot.service`:
```ini
[Unit]
Description=Crypto signals Telegram control bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/autobot
Environment=PYTHONIOENCODING=utf-8
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/autobot/venv/bin/python -m crypto_signals.telegram_bot
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Setup lần đầu (bắt buộc trước khi `deploy.sh` restart được 2 service này)

Sau khi tạo 2 file unit ở trên (và đã scp `crypto_signals_session.session` thật lên server —
xem bước đăng nhập Telethon phía trên), chạy một lần duy nhất để nạp + bật service:

```bash
ssh root@hieuit.top "systemctl daemon-reload && systemctl enable --now crypto-signals-listen crypto-signals-bot"
```

Bỏ qua bước này thì lần đầu tiên bạn thêm `TELEGRAM_API_ID=` vào `.env` trên server và chạy
`deploy.sh`, lệnh `systemctl restart crypto-signals-listen.service crypto-signals-bot.service`
trong guard restart sẽ thất bại vì unit chưa được cài/enable — và vì khối ssh của `deploy.sh`
chạy dưới `set -e`, cả deploy sẽ dừng giữa chừng trước khi in dòng trạng thái cuối cùng.
