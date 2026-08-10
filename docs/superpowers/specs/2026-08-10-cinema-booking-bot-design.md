# Bot tự động giữ vé xem phim (CGV, mở rộng đa rạp) — Design Spec

## Bối cảnh & mục tiêu

Xây dựng một bot tương tự `xeca_*` (đang dùng để tự động giữ vé xe khách Văn Minh) nhưng cho việc
giữ vé xem phim, bắt đầu với CGV, thiết kế đủ trừu tượng để sau này gắn thêm các chuỗi rạp khác
(BHD, Beta Cinema, Cinestar, Galaxy CineX Hà Nội Centre...) mà không phải viết lại phần lõi.

Mục tiêu cụ thể: tự động theo dõi/"camp" một buổi chiếu (phim + rạp + khoảng ngày), và khi có ghế
phù hợp trống, tự chọn + giữ ghế (không tự thanh toán) theo quy tắc ghế và ưu tiên rạp/ngày do
người dùng đặt ra, rồi báo qua Telegram để người dùng quyết định thanh toán hay không.

## Nguyên tắc cứng — KHÔNG tự động vượt qua bất kỳ bước xác minh người-thật nào

Đây là ràng buộc quan trọng nhất của thiết kế, áp dụng cho mọi provider (CGV và các rạp sau này):

- Bot **không** tự giải CAPTCHA (ảnh, ô nhập ký tự, v.v.) dưới bất kỳ hình thức nào (kể cả dùng
  model đọc ảnh, dùng lib/dịch vụ giải captcha thuê ngoài). Đăng nhập **luôn luôn** do người dùng
  tự làm bằng tay (tự nhập user/pass từ `.env` + tự giải captcha), một lần, trong một trình duyệt
  Playwright có profile bền (persistent context) để session được tái sử dụng cho các lần sau.
- Nếu bot phát hiện bị đưa về trang đăng nhập (session hết hạn) HOẶC gặp một trang thử thách lạ
  của WAF (CGV đứng sau F5 — cookie `TS...` xác nhận điều này) mà nó không nhận diện được, nó
  **dừng lại và báo cho người dùng qua Telegram**, không tự thử "vượt" bằng cách nào cả.
- Việc dùng trình duyệt Playwright thật (không phải HTTP client thô) là để **giả lập đúng hành vi
  của một người dùng thật đã đăng nhập hợp lệ** (giảm rủi ro bị WAF chặn do fingerprint khác lạ),
  **không** nhằm mục đích che giấu việc đây là automation hay đánh lừa hệ thống chống bot theo
  hướng gian lận. Không dùng các kỹ thuật "undetected"/ẩn dấu vết automation.

## Kiến trúc — trừu tượng theo provider

Toàn bộ code + test nằm trong một package duy nhất `cinema_booking/` (không rải ra root như các
script `xeca_*.py` hiện tại):

```
cinema_booking/
├── __init__.py
├── types.py                 # Cinema, Showtime, Seat, SeatMap, LockResult (dataclass, không phụ thuộc provider)
├── provider.py               # abstract class CinemaProvider
├── scoring.py                 # quy tắc chọn ghế — pure function, không I/O
├── state.py                   # watchlist lưu JSON (giống xeca_state.py)
├── control.py                 # điều phối: add/list/instant_camp_loop, provider registry
├── telegram_bot.py             # bot Telegram (entry point: python -m cinema_booking.telegram_bot)
├── providers/
│   ├── __init__.py
│   └── cgv.py                  # implementation cụ thể cho CGV (Playwright)
└── tests/
    ├── test_scoring.py
    ├── test_state.py
    ├── test_control.py
    └── test_providers_cgv.py
```

### `types.py`

Kiểu dữ liệu chung, không rạp nào (CGV/BHD/...) được phép rò vào các module khác ngoài
`providers/<tên_rạp>.py`:

- `Cinema(id, name, city, provider)`
- `Showtime(id, movie, cinema, start_time, date)`
- `Seat(label, row, col, zone, price, status)` — `zone` là enum chung: `STANDARD | VIP | SWEETBOX`
  (mỗi provider tự map ký hiệu riêng của họ, ví dụ CGV's `"Thường"/"Vip"/"Sw"`, sang enum này)
- `SeatMap(rows, cols, seats)`
- `LockResult(success, hold_expiry, payment_url, error)`

### `provider.py` — interface `CinemaProvider`

```
is_logged_in() -> bool
list_cinemas() -> list[Cinema]
list_showtimes(cinema, movie_query, date_range) -> list[Showtime]
get_seat_map(showtime) -> SeatMap
lock_seats(showtime, seats) -> LockResult
```

Tất cả các module khác (`scoring.py`, `state.py`, `control.py`, `telegram_bot.py`) chỉ được gọi
qua interface này — không import bất kỳ thứ gì từ `providers/cgv.py` trực tiếp. Nhờ vậy khi thêm
BHD/Beta/Cinestar/Galaxy sau này, chỉ cần viết thêm một file `providers/<tên>.py` implement đúng
interface rồi đăng ký vào registry trong `control.py`, không phải sửa logic chấm điểm/orchestration
hiện có. (Việc research cơ chế đặt vé của các rạp này để lại cho một phiên làm việc sau — không
viết code "khống" cho các provider chưa research, tránh over-engineering.)

## Kết quả research cơ chế đặt vé CGV (dùng Chrome DevTools MCP, phiên đăng nhập thật của bạn)

- Đăng nhập tại `/default/customer/account/login/...` yêu cầu captcha ảnh — luôn do người dùng
  tự làm, không tự động hoá.
- Trang chọn ghế `/default/cinemas/booking/tickets/site/<site>/seq/<seq>/dy/<yyyymmdd>/` render
  toàn bộ sơ đồ ghế bằng HTML lúc load trang (không có API JSON riêng) — mỗi ghế là `<div class="seat ...">`
  với attribute `zone` (`"Thường"|"Vip"|"Sw"`), `loc` (mã nội bộ), `price`. Ghế đã bán/không chọn được
  có class `seat-disable disable` và không có các attribute trên.
- Chọn ghế là xử lý client-side thuần JS (hàm `selectedseats()`, biến toàn cục `box`), **không** có
  API "lock" riêng cho mỗi click.
- Bấm "Tiếp theo" kiểm tra luật `checkleftright()` — không cho phép chừa đúng 1 ghế trống lẻ ở cạnh
  trái/phải của cụm ghế đã chọn — rồi hiện hộp xác nhận tuổi (13+/16+). Xác nhận "Đồng ý" mới gọi
  `POST /default/cinemas/booking/ajaxadd/` với `{product, seq, site, dy, box}` — đây là lệnh **giữ
  ghế thật** (tương đương `toggleSeatLock` của xeca). Response có `result.success`,
  `result.apply.same_zone`, `result.apply.status`.
- Bước combo (tuỳ chọn) gọi tiếp `POST /default/cinemas/booking/ajaxupdate/`, thành công thì
  chuyển sang `/default/cinox/sales/payment/` — bước thanh toán.
- Không thấy `form_key`/CSRF token riêng cho các lệnh trên — chỉ dựa vào session cookie.
- Cookie `TS...` (dạng hex) là dấu hiệu đặc trưng của F5 BIG-IP/WAF — gợi ý có thể có thêm lớp
  chống-bot ngoài captcha đăng nhập; **chưa xác nhận** nó có chặn browser automation thật hay không
  (vì ta luôn dùng session đã đăng nhập hợp lệ qua trình duyệt thật).
- Danh sách rạp theo thành phố lấy qua `/default/cinox/site/` (click vào thành phố, ví dụ
  `id="cgv_city_3"` = Hà Nội) → mỗi rạp có `id="cgv_site_<mã>"` và slug (ví dụ
  `cgv-vincom-royal-city`), dẫn tới trang lịch chiếu `/default/cinox/site/<slug>` — trang này có
  bộ chọn ngày + danh sách phim/giờ chiếu, là nguồn cho `list_showtimes()`.
- **Chưa xác nhận**: thời hạn giữ ghế (hold expiry) sau khi `ajaxadd` thành công — nghiên cứu chưa
  đi tới bước xác nhận tuổi thật (để tránh tạo giữ-chỗ thật ngoài ý muốn, giống sự cố đã xảy ra với
  xeca). Cần xác nhận việc này ở Phase 3 bằng một lần thử thật, có kiểm soát.

## Quy tắc chấm điểm ghế (`scoring.py`)

Với sơ đồ ghế của một buổi chiếu, có `total_rows` hàng (tính từ màn hình ra sau, bỏ qua chữ `I`)
và `total_cols` cột (theo từng hàng):

- **Điểm theo chiều dọc**: đỉnh ưu tiên tại `peak_row = round(2/3 * total_rows)`. Hàm điểm **không
  đối xứng**: các hàng ở phía SAU đỉnh (xa màn hình hơn) bị trừ điểm ít hơn mỗi hàng so với các
  hàng ở phía TRƯỚC đỉnh (gần màn hình hơn) — nghĩa là giữa hai ghế cách đỉnh 2/3 một khoảng bằng
  nhau, ghế lùi về sau được ưu tiên hơn ghế tiến về màn hình.
  - `penalty_per_row_back = 0.4`, `penalty_per_row_front = 1.0` (chuẩn hoá theo số hàng ở mỗi phía),
    có thể tinh chỉnh qua test nhưng tỉ lệ bất đối xứng (sau nhẹ hơn trước) là yêu cầu bắt buộc.
- **Điểm theo chiều ngang**: tiêu chí chính là ghế có nằm trong **nửa giữa** của hàng không (từ cột
  `total_cols/4` đến `3*total_cols/4`) — ghế trong dải này luôn được ưu tiên hơn ghế ngoài dải,
  bất kể điểm dọc. Khoảng cách tới cột trung tâm là tiêu chí phụ (gần hơn = tốt hơn) — dùng để so
  ghế trong cùng dải, và quan trọng hơn, dùng để chọn ghế khi dải giữa đã hết (tự nhiên "quay về
  ưu tiên 2/3 dọc, rồi lan ra hai bên" mà không cần logic đặc biệt, vì đây chỉ là bước sort tiếp
  theo khi tiêu chí "trong dải giữa" không còn phân biệt được ghế nào).
- **Khoá sort (ưu tiên giảm dần)**: `(nằm_trong_dải_giữa, điểm_dọc, -khoảng_cách_tới_cột_trung_tâm)`.
- **Cụm ghế** (mặc định `quantity=2`, ví dụ 1 cặp): chỉ xét các cụm ghế liên tiếp cùng `zone`; loại
  bỏ ngay cụm nào khi giữ sẽ để lại đúng 1 ghế trống lẻ ở cạnh trái/phải (luật `checkleftright` lấy
  từ chính JS của CGV) — kiểm tra trước khi thử giữ ghế thật, tránh gọi API vô ích. Điểm của cụm =
  khoá sort tính trên cả cụm (ví dụ: trung bình hoặc ghế thấp điểm nhất trong cụm).
- **Cờ ưu tiên sweetbox** (đặt theo từng mục watchlist, người dùng tự chọn có bật hay không): nếu
  bật, lọc ứng viên về `zone == SWEETBOX` trước, áp cùng khoá sort ở trên; nếu không có cụm sweetbox
  hợp lệ đủ `quantity` ghế, **rơi về (fallback)** áp dụng cùng quy tắc trên toàn bộ ghế Thường/VIP —
  không bao giờ "đứng im chờ sweetbox mãi" trừ khi người dùng chủ động muốn vậy (hiện tại: luôn có
  fallback, theo yêu cầu đã xác nhận).
- `pick_best_block(seat_map, quantity, prefer_sweetbox)` trả `None` khi không có cụm hợp lệ nào —
  loop camp chỉ tiếp tục poll, không coi là lỗi.

## Ưu tiên ngày & ưu tiên rạp

- **Ưu tiên ngày**: khi một mục watchlist cho một khoảng ngày (không phải 1 buổi chiếu cố định),
  các ngày rơi vào **Thứ Hai** hoặc **Thứ Tư** được xếp hạng cao nhất (giá vé 2D thường rẻ nhất ở
  các ngày này), các ngày khác là phương án dự phòng theo thứ tự ngày gần nhất.
- **Ưu tiên rạp**: danh sách rạp có thứ tự ưu tiên, mặc định:
  1. CGV Vincom Royal City
  2. CGV Indochina Plaza Hà Nội
  3. CGV Vincom Bắc Từ Liêm
  4. Các rạp CGV còn lại ở Hà Nội (thứ tự bất kỳ)

  Người dùng có thể đổi thứ tự này qua lệnh Telegram (xem phần Watchlist & bot). Có sẵn lệnh in ra
  toàn bộ danh sách rạp CGV theo thành phố (lấy từ `/default/cinox/site/`) để người dùng chọn/xếp
  lại trọng số — không hard-code danh sách đầy đủ, luôn lấy trực tiếp từ trang khi cần liệt kê.
- `find_best_showtime()` trong `control.py` kết hợp hai tiêu chí trên: với mỗi rạp theo đúng thứ tự
  ưu tiên, thử các ngày trong khoảng đã cho, ưu tiên Thứ Hai/Thứ Tư trước — rạp ưu tiên cao hơn luôn
  được thử trước rạp ưu tiên thấp hơn (không trộn lẫn "rạp thấp hơn nhưng ngày đẹp hơn" lên trên
  "rạp cao hơn nhưng ngày thường" — thứ tự rạp là chính, ngày là phụ trong cùng một rạp).

## Số lượng ghế & xử lý sau khi giữ chỗ

- Số ghế mặc định mỗi mục watchlist: **2** (một cặp), có thể chỉnh qua tham số khi `/add`.
- Sau khi `lock_seats()` thành công (ghế đã được giữ tạm trên hệ thống CGV), bot coi đây là **kết
  quả thành công chính** cần báo ngay qua Telegram — vì ghế đã ở trạng thái giữ tạm (hold), người
  dùng có thể quyết định thanh toán hay bỏ qua (để hold tự hết hạn) tuỳ ý, giống hệt cách
  `xeca_telegram_bot.py` xử lý trạng thái `pending_payment` + lệnh `/paid`. Bot **không** tự động
  thanh toán — chỉ gửi link/trang thanh toán để người dùng tự hoàn tất nếu muốn, trong thời hạn giữ
  chỗ (thời hạn cụ thể của CGV **chưa được xác nhận**, xem mục Rủi ro/điểm còn mở).

## Watchlist & lệnh Telegram (`telegram_bot.py`)

Theo đúng khuôn mẫu đã có ở `xeca_telegram_bot.py`, có provider ở đầu mỗi lệnh cần chọn rạp:

- `/add cgv <tên phim> <khoảng ngày>` — thêm mục watchlist mới (mặc định quantity=2, không sweetbox).
- `/setquantity <id> <n>`
- `/setsweetbox <id> on|off`
- `/setcinemapriority <id> <rạp 1>, <rạp 2>, ...` (theo thứ tự ưu tiên, giống `/setpickup` của xeca)
- `/listcinemas <thành phố>` — in toàn bộ danh sách rạp CGV để tham khảo khi đặt ưu tiên
- `/list`, `/remove <id>`, `/status`, `/instant <id> on|off`, `/logs [n]`, `/paid <id>`, `/help`

## Các phase triển khai (đi kèm test)

1. **Scoring core** — `types.py`, `provider.py` (interface + `FakeProvider` test double dùng cho
   test), `scoring.py`. Toàn bộ pure function, không I/O. Test nhiều nhất ở đây: điểm dọc bất đối
   xứng, ưu tiên dải giữa + fallback lan hai bên, sweetbox + fallback, luật cấm ghế trống lẻ, chọn
   cụm ghế, các trường hợp biên (hết ghế, chỉ 1 hàng, ghế đúng tại đỉnh 2/3).
2. **State & orchestration** — `state.py`, `control.py`. Test bằng `FakeProvider` (trả về sơ đồ ghế
   giả lập thay đổi qua từng lần poll) — kiểm tra logic camp loop, xếp hạng ngày/rạp, chưa đụng tới
   trình duyệt/CGV thật.
3. **CGV provider** — `providers/cgv.py`: Playwright + profile bền, phát hiện bị đưa về trang login
   hoặc gặp trang lạ của WAF → báo người dùng, parse sơ đồ ghế từ DOM, luồng giữ ghế qua `ajaxadd`.
   Phần parse (DOM → `SeatMap`, JSON response → `LockResult`) được test bằng fixture đã lưu lại từ
   buổi research hôm nay — CI không gọi CGV thật. Có thêm một smoke-test thủ công (chỉ chạy khi bật
   cờ môi trường, không tự chạy) để bạn tự kiểm tra với session thật của mình, và để xác nhận thời
   hạn giữ chỗ còn đang bỏ ngỏ ở trên.
4. **Telegram bot & nối toàn bộ luồng** — `telegram_bot.py`, theo khuôn mẫu `xeca_telegram_bot.py`.
   Kiểm tra thủ công đầu-cuối (vì đụng tới tài khoản CGV thật của bạn), không tự động trong CI.

## Rủi ro / điểm còn mở

- Chưa xác nhận thời hạn giữ ghế (hold expiry) thực tế của CGV sau `ajaxadd` — cần một lần thử thật
  có kiểm soát ở Phase 3 để lấy số liệu (tương tự cách xeca xác định mốc ~20 phút qua
  `get-book-expired-time`).
- Chưa rõ hành vi chính xác của lớp WAF (F5) khi gặp truy cập bất thường ngoài phạm vi "trình duyệt
  thật, session hợp lệ" — thiết kế chủ động không cố vượt qua nó, chỉ dừng và báo người dùng nếu gặp
  trang lạ, nên rủi ro lớn nhất chỉ là "bot phải dừng và chờ người", không phải rủi ro kỹ thuật.
- Cơ chế đặt vé của BHD/Beta/Cinestar/Galaxy hoàn toàn chưa được research — interface `CinemaProvider`
  chỉ là chỗ neo cho việc đó, không giả định trước điều gì về các rạp này.
