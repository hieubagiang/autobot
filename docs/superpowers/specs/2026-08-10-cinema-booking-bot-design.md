# Bot tự động giữ vé xem phim (Beta Cinemas, mở rộng đa rạp) — Design Spec

## Bối cảnh & mục tiêu

Xây dựng một bot tương tự `xeca_*` (đang dùng để tự động giữ vé xe khách Văn Minh) nhưng cho việc
giữ vé xem phim, thiết kế đủ trừu tượng để gắn thêm các chuỗi rạp khác (BHD, CGV, Cinestar, Galaxy
CineX Hà Nội Centre...) mà không phải viết lại phần lõi.

**Provider đầu tiên triển khai: Beta Cinemas**, không phải CGV. Đã research kỹ CGV trước (xem mục
riêng bên dưới, giữ lại làm tham khảo/tiền đề cho provider CGV sau này), nhưng quyết định bắt đầu
code với Beta vì đăng nhập đơn giản hơn hẳn (Facebook OAuth, không captcha — xem mục research Beta)
trong khi CGV có captcha ảnh + WAF F5 + session hay rớt, phù hợp làm provider thứ hai hơn là đầu tiên.

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
│   └── beta.py                  # implementation cụ thể cho Beta Cinemas (provider đầu tiên)
└── tests/
    ├── test_scoring.py
    ├── test_state.py
    ├── test_control.py
    └── test_providers_beta.py
```

(CGV sẽ là `providers/cgv.py` khi triển khai — để sau, xem mục research CGV bên dưới.)

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
qua interface này — không import bất kỳ thứ gì từ `providers/beta.py` (hay `providers/cgv.py` sau
này) trực tiếp. Nhờ vậy khi thêm BHD/CGV/Cinestar/Galaxy sau này, chỉ cần viết thêm một file
`providers/<tên>.py` implement đúng interface rồi đăng ký vào registry trong `control.py`, không
phải sửa logic chấm điểm/orchestration hiện có. CGV đã được research kỹ (xem mục riêng bên dưới)
nhưng chưa viết code — BHD/Cinestar/Galaxy thì chưa research gì cả. Không viết code "khống" cho
provider chưa cần tới, tránh over-engineering.

## Kết quả research cơ chế đặt vé Beta Cinemas (provider đầu tiên — dùng Chrome DevTools MCP)

- Nền tảng: ASP.NET WebForms cổ điển (`Ajax.aspx/<TênMethod>`, cookie `ASP.NET_SessionId`,
  `__AntiXsrfToken`), đứng sau **Cloudflare** (cookie `cf_clearance`) — khác hẳn CGV (F5).
- **Tra cứu rạp/phim/suất chiếu là API công khai, không cần đăng nhập, và gọi thẳng bằng HTTP
  thô cũng được** (đã kiểm chứng bằng `curl` trần, không cookie: `POST /Ajax.aspx/LoadShowtimesByFilm`
  với body `{"aData":["<cinemaId guid>","<filmId guid>","<tên phim>"]}` trả về `{"d": "<html suất
  chiếu>"}` — HTML nhúng trong JSON, chứa giờ chiếu + số ghế trống + tham số cho từng suất). Test lại
  với cookie session thật (lấy từ browser đã đăng nhập) cũng ra 200 bình thường — nhưng endpoint này
  vốn không cần đăng nhập nên chưa chứng minh được liệu một endpoint **cần session thật** (sơ đồ ghế/
  giữ ghế) có chấp nhận client không phải browser hay không — để lại xác nhận ở Phase khi viết
  `providers/beta.py`.
- Chọn rạp trên trang chủ qua `ChooseCinema(cinemaId_guid, tênRạp)` (biến JS toàn cục), chọn phim +
  mở popup lịch chiếu qua `viewsShowtimes(cinemaId, filmId, tênPhim, tênRạp)`. Bấm vào 1 suất chiếu
  gọi `bookingSeat(tênRạp, filmSessionId_guid, showId_guid, giờ, ngày, tênPhim, loạiPhòng, ...)`, xây
  URL `/chon-ghe.htm?f=<filmSessionId>&s=<showId>` rồi trigger một fancybox popup "Đặt vé" (thay vì
  cần đợi popup, có thể navigate thẳng tới URL này).
- Trang chọn ghế (`/chon-ghe.htm?...`) **yêu cầu đăng nhập** — chưa đăng nhập thì bị redirect về
  `/login.htm#login?referer=...`, giống hệt CGV.
- **Khác biệt lớn nhất so với CGV: form đăng nhập KHÔNG có captcha** — chỉ Email + Mật khẩu, cộng
  thêm nút "ĐĂNG NHẬP BẰNG FACEBOOK" (`loginByFacebook()`). Với tài khoản Facebook đã từng cho phép
  app Beta Cinemas trước đó (trường hợp của bạn), luồng Facebook chỉ là: mở popup OAuth của Facebook
  → Facebook hiện màn hình "Bạn từng đăng nhập bằng Facebook, tiếp tục?" → bấm "Tiếp tục dưới tên
  <tên>" → popup tự đóng, tab gốc có session Beta ngay. Không có bước xác minh nào (không 2FA, không
  captcha) trong nhánh happy-path này — nếu Facebook từng hiện bất kỳ thử thách nào khác (2FA, nhập
  lại mật khẩu vì session Facebook hết hạn, checkpoint bảo mật) thì bot phải dừng và báo người dùng,
  cùng nguyên tắc như CGV — không tự động xử lý các bước đó.
- Sơ đồ ghế có **5 trạng thái** (nhiều hơn CGV): "Ghế trống", "Ghế đang chọn", "Ghế đang giữ" (đang
  bị giữ tạm — bởi ai đó, có thể là do một khách khác đang giữ ghế cùng lúc), "Ghế đã bán", "Ghế đặt
  trước".
- Session cũng rớt khá thường xuyên trong lúc research (phải đăng nhập lại nhiều lần) — giống CGV,
  cần `is_logged_in()` kiểm tra chủ động và xử lý mất session một cách bình thường, không giả định
  session sống lâu.

### Addendum 2026-08-10 (Task 12 — live spike, đã xác nhận)

Nghiên cứu trực tiếp trên một suất chiếu ít khách (Beta Tây Sơn, "Người Nhện: Khởi Đầu Mới",
08:10 13/08/2026 — 2 ngày sau, sáng sớm, ~171/213 ghế trống lúc kiểm tra), đã giữ + nhả 2 ghế
thật (A1, A2) để xác nhận toàn bộ luồng rồi giải phóng lại ngay, seat map xác nhận đã trở về
`seat-empty` — không để lại ảnh hưởng.

- **Cấu trúc DOM ghế**: mỗi ghế là `<div class="seat-cell {trạng_thái} {loại}">` với các
  `data-seat-*` attribute (`data-seat-name` vd `"A1"`, `data-seat-index` — số thứ tự toàn cục dùng
  để gọi API, KHÔNG phải số ghế in trên ghế, `data-seat-row` — số hàng 0-based, `data-seat-price`,
  `data-seat-type` — `seat-normal`/`seat-vip`/`seat-double`, `data-seat-type-id` guid) và một
  `onclick="SeatOnclick({...json...}, this)"` chứa toàn bộ metadata ghế (giá, loại vé, v.v.).
  `seat-double` là ghế đôi ("sweetheart") — tương đương SWEETBOX; 2 seat-cell (`data-seat-index`
  liền nhau, liên kết qua `SeatIndexRelation`) tạo thành 1 ghế đôi vật lý, ô thứ 2 hiển thị nhãn
  gộp `"L1 - L2"`.
- **Ghế không phải ghế thật** (loại trừ khi parse, giống "Q88" của CGV):
  `StatusClass:"seat-for-way"` (khoảng trống/lối đi giữa các ghế) và `StatusClass:"seat-broken"`
  (ví dụ nhãn `"Lối vào"` = lối vào, không phải ghế) — cả hai có `SeatSoldStatus:0` và không phải
  ghế có thể đặt. Ghế thật có `StatusClass:"seat-used"`.
- **5 class trạng thái ghế thật** (xác nhận bằng cách đọc trực tiếp JS của trang, hàm xử lý
  callback SignalR broadcast trạng thái ghế — không phải đoán): `seat-empty` (trống — xác nhận
  sống bằng ghế thật A1/C1/L1...), `seat-select` (đang chọn bởi chính mình — xác nhận sống bằng
  cách tự chọn 1 ghế và quan sát class đổi), `seat-hold` (đang giữ — ứng với
  `SEAT_SALE_STATUS.WAITINGPAY` trong code, **chưa có ví dụ sống** vì suất test không có ghế nào ở
  trạng thái này, chỉ xác nhận qua đọc source), `seat-sold` (đã bán — ứng với
  `SEAT_SALE_STATUS.BOOKED` hoặc cao hơn, **chưa có ví dụ sống**, chỉ xác nhận qua đọc source).
  "Ghế đặt trước" (đặt-trước) chưa xác nhận được tên class tương ứng — không thấy nhánh xử lý riêng
  trong đoạn JS đã đọc, có thể trùng với `seat-sold` hoặc là một trạng thái hiếm.
- **Endpoint giữ ghế thật — đã xác nhận sống**: `POST /Ajax.aspx/SelectSeat`, body
  `{"aData": ["<seatIndex>", "<showId>", "<customerId>"]}` (3 chuỗi — `seatIndex` là
  `data-seat-index` dạng string, `showId` là tham số `s` trên URL, `customerId` là GUID khách hàng
  đang đăng nhập — đọc trực tiếp từ biến JS `customerId` trên trang, KHÔNG phải id riêng của ghế).
  Response: `{"d": "{\"SeatIndex\":N,\"SeatStatus\":1,\"IsYourSeat\":true}"}` (chuỗi JSON lồng
  trong JSON, cần parse 2 lần). `IsYourSeat:true` = giữ thành công.
- **Endpoint nhả ghế — đã xác nhận sống**: `POST /Ajax.aspx/ReturnSeat`, cùng hình dạng payload
  (`[seatIndex, showId, customerId]`), response `{"d": "{\"SeatIndex\":N,\"SeatStatus\":1,
  \"IsYourSeat\":false}"}`. Đã dùng để nhả 2 ghế test ngay sau khi xác nhận — xác nhận qua reload
  seat map rằng ghế trở lại `seat-empty` thật.
- **Thời hạn giữ ghế — khác hẳn cơ chế của CGV**: không có đồng hồ đếm riêng cho từng ghế đã giữ.
  Thay vào đó có một đồng hồ đếm ngược **10 phút cho toàn bộ trang `chon-ghe.htm`**
  (`time_in_minutes = 10`, khởi tạo lại từ đầu mỗi lần hàm `init()`/`countDownTimer()` chạy — tức
  mỗi lần trang load), hiển thị "Thời gian còn lại". Hết giờ thì `window.location = "/"` (chuyển
  hướng về trang chủ) — **không thấy gọi `ReturnSeat` tự động khi hết giờ** trong đoạn JS đã đọc,
  nghĩa là việc nhả ghế khi hết giờ (nếu có) phải do server tự làm ở phía sau, không phải do
  client chủ động gọi như lúc bấm bỏ chọn. Thời hạn thật của server cho một ghế đã `SelectSeat`
  (độc lập với đồng hồ hiển thị ở client) **vẫn chưa được đo chính xác** — code đếm ngược có một
  đoạn dùng `localStorage` để lưu deadline nhưng đã bị comment-out (dead code), nên đồng hồ hiển
  thị luôn tính lại "bây giờ + 10 phút" mỗi lần hàm chạy, không đáng tin cậy làm cơ sở tính
  `LockResult.hold_expiry` chính xác — **tạm dùng 10 phút làm giá trị ước lượng** cho
  `hold_expiry`, cần xác nhận lại bằng một lần đo có kiểm soát riêng nếu cần độ chính xác cao hơn.
- **Luật ràng buộc chọn ghế kiểu `checkleftright`**: **chưa kiểm chứng** — không có thời gian/rủi ro
  phù hợp để test trên suất ít khách đã chọn (test này cần chọn 1 ghế đơn lẻ chừa đúng 1 ghế trống
  cạnh bên rồi xem site có chặn không, rủi ro thao tác nhiều hơn giữ+nhả đơn giản). Hàm
  `leaves_isolated_gap` đã viết (Task 5) vẫn nên giữ nguyên khi tích hợp — nếu Beta không có luật
  này, hàm chỉ đơn giản không loại bỏ thêm ứng viên nào, không gây sai.
- **Phát hiện thêm ngoài phạm vi research ban đầu**: trang dùng **SignalR** (`chooseseathub`,
  qua `/signalr/negotiate`, `/signalr/start`, `/signalr/send`) để broadcast trạng thái ghế theo
  thời gian thực giữa các khách đang xem cùng suất chiếu — nghĩa là poll lại `get_seat_map` định kỳ
  vẫn hoạt động đúng (server luôn trả trạng thái mới nhất), SignalR chỉ là kênh cập nhật UI nhanh
  hơn cho người dùng thật, không bắt buộc bot phải dùng.
- Không có `X-Requested-With`/CSRF token riêng ngoài cookie session cho `SelectSeat`/`ReturnSeat` —
  giống `LoadShowtimesByFilm`, chỉ dựa vào session cookie hiện tại.

## Kết quả research cơ chế đặt vé CGV (đã research kỹ, tạm hoãn triển khai — xem lý do ở đầu tài liệu)

> Giữ lại phần research này làm tham khảo cho `providers/cgv.py` khi tới lúc triển khai — không phải
> provider đang code hiện tại (xem "Bối cảnh & mục tiêu").

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
- **Đã xác nhận bằng 1 lần thử thật, có kiểm soát** (chọn ghế A20/Thường tại suất 22:10 10/08/2026,
  Cinema 7 — không phải suất/ghế "đẹp" để giảm ảnh hưởng tới khách thật, và đã giải phóng lại ngay
  sau khi đo được số liệu): thời hạn giữ ghế (hold) sau khi `ajaxadd` thành công là **CHÍNH XÁC 5
  PHÚT**, tính hoàn toàn ở client-side (`countDownDate.setMinutes(getMinutes() + 5)`) — ngắn hơn
  rất nhiều so với mốc ~20 phút của xeca. Khi đồng hồ về 0, trang tự gọi
  `POST /default/cinemas/booking/ajaxdelete/` (không cần body, chỉ cần session cookie hiện tại) để
  nhả ghế — bot có thể gọi endpoint này chủ động để hủy giữ chỗ sớm (đã kiểm chứng: gọi ngay sau khi
  `ajaxadd` được ~1 phút, seat map load lại cho thấy ghế A20 trở lại `seat-standard active`, không
  còn bị giữ).
  - **Tác động tới thiết kế**: 5 phút là RẤT NGẮN so với toàn bộ luồng chọn combo → xác nhận tuổi →
    thanh toán. `instant_camp_loop` phải coi "khoá ghế thành công" là điểm báo Telegram NGAY, và
    người dùng phải quyết định thanh toán trong vòng 5 phút đó — không có nhiều thời gian chờ như
    với xe khách. Nên cân nhắc thêm bước tự động bấm tiếp tới trang thanh toán (lấy `payment_url`)
    ngay trong lúc khoá ghế, để đồng hồ 5 phút của người dùng bắt đầu từ lúc họ nhận được link, gần
    hết mức có thể với 5 phút thật của CGV (chi tiết luồng combo→payment cụ thể vẫn để research ở
    Phase 3 khi viết `providers/cgv.py`, tài liệu này chỉ cần biết mốc 5 phút để thiết kế đúng).
  - `LockResult.hold_expiry` nên được tính bằng `thời điểm ajaxadd thành công + 5 phút` (client-side
    only, không có field expiry riêng trong response `ajaxadd`) — cần re-xác nhận nếu CGV thay đổi
    giá trị này trong tương lai, vì đây là hằng số hard-code trong JS của họ, không phải cấu hình
    trả về từ server.

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

- **Ngày mục tiêu — hỗ trợ cả 2 kiểu**: `/add` nhận một ngày đơn (vd `12/08/2026`) HOẶC một khoảng
  ngày (vd `10/08/2026-20/08/2026`). Ngày đơn được coi là khoảng ngày chỉ gồm 1 ngày — không áp
  ưu tiên Thứ Hai/Thứ Tư vì không có gì để xếp hạng (đây là trường hợp "tôi biết chắc muốn xem
  ngày nào rồi"). Với khoảng ngày thật (nhiều hơn 1 ngày), các ngày rơi vào **Thứ Hai** hoặc
  **Thứ Tư** được xếp hạng cao nhất (giá vé 2D thường rẻ nhất ở các ngày này), các ngày khác là
  phương án dự phòng theo thứ tự ngày gần nhất. Cả 2 kiểu dùng chung một trường `date_range` trong
  `state.py` (`[ngày, ngày]` khi là ngày đơn) — `find_best_showtime()` không cần biết đang xử lý
  kiểu nào, chỉ xếp hạng danh sách ngày nó nhận được (danh sách 1 phần tử thì xếp hạng cũng chỉ ra
  đúng 1 kết quả).
- **Ưu tiên rạp**: danh sách rạp có thứ tự ưu tiên. Với Beta Cinemas, mặc định hiện tại chỉ có:
  1. Beta Tây Sơn (rạp ưu tiên duy nhất được xác nhận cho tới nay)
  2. Các rạp Beta khác (thứ tự bất kỳ, mở rộng sau nếu cần)

  (Với CGV — để sau — thứ tự ưu tiên đã thống nhất trước đó là Vincom Royal City > Indochina Plaza
  Hà Nội > Vincom Bắc Từ Liêm > rạp CGV còn lại, giữ nguyên trong tài liệu để dùng lại khi tới lúc.)

  Người dùng có thể đổi thứ tự này qua lệnh Telegram (xem phần Watchlist & bot). Có sẵn lệnh in ra
  toàn bộ danh sách rạp theo thành phố (Beta: từ dropdown chọn rạp trên trang chủ; CGV: từ
  `/default/cinox/site/`) để người dùng chọn/xếp lại trọng số — không hard-code danh sách đầy đủ,
  luôn lấy trực tiếp từ trang khi cần liệt kê.
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
  chỗ — đã đo được thực tế là **5 phút** (xem mục "Kết quả research cơ chế đặt vé CGV" ở trên), rất
  ngắn (với CGV, xem mục research CGV) — nên Telegram phải báo ngay lập tức. Với Beta Cinemas, thời
  hạn giữ ghế **chưa được đo** — cần xác nhận khi viết `providers/beta.py` (Phase 3), tạm thiết kế
  theo hướng báo ngay lập tức giống CGV cho tới khi có số liệu thật.

## Watchlist & lệnh Telegram (`telegram_bot.py`)

Theo đúng khuôn mẫu đã có ở `xeca_telegram_bot.py`, có provider ở đầu mỗi lệnh cần chọn rạp:

- `/add beta <tên phim> <ngày đơn hoặc khoảng ngày>` — thêm mục watchlist mới (mặc định quantity=2,
  không sweetbox). Vd `/add beta "Người Nhện" 12/08/2026` (1 ngày cụ thể) hoặc
  `/add beta "Người Nhện" 10/08/2026-20/08/2026` (khoảng ngày, bot tự xếp hạng theo ưu tiên Thứ 2/Thứ 4).
  (Cú pháp giữ nguyên `<provider> ...` ở đầu để dùng lại cho `cgv` hoặc rạp khác sau này.)
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
3. **Beta Cinemas provider** — `providers/beta.py`: phần tra cứu rạp/phim/suất chiếu gọi thẳng API
   công khai (HTTP thô, không cần Playwright — đã xác nhận hoạt động). Phần đăng nhập (Facebook OAuth
   continue) và mọi thứ sau đăng nhập (đọc sơ đồ ghế thật, giữ ghế) dùng Playwright + profile bền,
   phát hiện bị đưa về `/login.htm` hoặc gặp bất kỳ bước xác minh Facebook lạ nào (2FA, checkpoint) →
   báo người dùng, không tự xử lý. Cần xác nhận trong phase này: cấu trúc DOM/attribute của từng ghế,
   endpoint giữ ghế thật, thời hạn giữ ghế, và luật ràng buộc chọn ghế (nếu có) — tất cả đang là điểm
   mở (xem mục research Beta). Phần parse được test bằng fixture lưu lại từ buổi research — CI không
   gọi Beta thật. Có thêm smoke-test thủ công (chỉ chạy khi bật cờ môi trường) để tự kiểm tra với
   session thật.
4. **Telegram bot & nối toàn bộ luồng** — `telegram_bot.py`, theo khuôn mẫu `xeca_telegram_bot.py`.
   Kiểm tra thủ công đầu-cuối (vì đụng tới tài khoản Beta Cinemas thật của bạn), không tự động trong CI.

*(CGV, và các rạp khác, trở thành `providers/cgv.py`/`providers/<tên>.py` bổ sung sau — không phải
Phase 3, để dành cho khi cần mở rộng, xem mục research CGV để tái sử dụng.)*

## Rủi ro / điểm còn mở

**Beta Cinemas (provider đang triển khai):**

- ~~Chưa xác nhận cấu trúc DOM/attribute của từng ghế, endpoint giữ ghế thật~~ — **đã xác nhận
  2026-08-10** bằng 1 lần thử thật có kiểm soát (giữ + nhả ngay 2 ghế trên suất ít khách): xem
  addendum trong mục research Beta ở trên (`SelectSeat`/`ReturnSeat`, cấu trúc `data-seat-*`, 5
  class trạng thái — 2 trong 5 mới xác nhận qua đọc source, chưa có ví dụ sống). Vẫn còn mở: thời
  hạn giữ ghế thật ở server (đồng hồ 10 phút chỉ là hiển thị client, không đáng tin cậy 100%), và
  luật ràng buộc kiểu `checkleftright` (chưa test) — để lại cho khi viết `providers/beta.py` thật
  (Task 15) nếu cần độ chính xác cao hơn.
- Chưa xác nhận liệu các endpoint **cần session thật** (không phải endpoint tra cứu công khai) có
  chấp nhận gọi bằng HTTP thô (không qua browser) hay không — an toàn nhất là giả định cần Playwright
  cho tới khi kiểm chứng được, không assume.
- Session rớt khá thường xuyên trong lúc research (phải đăng nhập lại Facebook nhiều lần) — cần
  `is_logged_in()` chủ động và xử lý mất session mượt mà (không giả định session sống lâu), tương tự
  CGV.

**CGV (đã research kỹ, để dành cho khi mở rộng):**

- ~~Chưa xác nhận thời hạn giữ ghế (hold expiry) thực tế sau `ajaxadd`~~ — **đã xác nhận 2026-08-10**
  bằng 1 lần thử thật có kiểm soát: đúng 5 phút, client-side, tự nhả qua
  `POST /default/cinemas/booking/ajaxdelete/` (xem mục "Kết quả research cơ chế đặt vé CGV"). Vẫn
  còn mở: 5 phút này có được server-side gia hạn/xác nhận độc lập khỏi client không (tức nếu người
  dùng tắt tab ngay sau khi khoá ghế, liệu ghế có nhả đúng lúc 5 phút hay chờ session timeout dài
  hơn) — cần kiểm tra khi tới lúc viết `providers/cgv.py`.
- Chưa rõ hành vi chính xác của lớp WAF (F5) khi gặp truy cập bất thường ngoài phạm vi "trình duyệt
  thật, session hợp lệ" — thiết kế chủ động không cố vượt qua nó, chỉ dừng và báo người dùng nếu gặp
  trang lạ, nên rủi ro lớn nhất chỉ là "bot phải dừng và chờ người", không phải rủi ro kỹ thuật.

**Chưa research gì cả:** BHD, Cinestar, Galaxy CineX Hà Nội Centre — interface `CinemaProvider` chỉ
là chỗ neo cho việc đó, không giả định trước điều gì về các rạp này.
