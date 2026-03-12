# like-blog-itviec

Auto like bài viết trên [itviec.com](https://itviec.com) sử dụng Selenium + undetected-chromedriver.

## Cài đặt

```bash
pip install -r requirements.txt
```

## Cấu hình

1. Sửa `CHROME_PATH` trong `itviec_google_login.py` trỏ đến `chrome.exe` của bạn
2. Tạo file `accounts.txt` với định dạng:

```
email1@gmail.com|password1
email2@gmail.com|password2
```

## Chạy

```bash
# Dùng file mặc định accounts.txt, 5 workers
python itviec_google_login.py

# Truyền file khác
python itviec_google_login.py accounts_2.txt

# Tuỳ chỉnh số Chrome chạy song song
python itviec_google_login.py accounts_2.txt --workers 3
```

## Tính năng

- ✅ Đăng nhập Google tự động
- ✅ Xử lý onboarding modal (tài khoản mới)
- ✅ Like bài viết, verify trạng thái trước khi tiếp tục
- ✅ Chạy song song nhiều Chrome cùng lúc
- ✅ Đánh dấu `DONE|` vào file accounts sau khi like thành công → retry an toàn
- ✅ Report kết quả cuối session
