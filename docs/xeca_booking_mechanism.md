# XECA.VN (Văn Minh) Booking Flow Notes

## Scope
Reverse-engineering notes for the public booking API behind https://vanminh.xeca.vn, used by
`xeca_client.py`, `xeca_ticket_watch.py` (Phase 1) and `xeca_auto_book.py` (Phase 2).

Base URL: `https://api-pro.xeca.vn/v1`. All requests are unauthenticated GETs (public storefront
API) and require these headers/params:
- `origin: https://vanminh.xeca.vn`, `referer: https://vanminh.xeca.vn/`
- `x-bus-agency-id: 1`
- query params `_source=wb&_client_id=<uuid>` — `_client_id` looks like a per-browser-session
  identifier; kept stable for one run via `XecaClient`.

## Route search — `GET /bus-times`
Params: `departDate=YYYYMMDD&fromProvinceId=<id>&toProvinceId=<id>&sourceChannel=11`.

Returns `data.busTimes[]`, each with `id` (bus_time_id), `bus_hop_id`, `bus_stage_id`,
`start_time`, `price`, `empty_seat`, `bus_type_name`, `hop_name`, `bus_stage_name`, etc.
Known province ids: Hà Nội=2, Hà Tĩnh=3.

`bus_type_name` distinguishes fare classes on the same route: `"Xe giường nằm"` (~340-370k,
regular sleeper) vs `"Limousine giường VIP"` (~530k). The user's preference is regular bus
first, Limousine only as a last resort if no regular departure has seats left — see
`select_preferred_bus_time()` in `xeca_client.py`. On a sample day there were regular-bus
departures as late as 23:12/23:14, so "prefer regular" and "prefer the latest time of day"
are usually both satisfiable, not a real trade-off.

Note: this endpoint does **not** reflect the "chưa mở bán" state — `online_status`/`status` stay
`1` and `empty_seat` looks normal even for a date that isn't on sale yet. The real signal is in
`detail-bus-time` (see below).

## Sale-open check — `GET /bus-time-exts/detail-bus-time`
Params: `depart_date, bus_time_id, bus_hop_id, bus_stage_id, from_province_id, to_province_id`.

Returns `data.buxTimeExt` (bus/trip detail) and `data.seatMap`, plus the key field:

`data.busStageSpecialRules[]` — list of rules, each like:
```json
{
  "bus_stage_id": -1,
  "from_date": 20260826,
  "to_date": 20310101,
  "not_allow_book": 1,
  "not_allow_sell": 1,
  "book_msg": "Công ty Văn Minh chưa mở bán vé, xin quý khách thông cảm. Xin cảm ơn"
}
```
- `bus_stage_id = -1` means the rule applies globally to the whole hop, not one stage.
- A rule blocks `depart_date` when `from_date <= depart_date <= to_date` AND
  (`not_allow_book` or `not_allow_sell`) is truthy.
- When sale is open for a date, `busStageSpecialRules` is `[]` (confirmed by comparing a date
  2 days out, which was open, vs 21 days out, which was blocked).
- Observed `upd_date_time` on the blocking rule was stamped the same morning we checked
  (~08:37 local time) — the agency appears to push `from_date` forward by ~1 day, once per day,
  around 08:00–09:00. Not confirmed over multiple days yet; treat as a hint for tightening the
  poll interval in Phase 2, not a hard guarantee.

`xeca_client.is_sale_open(special_rules, depart_date, bus_stage_id)` implements this check.

## Seat map (`data.seatMap`)
`objArea[]` — one entry per floor, `areaName` e.g. `"Tầng 1"`, `"Tầng 2"`. Each area has
`objRow[].objSeat[]` with:
- `seatDisplayName` — e.g. `"M3"`, `"S6"`, `"P-M1"` (the `P-` prefixed ones are auxiliary/foldable
  seats, `type: 4`, usually not preferred).
- `seatStatus`: `"empty" | "sold" | "lock"`.
- `type`: `1` = real seat, `4` = auxiliary seat.
- `price`.

**Seat letter/floor naming is bus-category specific.** The VIP 20-giường Limousine sampled
(bus_time_id 18311, bus_category_id 664) uses `M/V` on Tầng 1 and `S/L` on Tầng 2. The regular
"Xe giường nằm" bus (bus_time_id 17698, the one actually booked in the capture below) uses:
- Tầng 1: `B`, `D`, `F` (+ `P-B/P-D/P-F` auxiliary seats)
- Tầng 2: `A`, `C`, `E` (+ `P-A/P-C/P-E` auxiliary seats)

This matches the user's stated preference exactly: `E, A` (Tầng 2) preferred over `F, B`
(Tầng 1), with `C`/`D` (middle lane) excluded from the preference list entirely. Since
`select_preferred_bus_time()` already prefers "Xe giường nằm" over Limousine, this A/C/E,B/D/F
scheme is the one Phase 2 will hit in practice — but still re-fetch the seat map per bus_time
rather than hard-coding, in case other regular-bus categories differ.

## Pickup / drop-off points — `GET /boarding-points/pickup-drop-points`
Params: `busTimeId, type (1=pickup/home zone, 2=drop-off), seats, departDate, fromWeb=true`.

Each entry is either:
- A fixed stage point (`type: 3` in the response body, unrelated to the `type` query param):
  `boarding_point_id`, `boarding_point_name`, `bus_stage_detail_id`.
- A home-pickup zone (`type: 1` in the response body): `home_pickup_zone_id`,
  `home_pickup_zone_name`, `tranship_bus_time_detail_id` (needed later when creating the order).

Confirmed IDs for the Hà Nội → Hà Tĩnh route sampled:
- Pickup "493 Nguyễn Trãi": `home_pickup_zone_id=1085`, `tranship_bus_time_detail_id=40415`
  (province_id 436, "KV Mỹ Đình - HN").
- Drop-off "VP THẠCH HÀ - HT": `boarding_point_id=3`, `bus_stage_detail_id=49386`.
- Drop-off "XANH ĐỎ THẠCH LONG - HT" (used only for the "Ven biển HT - Quốc lộ 1 NA" route
  variant): **not found yet** — wasn't present in the drop-point list for bus_time_id 18311.
  Needs a live lookup against a bus_time whose `hop_name`/`bus_stage_name` actually is that
  coastal route, captured via Chrome DevTools (see below).

IDs (`tranship_bus_time_detail_id`, `boarding_point_id`) should be looked up by name each run
rather than hard-coded, since they can differ per `bus_time_id`.

## Directions (`xeca_client.DIRECTIONS`)
Two directions are supported, each with its own default pickup/drop-off names (confirmed by
the user directly, not scraped):
- `HN-HT` (Hà Nội → Hà Tĩnh): pickup "493 Nguyễn Trãi" (home zone), drop-off
  "VP THẠCH HÀ - HT" (fixed point); coastal-route drop-off override "XANH ĐỎ THẠCH LONG - HT".
- `HT-HN` (Hà Tĩnh → Hà Nội): pickup "VP THẠCH HÀ - HT" (fixed point), drop-off
  "Số 275 Nguyễn Trãi" (home zone); coastal-route **pickup** override
  "XANH ĐỎ THẠCH LONG - HT" (the coastal variant swaps out whichever endpoint is in
  Hà Tĩnh — the drop-off when HT is the destination, the pickup when HT is the origin).

Since a pickup/drop-off point can be either a home-pickup zone (`home_pickup_zone_id`) or a
fixed stage point (`boarding_point_id`) **regardless of which role (pickup/drop-off) it's
playing**, `xeca_client.pickup_fields()` / `dropoff_fields()` inspect the resolved point and
pick the right create-order field pair rather than assuming pickup=zone, drop-off=point (that
assumption only happened to hold for the one direction first captured).

## Booking + payment (captured via Chrome DevTools MCP on a live open-sale date)

> **Note:** capturing this flow on 2026-08-06 against the real (already on-sale) date
> 2026-08-08 actually created a live, unpaid order in Văn Minh's production system
> (order id `14013565`, ticket code `yPIoaDVw`, seat C3, phone `0364826228`, name
> "Nguyễn Văn Minh" — the test passenger info the user supplied). The flow was stopped at
> the VNPay payment-method-selection page; no card/bank details were entered and no payment
> was completed. The order should auto-expire and release the seat once the ~20 min
> transaction countdown (`get-book-expired-time`) runs out. Be aware of this side effect
> before re-running this capture for real.

### Seat lock — `POST /v1/tickets/toggleSeatLock`
Fired the moment a seat is clicked (before any passenger info is entered).
```json
// request
{"action":"lock","busHopId":2,"busTimeId":17698,"departDate":20260808,"seatIds":[29],"preStatus":"empty"}
// response
{"statusCode":200,"data":{"seatIds":[29],"locked":true}}
```
`action` is `"lock"` or `"unlock"`; `preStatus` tracks the seat's state before this call
(`"empty"` when first locking, `"book"` when releasing the lock after an order is created —
see below). `seatIds` are the numeric `seatId` values from the `detail-bus-time` seat map
(NOT the display name like `"C3"`).

### Transaction countdown — `GET /v1/orders/get-book-expired-time`
Called right after navigating to the checkout step.
```
?busTimeId=17698&departDate=20260808&numberOfTickets=1&startTime=23:14
// response
{"expiredTime":1786036862000,"canBookTicket":true,"canHoldTicket":false,"branchId":"900"}
```
`expiredTime` is a Unix ms timestamp — the hard deadline to complete `orders/book/web` +
payment before the seat lock is released. Observed ~20 minutes from checkout page load.

### Create order — `POST /brand-service/v1/orders/book/web`
Note the different base path (`/brand-service/v1/...`, not `/v1/...`).
```json
{
  "departDate": 20260808, "busTimeId": "17698", "busHopsId": "2",
  "couponUuid": 0, "discountId": 0, "paymentMethod": 3,
  "custId": 0, "custMobileNo": "0364826228", "custName": "Nguyễn Văn Minh",
  "custArriveAddr": "VP THẠCH HÀ - HT", "srcChannel": 11, "sendSms": false,
  "pickupType": null,
  "details": [{
    "seatId": 29, "custPickupAddr": "493 Nguyễn Trãi", "homePickupZoneId": 1085,
    "custBoardingPointId": null, "notes": "", "paymentType": 8,
    "arriveAddrDetail": "VP THẠCH HÀ - HT", "custMobileDetail": "0364826228",
    "custNameDetail": "Nguyễn Văn Minh", "custArriveAddr": "VP THẠCH HÀ - HT",
    "custArriveZone": null, "custArrivePointId": 3, "pickupType": 1,
    "custArriveType": 3, "isShip": 0, "custEmailDetail": ""
  }],
  "buyInsurance": false
}
```
Key fields to fill programmatically:
- `busTimeId` / `busHopsId` — from the chosen `bus-times` entry.
- `details[].seatId` — numeric seat id from the seat map (not the display name).
- `homePickupZoneId` — from `boarding-points?type=1`, matched by name (e.g. 1085 for
  "493 Nguyễn Trãi"); `custPickupAddr` is that zone's display name.
- `custArrivePointId` — from `boarding-points?type=2`, matched by name (e.g. 3 for
  "VP THẠCH HÀ - HT"); `custArriveAddr`/`arriveAddrDetail` is that point's display name.
- `paymentMethod: 3` and `details[].paymentType: 8` were the values sent for the "Thanh toán
  online" (VNPay) option — this route/session offered **only** online payment, no COD
  ("thanh toán khi lên xe") radio was present. COD may exist on other routes/agencies; verify
  per-route via the checkout page's "Phương thức thanh toán" section before assuming.
- Response was not captured (buffer expired) but must contain the new `orderId` (used
  immediately by the next call) and presumably the ticket code shown in the VNPay
  `vnp_OrderInfo` param (`"Ma ve: yPIoaDVw. SDT Khach: 0364826228"`).

### Initiate payment — `POST /payment-service/v1/payment`
```json
// request
{"orderId":14013565,"provider":"vnpay","returnUrl":"https://vanminh.xeca.vn/booking/booking/complete?ticket=14013565&type=1"}
```
Response not captured directly, but the browser was immediately redirected to:
```
GET https://pay.vnpay.vn/vpcpay.html?vnp_Amount=35000000&vnp_Command=pay&vnp_CreateDate=20260806165148
  &vnp_CurrCode=VND&vnp_IpAddr=...&vnp_Locale=vn
  &vnp_OrderInfo=Ma+ve%3A+yPIoaDVw.+SDT+Khach%3A+0364826228&vnp_OrderType=other
  &vnp_ReturnUrl=https%3A%2F%2Fvanminh.xeca.vn%2Fbooking%2Fbooking%2Fcomplete%3Fticket%3D14013565%26type%3D1
  &vnp_TmnCode=VANMINHW&vnp_TxnRef=yPIoaDVw_1_900&vnp_Version=2.1.0&vnp_SecureHash=...
```
i.e. the payment-service response almost certainly returns a `paymentUrl` field containing
this exact VNPay redirect URL (standard VNPay `vpcpay.html` integration — `vnp_Amount` is
the total in VND × 100, `vnp_TxnRef` embeds the ticket code, `vnp_SecureHash` is server-signed
and can't be constructed client-side). This redirects (302) to VNPay's own
`Transaction/PaymentMethod.html?token=...` page where the human picks a bank/wallet/QR method
— **this step cannot be automated further via API**; the script's job is to get this URL and
hand it to the user (e.g. via Telegram) to finish inside the countdown window.

### Seat unlock after order creation — `POST /v1/tickets/toggleSeatLock`
Fires automatically right after `orders/book/web` succeeds, transitioning the seat from
`"lock"` to `"book"` status (distinct from `"sold"`, which presumably follows a confirmed
payment):
```json
{"seatIds":[29],"action":"unlock","busHopId":2,"busTimeId":17698,"departDate":20260808,"preStatus":"book"}
```

### Open items still not covered
- The exact shape of the `payment-service/v1/payment` response (does it include a QR
  image/base64 in addition to `paymentUrl`? Does a Momo/COD provider value exist?).
- Whether there's a webhook/poll endpoint the `complete` page uses to confirm payment status
  after VNPay redirects back with `?ticket=<id>&type=1`.
- The real `home_pickup_zone_id` / `boarding_point_id` for the "Ven biển HT - Quốc lộ 1 NA"
  route variant and its "XANH ĐỎ THẠCH LONG - HT" point — not yet looked up against a bus_time
  actually running that variant (e.g. the 09:00 or 12:30 departures on 08/08/2026 both showed
  "(Ven biển HT- Quốc lộ 1 NA)" in their heading). This affects both directions.
- ~~`dropoff_fields()`'s home-zone branch (`custArriveZone`) is an educated guess~~ —
  **verified 2026-08-06** via a second live capture on `HT-HN` (pickup = fixed point
  "VP THẠCH HÀ - HT", drop-off = home zone "Số 275 Nguyễn Trãi"). This capture also caught
  a real bug: `pickupType`/`custArriveType` are NOT fixed constants (1/3) — they must
  mirror whichever kind of point is resolved (1=home-zone, 3=fixed-point), same as
  `homePickupZoneId`/`custBoardingPointId` and `custArrivePointId`/`custArriveZone`. The
  first (`HN-HT`) capture happened to have pickupType=1/custArriveType=3, which is why the
  original code hardcoded those values — they're now derived per-point in
  `pickup_fields()`/`dropoff_fields()` instead. Both directions' full create-order payload
  are now confirmed against real production requests.
