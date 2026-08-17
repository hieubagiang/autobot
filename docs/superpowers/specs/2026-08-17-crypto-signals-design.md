# Bot nghe tín hiệu crypto đa kênh Telegram (Phase 1: listen + parse + relay) — Design Spec

## Bối cảnh & mục tiêu

Nghe tin nhắn từ một hoặc nhiều kênh Telegram public chuyên đăng "signal" giao dịch crypto (kênh
đầu tiên: `@crypto_vulture_signals`), tự động parse ra entry/target/stop-loss, và báo lại qua
Telegram của người dùng theo định dạng gọn, dễ đọc — tận dụng cơ chế đăng nhập Telethon (tài khoản
cá nhân, MTProto) đã có sẵn trong `telegram-tools/telegram_bot_episode_grabber.py`, và cơ chế
service-đôi (watch/bot) + state JSON + bot Telegram 2 chiều đã dùng cho `xeca_*`/`cinema_booking`.

**Phase 1 (spec này)**: nghe N kênh, parse, lưu trạng thái từng signal, relay thông báo đã format
đẹp về Telegram cá nhân. Quản lý danh sách kênh qua lệnh Telegram động (`/addchannel`,
`/removechannel`...), không phải sửa file + restart tay.

**Ngoài phạm vi Phase 1 (để sau)**: tổng hợp "xu hướng" xuyên nhiều kênh (vd "BTC: 3/4 kênh đang
Long trong 2h qua ⇒ bullish"). Cần dữ liệu thực tế từ nhiều kênh chạy một thời gian trước khi thiết
kế phần này cho chuẩn — làm sau khi Phase 1 đã chạy ổn.

## Nghiên cứu định dạng tin nhắn thật (`@crypto_vulture_signals`, xem trực tiếp qua `t.me/s/...`)

Đã lấy trực tiếp 3 tin nhắn gần nhất (2026-08-17) — **quan trọng: kênh này dùng ít nhất 2 khuôn
mẫu tin nhắn signal khác nhau, cộng thêm tin nhắn "update" không có ID rõ ràng** — parser bắt buộc
phải xử lý cả 2 khuôn mẫu signal + tin update, không được giả định chỉ có 1 dạng.

**Khuôn mẫu A — "SCALP TRADE" (một dòng, có `$`, target nối bằng dấu phẩy + `&`):**
```
🔥 CRYPTO_VULTURE_SIGNALS 🔥

✅ SCALP TRADE - UNI 🏮 TYPE - LONG 👉 ENTRY - $3.28 - $3.22 👉 TARGET - $3.30, $3.32, $3.34, $3.37 & $3.40+ 👉 SL - $3.16 🚨LEVERAGE - 60x 🔴TRADE VALID ON
```
(sau "TRADE VALID ON" còn nội dung khác — thời hạn hiệu lực — không quan trọng để parse, bỏ qua).

**Khuôn mẫu B — nhiều dòng, hashtag coin, "Entries/Targets/Stop Loss" đánh số:**
```
🔥 CRYPTO_VULTURE_SIGNALS 🔥

#ETHUSDT, #long, leverage - 50x
📈 Entries: 1895

🎯 Targets:
1) 1910
2) 1925
3) 1940
4) 1955

🚫 Stop Loss:
1) 1850
```

**Tin nhắn "update"** — báo TP/SL/entry-fill của 1 signal đã đăng trước đó, **KHÔNG có ID**, chỉ có
hashtag coin — phải khớp ngược lại signal đang mở gần nhất cùng (kênh, coin):
```
#UNI/USDT Take-Profit target 1 ✅
Profit: 36.5854% 📈
Period: 5 hr 26 min ⏰
```
```
#UNI/USDT Entry 1 ✅
Average Entry Price: 3.28 💵
```

Không có gì đảm bảo đây là toàn bộ các dạng update sẽ gặp (vd Stop-Loss hit, Cancelled chưa thấy
ví dụ sống) — parser phải có nhánh fallback an toàn (xem "Nguyên tắc parser" bên dưới), không được
crash hay âm thầm bỏ qua khi gặp định dạng lạ.

## Kiến trúc

Package `crypto_signals/` (giống `cinema_booking/`), 2 process riêng biệt (giống mô hình
xeca-watch/xeca-bot):

```
crypto_signals/
├── __init__.py
├── parser.py              # parse_message(text) -> dict, pure function, không I/O
├── state.py                # signals.json: channels[], signals[] (giống xeca_state.py — file lock)
├── control.py               # CRUD channel, list open signals, systemctl start/stop/restart
├── listener.py               # Telethon client — nghe realtime, parse, cập nhật state, relay
├── telegram_bot.py            # bot Telegram 2 chiều — quản lý kênh, xem trạng thái
└── tests/
    ├── fixtures/               # raw message text lấy trực tiếp từ kênh thật (xem mục nghiên cứu trên)
    ├── test_parser.py
    ├── test_state.py
    ├── test_control.py
    └── test_telegram_bot.py
```

Hai service:
- **`crypto-signals-listen.service`** — chạy `python -m crypto_signals.listener`. Đăng nhập
  Telethon (tài khoản cá nhân), resolve từng kênh trong `state.json["channels"]`, đăng ký 1
  `events.NewMessage` handler duy nhất (không filter theo chat ở tầng Telethon — filter bằng code
  theo danh sách kênh đang cấu hình, để việc thêm/bớt kênh không cần đổi cách đăng ký handler).
  Chỉ **gửi** thông báo qua Bot API (`requests.post sendMessage`), không gọi `getUpdates`.
- **`crypto-signals-bot.service`** — chạy `python -m crypto_signals.telegram_bot`, long-poll
  `getUpdates`, chỉ nhận lệnh từ `chat_id` đã whitelist trong `.env` (đúng khuôn mẫu
  `xeca_telegram_bot.py`). Sửa danh sách kênh xong thì tự `systemctl restart
  crypto-signals-listen.service` — listener không cần logic live-reload, tận dụng lại
  `service_control()` đã có ở `xeca_control.py`/`cinema_booking/control.py`.

## `parser.py` — nguyên tắc parser

`parse_message(text: str) -> dict`, luôn trả về 1 trong 3 dạng, **không bao giờ raise**:

- `{"type": "signal", "coin": "UNI/USDT", "direction": "LONG"|"SHORT", "scalp": bool,
  "entry": [3.28, 3.22], "targets": [3.30, 3.32, 3.34, 3.37, 3.40], "targets_plus": bool,
  "sl": 3.16, "leverage": "60x"}` — thử khớp Khuôn mẫu A rồi Khuôn mẫu B, theo thứ tự.
  - `entry`: luôn là list (1 hoặc 2 phần tử theo thứ tự xuất hiện trong tin gốc — không tự sắp xếp
    tăng/giảm, vì Khuôn mẫu A có thể ghi cận trên trước "$3.28 - $3.22").
  - `targets_plus=True` nếu target cuối có hậu tố `+` (nghĩa là "trở lên") — bỏ dấu `+` khỏi giá
    trị số nhưng giữ lại cờ này để hiển thị lại đúng ý nghĩa gốc khi relay.
  - Dấu `$` bị bỏ khi parse ra số, nhưng không giả định coin nào cũng dùng `$` (Khuôn mẫu B không
    dùng `$`).
- `{"type": "update", "coin": "UNI/USDT", "kind": "tp_hit"|"entry_filled", "target_index": int,
  "profit_pct": float | None, "period": str | None, "entry_price": float | None}` — khớp 2 dạng
  update đã thấy. Coin lấy từ hashtag đầu dòng (`#UNI`/`#ETH` + `/USDT` phía sau) — chuẩn hoá cùng
  định dạng `"XXX/USDT"` như trường `coin` của signal để so khớp được.
- `{"type": "unknown", "raw": text}` — không khớp gì cả. Vẫn được `listener.py` relay về Telegram
  (kèm nhãn "⚠️ Không nhận diện được định dạng") thay vì bị âm thầm bỏ qua — tránh lặp lại sự cố đã
  từng gặp khi hard-code field/định dạng của bên thứ 3 mà không có đường lùi khi nó khác giả định
  ([[tqtt_field_name_hardcoding_incident]]).

Test bằng đúng các đoạn text thật đã lấy ở mục nghiên cứu trên (fixtures), không phải text tự bịa.

## `state.py` — schema `signals.json`

```jsonc
{
  "channels": [
    {"username": "crypto_vulture_signals", "added_at": "2026-08-17T..."}
  ],
  "signals": [
    {
      "id": "a1b2c3d4",
      "channel": "crypto_vulture_signals",
      "coin": "UNI/USDT",
      "direction": "LONG",
      "scalp": true,
      "entry": [3.28, 3.22],
      "targets": [3.30, 3.32, 3.34, 3.37, 3.40],
      "targets_plus": true,
      "sl": 3.16,
      "leverage": "60x",
      "status": "open",              // open | tp_hit | closed
      "hits": [
        {"target_index": 1, "profit_pct": 36.5854, "period": "5 hr 26 min", "at": "2026-08-17T10:46:00"}
      ],
      "opened_at": "2026-08-17T05:19:00",
      "last_update_at": "2026-08-17T10:46:00"
    }
  ]
}
```

Cùng cơ chế lock file (`_StateFileLock`, exclusive-create + stale-lock reclaim) như
`xeca_state.py` — `listener.py` (ghi liên tục khi có tin mới) và `telegram_bot.py`
(đọc để trả lời `/status`/`/open`, ghi khi `/addchannel`/`/removechannel`) đều đụng vào cùng file
này từ 2 process khác nhau.

## Khớp tin update vào signal gốc (không có ID)

Khi `parser.parse_message()` trả `type: "update"`: tìm trong `state["signals"]` signal có cùng
`channel` + `coin`, `status != "closed"`, **mới nhất theo `opened_at`** — gắn `hits` vào đó, cập
nhật `last_update_at`. Nếu không tìm thấy signal nào khớp (vd bot mới bật sau khi signal gốc đã
được đăng), tin update vẫn được relay về Telegram nguyên văn kèm cảnh báo "không tìm thấy signal
gốc tương ứng" thay vì bị bỏ qua.

**Giới hạn đã biết (chấp nhận cho Phase 1)**: nếu 1 kênh có 2 signal đang mở cùng lúc cho cùng
coin + cùng chiều, update sẽ luôn gắn vào signal mới hơn — trường hợp hiếm, không xử lý đặc biệt.

## `listener.py` — luồng chính

1. Load `.env`: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` (giống `telegram_bot_episode_grabber.py`,
   nhưng đưa vào `.env` thay vì hard-code trong source), `CRYPTO_SIGNALS_TELEGRAM_BOT_TOKEN`,
   `CRYPTO_SIGNALS_TELEGRAM_CHAT_ID` (bot Bot API riêng để relay — **phải khác token** với
   `TELEGRAM_BOT_TOKEN`/`CINEMA_TELEGRAM_BOT_TOKEN` đã dùng, theo đúng quy tắc đã ghi trong
   `.env.example`).
2. Khởi tạo `TelegramClient("crypto_signals_session", API_ID, API_HASH)` — **session file riêng**,
   không dùng chung `episode_grabber_session` (2 process Telethon cùng mở 1 file session SQLite dễ
   lock-conflict). Lần chạy đầu tiên cần đăng nhập tay (số điện thoại + OTP) y hệt episode grabber —
   ghi chú lại bước này trong docs deploy, và cần `scp` file `.session` lên server sau khi đăng nhập
   xong ở máy local (không đăng nhập tương tác được trên server qua SSH không có TTY thuận tiện).
3. Với mỗi kênh trong `state["channels"]`, `await client.get_entity(username)` để resolve (kênh
   public — không cần join thật).
4. Đăng ký 1 `@client.on(events.NewMessage())` — trong handler: lấy username kênh từ
   `event.chat.username`, bỏ qua nếu không nằm trong danh sách đang theo dõi, gọi
   `parser.parse_message(event.raw_text)`, cập nhật `state.py`, format thông báo, gửi qua Bot API.
5. Không backfill lịch sử khi mất kết nối/restart — chỉ nghe tin mới kể từ lúc kết nối lại (chấp
   nhận, vì đây là công cụ theo dõi realtime, không phải audit đầy đủ).

## `telegram_bot.py` — lệnh

Theo khuôn mẫu `xeca_telegram_bot.py`:

- `/addchannel <username>` — thêm vào `state["channels"]`, restart `crypto-signals-listen.service`.
  Không tự validate kênh có tồn tại/public hay không ở tầng bot (bot này không có Telethon) — nếu
  sai, `listener.py` sẽ log lỗi resolve, xem qua `/logs`.
- `/removechannel <username>`
- `/listchannels`
- `/open` — liệt kê các signal đang `status != closed`, kèm coin/chiều/entry/target đã hit.
- `/status` — service `crypto-signals-listen` đang chạy không, số kênh, số signal đang mở.
- `/logs [n]`
- `/help`

## Testing

- `test_parser.py`: fixture = đúng các đoạn text thật trong mục nghiên cứu ở trên (cả 2 khuôn mẫu
  signal + 2 dạng update + ít nhất 1 case "unknown" tự bịa để test nhánh fallback).
- `test_state.py`: CRUD channel + signal, khớp update vào signal đúng/không tìm thấy.
- `test_control.py`: gọi qua `FakeStateFile`/tmp file, không đụng Telethon/Telegram thật.
- `test_telegram_bot.py`: format tin nhắn + dispatch lệnh, mock HTTP (giống
  `cinema_booking/tests/test_telegram_bot.py`).
- `listener.py` (phần Telethon thật): không có test tự động trong CI — kiểm tra thủ công đầu-cuối
  sau khi deploy, giống cách `cinema_booking`/Beta Cinemas provider đang làm (đụng tài khoản thật).

## Rủi ro / điểm còn mở

- Kênh có thể đổi định dạng bất kỳ lúc nào (không phải API chính thức, không có schema cam kết) —
  nhánh `unknown` trong parser là lưới an toàn chính, không phải giải pháp triệt để; cần theo dõi
  `/logs` định kỳ để phát hiện định dạng mới rồi bổ sung parser.
- Chưa xác nhận Telethon có nhận được `NewMessage` event cho 1 kênh public **mà tài khoản chưa từng
  tương tác/join** hay không trong mọi trường hợp — an toàn nhất là để `listener.py` tự join kênh
  (`JoinChannelRequest`) khi resolve lần đầu nếu `get_entity` không đủ để nhận event (xác nhận khi
  triển khai thật, không giả định trước).
- Chưa có cơ chế phát hiện "kênh im lặng bất thường" (vd kênh đổi username, hoặc bot bị kick) — để
  sau nếu cần, không nằm trong Phase 1.
