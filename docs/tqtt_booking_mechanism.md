# TQTT.VN (Tổ Quốc Trong Tim) Registration Flow Notes

## Scope
Reverse-engineered from the production JS bundle of https://tqtt.vn
(`/assets/index-DW0AXM50.js`, ~1.05MB, Vite+React build) — used by `tqtt_client.py`,
`tqtt_watch.py` (Phase 1) and `tqtt_register.py` (Phase 2).

As of the capture date, the site only shows a "coming soon" page (background image +
embedded YouTube video) — the actual registration form only mounts once
`GET /concert/capacity` reports `is_open: true`. **This is a free registration form, not
a paid ticket purchase** — there is no seat lock / order / payment step, unlike the Xeca
bus-booking flow. A single `POST /concert/submit` is the entire flow.

Base URL: `https://api.tqtt.vn/api` (inlined into the bundle at build time as
`import.meta.env.VITE_API` — no auth header observed, public storefront API).

The axios instance (`i2`) is configured with `axios-retry`, retries: 3, retryDelay
`e=>e*1000` (linear backoff), and:
```js
retryCondition: e => {
  if (e.response) { const n = e.response.status; return n !== 409 && n !== 429 && n >= 500 }
  return isNetworkOrIdempotentRequestError(e)
}
```
i.e. it retries 5xx and network errors, but **never retries 409 (conflict) or 429 (rate
limited)** — the two statuses most likely to fire in a mass-submit race the instant the
form opens. A bot should mirror this: no retry loop on 409/429, just report the outcome.

## Capacity / open-status check — `GET /concert/capacity`
No params, no auth. Response:
```json
{ "result": { "capacity_valid": true, "is_open": false } }
```
- `is_open` — whether the registration form is open for submissions right now. This is
  the field to poll.
- `capacity_valid` — whether remaining capacity still allows new submissions (distinct
  signal; UI shows a "success"/"fail" banner based on it, independently of `is_open`).

The frontend calls this once on page mount (`useEffect(() => { Q() }, [])`) — no visible
polling from the page itself, so a mount-and-forget page won't auto-refresh; a bot must
poll this endpoint itself.

## Submit registration — `POST /concert/submit`
Content-Type: `application/json`. Body (all fields required except `priority_group`,
per the zod schema `nk` in the bundle):
```json
{
  "name": "Nguyễn Văn A",
  "email": "a@example.com",
  "identifier": "001234567890",
  "phone": "0912345678",
  "date_of_birth": "1995",
  "living_area": "Ha_noi",
  "ward": "267",
  "priority_group": "none",
  "agree_receive_info": true
}
```

Field validation (from the bundle's zod schema):
- `name` — non-empty string.
- `email` — non-empty, must pass email format check.
- `identifier` — CCCD/CMND number, length **6 to 12** chars (covers old 9-digit CMND and
  new 12-digit CCCD).
- `phone` — non-empty, matches `/\(?([0-9]{3})\)?([ .-]?)([0-9]{3})\2([0-9]{4})/` (a
  generic 10-digit grouped-triplet pattern, not VN-specific — a plain 10-digit VN mobile
  number like `0912345678` satisfies it).
- `date_of_birth` — **birth year only**, as a string (`"1995"`), not a full date. UI
  builds the dropdown as `1900..2026` reversed (latest year first).
- `living_area` — province `value` (e.g. `"Ha_noi"`), from the 34-province list. See
  `data/tqtt_provinces.json` (extracted from the bundle's `Wb` array — 34 entries, one
  per current merged province/city, each `{name_vi, name_en, value, slug, code}`).
- `ward` — ward/commune `value` (numeric string id, e.g. `"267"`), **must belong to the
  chosen province** — the frontend filters `MB.filter(k => k.parent_code === <province
  code>)`. See `data/tqtt_wards.json` (extracted from the bundle's `MB` array — 3321
  entries, `{name_vi, name_en, value, parent_code, slug}`; `parent_code` matches the
  province's `code`, NOT its `value`).
- `priority_group` — optional, one of:
  - `"revolutionary"` — "Tôi là người có công với cách mạng"
  - `"wheelchair_user"` — "Tôi là người sử dụng xe lăn"
  - `"none"` — "Tôi không thuộc nhóm ưu tiên nào"
  - Or omit entirely (schema marks it `.optional()`).
- `agree_receive_info` — boolean, defaults to `false` in the form.

Success response: `{"result": true}` at `data.result` (or any truthy value — the
frontend just checks `Bt.get(q, "data.result", null)` and treats any truthy value as
success). On failure, the frontend reads a localized error at
`response.data.error.message_<lang>` (e.g. `message_vi`, `message_en`).

## Practical bot strategy
1. Poll `GET /concert/capacity` on an interval (Phase 1, `tqtt_watch.py`) until
   `is_open: true`, then notify via Telegram (registration opening is a rare, ~one-time
   event per person — no seat-freeing/retry-camping dynamic like the bus booking case).
2. The instant `is_open` flips true, fire `POST /concert/submit` once (Phase 2,
   `tqtt_register.py`) with the pre-filled payload — do NOT loop-retry on 409/429 per the
   frontend's own retry policy; a 409 likely means capacity is already exhausted, a 429
   means to back off, not hammer harder.
3. Because this is a single free-form submission (not a seat hold with a payment
   countdown), there's no follow-up payment step to hand off to the user — success/failure
   is known immediately from the response.

## Confirmed live: submitting while closed
Tested with a real, correctly-shaped payload (valid province `Ho_chi_minh` / ward `1548`)
while `GET /concert/capacity` still reported `is_open: false`. Server-side check is fully
independent of the client — faking `is_open` in the browser (DevTools override,
Requestly, etc.) does NOT let a real submit through, since the server re-validates. Result:
```
HTTP/2 500
{"error":{"code":500,"status":"Internal Server Error","message_en":"Unexpected error occurred","message_vi":"Có lỗi bất ngờ xảy ra"}}
```
So a generic `500` (not a semantic 403/409) is what "not open yet" looks like server-side
— `tqtt_register.py`'s `SaleNotOpenError` should really be driven off `GET
/concert/capacity`'s `is_open` (as it already is) rather than trying to interpret a 500
from `/concert/submit` as "closed", since a 500 could also mean an actual server bug.

The CORS `access-control-allow-headers` response header lists `Access-Token`, `X-Api-Key`,
`X-Device-Id`, `recaptcha-token` etc. — but none of these appear anywhere in the frontend
bundle's actual request code for `/concert/submit`, so they're most likely generic
API-gateway config shared across all of `api.tqtt.vn`, not something this specific
endpoint requires. No reCAPTCHA integration found in the bundle (`grep -i recaptcha`
returns nothing).

## Open items not yet confirmed
- Exact shape of the 409/429 error bodies (not observed live — the form isn't open yet;
  the only live error captured so far is the generic 500-while-closed above).
- Whether `identifier` is validated against a real CCCD checksum server-side (client-side
  only checks length 6-12).
- Whether submitting twice with the same `identifier`/`phone`/`email` is rejected
  server-side (dedup) — not observable until the form opens.
