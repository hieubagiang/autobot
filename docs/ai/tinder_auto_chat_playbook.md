# Tinder Auto Chat Playbook (Anh/em)

## Muc tieu
Playbook nay dung de tu dong hoa mot phan quy trinh chat Tinder theo huong:
- Ton trong doi phuong
- Khong thao tung
- Khong spam
- Uu tien ket noi that

## 1. Nguyen tac van hanh
- Xung ho: nguoi dung la "Anh", doi phuong nu la "em".
- Tin nhan ngan gon, de doc, 1 y chinh/tin.
- Moi luot chi gui tiep khi co tin hieu tuong tac tu em.
- Neu em khong quan tam, dung lich su.
- Khong gui noi dung nhay cam qua som.

## 2. Input profile can co
- Anh: nghe nghiep, so thich, khu vuc sinh song, lich ranh.
- Em: thong tin trong bio, anh, so thich, vi tri.
- Rang buoc: di ung vo hai san, hay di cafe.

## 3. State machine chat
1. `new_match`
- Muc tieu: mo cau chao ca nhan hoa.
- Hanh dong: gui 1 opener.

2. `engaged`
- Muc tieu: duy tri doi thoai 2 chieu.
- Hanh dong: 1 cau hoi mo + 1 thong tin ve Anh.

3. `warm`
- Muc tieu: xac nhan vibe.
- Hanh dong: de xuat mini-date nhe (cafe 45-60 phut).

4. `invite_sent`
- Muc tieu: cho phan hoi.
- Hanh dong: khong spam; chi follow-up 1 lan sau 24-48h.

5. `closed`
- Muc tieu: ket thuc dep.
- Hanh dong: cam on va dung.

## 4. Rule de quyet dinh next message
- Neu em rep nhanh + co noi dung: tiep tuc state `engaged`.
- Neu em dat cau hoi nguoc lai: nang state `warm`.
- Neu em rep ngan lap lai > 3 lan: chuyen `closed`.
- Neu em im lang > 48h: gui 1 follow-up lich su, sau do dung.

## 5. Template message (Anh/em)
### 5.1 Opener (new_match)
- "Chao em, thay em co gu [X], Anh cung thich [Y]. Em thuong di cafe kieu chill hay nhon nhip?"
- "Hello em, profile em de thuong qua. Cuoi tuan em thuong thu gian bang gi?"

### 5.2 Engaged
- "Anh de y em cung thich [chu de]. Anh hay [thoi quen ngan]. Em bat dau thich no tu luc nao?"
- "Anh de an, tru vo hai san vi bi di ung. Em co mon ruot nao o Ha Noi khong?"

### 5.3 Invite
- "Anh thay noi chuyen hop do. Neu em ranh, minh cafe 45-60 phut cuoi tuan nay nhe, nhe nhang thoi."
- "Neu em thay thoai mai, Anh moi em 1 buoi cafe ngan de doi gio. Khong tien cung khong sao em nhe."

### 5.4 Follow-up (1 lan duy nhat)
- "Anh bump nhe, neu em ban thi minh de dip khac cung duoc."

### 5.5 Graceful close
- "Cam on em da chat, Anh chuc em mot ngay that vui nhe."

## 6. Guardrails (bat buoc)
- Khong su dung mind game (co tinh im lang de tao lo au).
- Khong noi qua da, khong gian doi profile.
- Khong lap lich gap o noi thieu an toan.
- Khong lien tuc gui tin khi chua co phan hoi.

## 7. KPI de toi uu chat
- Reply rate sau opener.
- Ty le hoi dap 2 chieu (em co hoi nguoc lai hay khong).
- Ty le dong y gap mat.
- Ty le dung lich su (khong de lai trai nghiem tieu cuc).

## 8. A/B testing goi y
- Test 2 opener/tuần, khong test qua 4 bien the cung luc.
- Giu nguyen tone lich su, thay doi 1 bien moi lan (chu de/mo dau/do dai).
- Ghi log ket qua de hoc, khong toi uu bang thao tung.

## 9. Logging theo tung nguoi (keep context)
- Muc tieu: moi match co 1 file log rieng de luu lich su hoi thoai, danh gia muc do tuong tac va toi uu buoc tiep theo.
- Thu muc log: `docs/ai/chat_logs/`
- Script: `tinder_chat_logger.py`

Quy trinh toi thieu:
1. Tao log khi co match moi
```bash
python tinder_chat_logger.py init --match-id tinder_linh_20260411 --name "Linh"
```

2. Moi lan gui/nhan tin, append 1 dong log
```bash
python tinder_chat_logger.py log --match-id tinder_linh_20260411 --sender anh --state engaged --text "Chao em, cuoi tuan em hay di cafe o dau?"
python tinder_chat_logger.py log --match-id tinder_linh_20260411 --sender em --state engaged --text "Em hay di cafe o Ha Dong, khong gian yen tinh."
```

3. Danh gia nhanh truoc khi gui tin tiep theo
```bash
python tinder_chat_logger.py eval --match-id tinder_linh_20260411
python tinder_chat_logger.py suggest --match-id tinder_linh_20260411
python tinder_chat_logger.py suggest --match-id tinder_linh_20260411 --json
python tinder_chat_logger.py prompt --match-id tinder_linh_20260411
```

Ket qua danh gia gom:
- `interest_score` (0-100)
- `next_action` (`continue_light`, `move_to_invite`, `single_follow_up`, `close_politely`)
- `next_message_suggestion` (cau nhan tin de xai ngay theo context hien tai)
- metric doi thoai: reciprocity, ty le cau hoi nguoc, ty le rep kho.

Ghi chu:
- `next_message_suggestion` duoc sinh dong dua tren tin nhan gan nhat cua em + trang thai hoi thoai (khong con chon template co dinh).

Output JSON cho automation (`suggest --json`):
```json
{
	"score": 72,
	"next_action": "move_to_invite",
	"suggestion": "Anh thay no chuyen voi em hop do. Neu em ranh, minh cafe 45-60 phut cuoi tuan nay nhe?"
}
```

Che do dung instruction cho AI (`prompt`):
- Lenh `prompt` xuat 1 payload gom `system_instruction`, `user_instruction`, `context`, `response_contract`.
- Payload nay duoc thiet ke de gui truc tiep sang LLM, giup AI tu suy luan theo context thay vi if/else co dinh.
- LLM se tra ve JSON gom: `reply`, `reasoning_brief`, `confidence`.

Luu y van hanh:
- Neu `next_action=single_follow_up`: chi follow-up 1 lan.
- Neu `next_action=close_politely`: dung lich su, khong gui them.
- Neu `next_action=move_to_invite`: gui loi moi cafe nhe nhang 45-60 phut.
