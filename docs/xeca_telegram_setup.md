# Tạo Telegram bot để nhận thông báo vé xe

1. Mở Telegram, tìm **@BotFather**, nhắn `/newbot`.
2. Đặt tên hiển thị (bất kỳ) rồi đặt username kết thúc bằng `bot` (vd `xeca_watch_bot`).
3. BotFather trả về một dòng token dạng `123456789:AA...` — đây là `TELEGRAM_BOT_TOKEN`.
4. Lấy `TELEGRAM_CHAT_ID`:
   - Mở chat với bot vừa tạo, bấm **Start** (bắt buộc phải nhắn ít nhất 1 tin trước).
   - Mở trình duyệt, truy cập:
     `https://api.telegram.org/bot<TOKEN>/getUpdates`
   - Tìm field `"chat":{"id": ...}` trong JSON trả về — đó là `TELEGRAM_CHAT_ID` của bạn.
   - (Cách khác: nhắn cho bot @userinfobot để bot đó trả về chat id của chính bạn — dùng được
     nếu id đó trùng với id bạn dùng để chat với bot mới tạo.)
5. Copy `.env.example` thành `.env` rồi điền:
   ```
   TELEGRAM_BOT_TOKEN=123456789:AA...
   TELEGRAM_CHAT_ID=123456789
   ```
6. Test nhanh bằng script:
   ```bash
   python xeca_ticket_watch.py --depart-date 20260808 --once
   ```
   Ngày `20260808` (ví dụ) là ngày thực tế đã mở bán tại thời điểm viết doc này — nếu chạy đúng
   bạn sẽ nhận được tin nhắn "🎉 ĐÃ MỞ BÁN VÉ!" trên Telegram trong vài giây.
