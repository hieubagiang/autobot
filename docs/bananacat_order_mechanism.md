# BANANACAT.STORE Order Flow Notes

## Scope
This document summarizes the authenticated staff order page flow for the boosting orders screen.

## Login / session
- Login page: `https://bananacat.store/login`
- Protected staff page: `https://bananacat.store/staff/orders/boostings`
- Page requires valid session cookies:
  - `XSRF-TOKEN`
  - `laravel_session`
- The login form includes a remember-me checkbox:
  - selector: `#remember_me` or `input[name="remember"]`

## Observed page state after login
- Title: `- Admin Control Panel v3`
- Current section: `Đơn hàng Cày thuê - Đã nhận`
- The page renders a table of orders plus one modal per order.

## Claim order mechanism
The page includes a dedicated JavaScript function:
- `claimOrder(id)`

Observed behavior:
1. Shows a Swal confirmation dialog.
2. If confirmed, sends:
   - method: `POST`
   - endpoint: `https://bananacat.store/staff/orders/boostings/claim`
   - body: `{ id }`
3. On success, reloads the page.

Conclusion:
- “Nhận đơn” is handled by the `claim` endpoint, not by the update form.

## Update / completion mechanism
Each order has an update modal, for example:
- modal id: `modal-edit-2418`
- form action: `https://bananacat.store/staff/orders/boostings/update?id=2418`
- method: `POST`
- token field: `_token`

Inside the modal:
- `status` select contains:
  - `Assigned` = `Đã nhận đơn`
  - `Processing` = `Đang xử lý`
  - `Completed` = `Hoàn thành`
  - `Cancelled` = `Đã hủy / Hoàn`

Conclusion:
- The update form is used to change order status and notes.
- The `claim` endpoint is used specifically to receive/claim an order.

## Relevant scripts loaded by the page
Observed script sources:
- `https://bananacat.store/_admin/js/main.js`
- `https://bananacat.store/build/assets/app-a39774fb.js`
- `https://bananacat.store/build/assets/functions-535f204f.js`
- `https://code.jquery.com/jquery-3.6.1.min.js`
- `https://bananacat.store/_admin/js/datatables.js`

## Notable client-side behavior
- `functions-535f204f.js` configures `axios` with CSRF headers.
- `.axios-form` submits via `axios(...)` and supports reload after success.
- `claimOrder(id)` uses `axios.post('/staff/orders/boostings/claim', { id })`.

## Automation idea
To automate staff order claiming:
1. Login.
2. Load `/staff/orders/boostings`.
3. Detect claimable orders.
4. Call `POST /staff/orders/boostings/claim` with the order id.
5. Refresh and repeat.

To automate order completion:
1. Open the modal or post directly.
2. Submit `POST /staff/orders/boostings/update?id=...`.
3. Set `status=Assigned` / `Processing` / `Completed` as required.

## Notes
- The page is behind HTTPS and may trigger certificate errors in some Chrome setups.
- In the local script, remember-me was enabled before login.
