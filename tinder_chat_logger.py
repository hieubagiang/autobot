import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "docs" / "ai" / "chat_logs"

VALID_SENDERS = {"anh", "em", "system"}
VALID_STATES = {"new_match", "engaged", "warm", "invite_sent", "closed"}

AI_SYSTEM_INSTRUCTION = """Bạn là trợ lý soạn tin nhắn Tinder bằng tiếng Việt có dấu.
Mục tiêu: giúp Anh trả lời tự nhiên, lịch sự, có thiện chí, không thao túng.

Ràng buộc bắt buộc:
1) Xưng hô luôn theo ngữ cảnh: gọi người dùng là 'Anh', gọi đối phương là 'em'.
2) Chỉ tạo đúng 1 tin nhắn trả lời cho lượt tiếp theo.
3) Độ dài 1-3 câu, rõ ràng, dễ trả lời lại.
4) Bám sát ngữ cảnh chat gần nhất; không bịa thông tin.
5) Không dùng kỹ thuật gây áp lực, guilt, mind game, hay spam.
6) Nếu tín hiệu yếu, ưu tiên câu nhẹ nhàng hoặc kết thúc lịch sự.

Phong cách:
- Tự nhiên, ấm áp, không sến.
- Có thể gợi mở bằng 1 câu hỏi mở.
- Tránh dài dòng và tránh lặp ý.

Đầu ra bắt buộc ở JSON:
{
    "reply": "...",
    "reasoning_brief": "...",
    "confidence": 0.0
}
"""

TOPIC_HINTS = {
    "cafe": ["cafe", "cà phê", "quán", "chill"],
    "food": ["ăn", "món", "đồ ăn", "nhà hàng", "quán ăn"],
    "movie": ["phim", "marvel", "mcu", "the flash", "kamen rider"],
    "game": ["game", "lol", "liên minh", "valorant"],
    "sport": ["chạy", "gym", "thể thao", "workout"],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_log_dir() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def log_path(match_id: str) -> Path:
    return LOG_DIR / f"{match_id}.json"


def init_log(match_id: str, name: str = "") -> dict:
    ts = now_iso()
    return {
        "match_id": match_id,
        "created_at": ts,
        "last_updated": ts,
        "status": "new_match",
        "profile_snapshot": {
            "name": name,
            "bio": "",
            "interests": [],
            "location": "",
        },
        "messages": [],
        "metrics": {
            "anh_messages": 0,
            "em_messages": 0,
            "reciprocity_ratio": 0.0,
            "em_question_rate": 0.0,
            "em_dry_reply_rate": 0.0,
        },
        "evaluation": {
            "interest_score": 50,
            "next_action": "continue_light",
            "notes": [],
        },
    }


def load_or_create(match_id: str, name: str = "") -> tuple[dict, Path, bool]:
    ensure_log_dir()
    path = log_path(match_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")), path, False
    return init_log(match_id, name=name), path, True


def save_log(path: Path, data: dict) -> None:
    data["last_updated"] = now_iso()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_dry_reply(text: str) -> bool:
    cleaned = text.strip().lower()
    return len(cleaned) <= 6


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def get_last_message(data: dict, sender: str | None = None) -> dict | None:
    messages = data.get("messages", [])
    if not messages:
        return None
    if sender is None:
        return messages[-1]

    for item in reversed(messages):
        if item.get("sender") == sender:
            return item
    return None


def detect_topic(text: str) -> str | None:
    normalized = normalize_text(text)
    if not normalized:
        return None

    for topic, keywords in TOPIC_HINTS.items():
        if any(keyword in normalized for keyword in keywords):
            return topic
    return None


def maybe_add_name(data: dict) -> str:
    name = (data.get("profile_snapshot", {}).get("name") or "").strip()
    if not name:
        return "em"
    return name


def dynamic_continue_message(data: dict) -> str:
    last_em = get_last_message(data, sender="em")
    last_text = (last_em or {}).get("text", "")
    topic = detect_topic(last_text)
    em_name = maybe_add_name(data)

    if last_text and "?" in last_text:
        if topic == "food":
            return f"Anh ăn khá dễ, chỉ dị ứng vỏ hải sản thôi. Còn {em_name} thì hay mê món nào nhất để Anh note lại?"
        if topic == "cafe":
            return f"Anh cũng hay đi cafe, thiên về quán ngồi nói chuyện thoải mái. {em_name} có quán ruột nào muốn recommend không?"
        if topic == "movie":
            return f"Gu phim của Anh thiên về Marvel với mấy phim nhịp nhanh. Dạo này {em_name} đang cày phim gì hay nhất?"
        if topic == "game":
            return f"Anh có chơi game để xả stress sau giờ làm. {em_name} thường chơi để chill hay chơi kiểu tryhard?"
        if topic == "sport":
            return f"Anh duy trì chạy bộ đều để giữ năng lượng. {em_name} thích vận động kiểu cardio hay nhẹ nhàng thôi?"

        return f"Câu em hỏi hay đấy. Anh thấy nói chuyện với {em_name} khá thoải mái, nên muốn nghe thêm góc nhìn của em nữa."

    if topic == "food":
        return f"Nhắc tới ăn uống mới nhớ, Anh khá dễ ăn, chỉ dị ứng vỏ hải sản. {em_name} có món tủ nào mà ăn hoài không chán không?"
    if topic == "cafe":
        return f"Anh hay đi cafe để đổi gió sau giờ làm. {em_name} thích vibe yên tĩnh hay kiểu nhộn nhịp hơn?"
    if topic == "movie":
        return f"Anh thấy gu giải trí của {em_name} thú vị đấy. Nếu chọn 1 bộ xem lại nhiều lần, em chọn phim nào?"
    if topic == "game":
        return f"Anh tò mò gu game của {em_name}. Em thường chơi để thư giãn hay thích cảm giác leo rank hơn?"

    return f"Nói chuyện với {em_name} thấy khá tự nhiên. Cuối tuần em hay đi đâu để nạp lại năng lượng?"


def dynamic_invite_message(data: dict) -> str:
    last_em = get_last_message(data, sender="em")
    topic = detect_topic((last_em or {}).get("text", ""))
    em_name = maybe_add_name(data)

    if topic == "food":
        return f"Anh thấy mình nói chuyện hợp đó {em_name}. Nếu em rảnh, mình hẹn ăn nhẹ rồi cafe khoảng 45-60 phút cuối tuần này nhé?"
    if topic == "movie":
        return f"Anh thấy vibe hợp đó {em_name}. Nếu em thoải mái, mình cafe ngắn cuối tuần rồi trao đổi thêm về phim nhé?"

    return f"Anh thấy nói chuyện với {em_name} khá hợp. Nếu em rảnh, mình cafe 45-60 phút cuối tuần này nhé, nhẹ nhàng thôi."


def dynamic_followup_message(data: dict) -> str:
    em_name = maybe_add_name(data)
    return f"Anh nhắn lại nhẹ một lần thôi nhé {em_name}, nếu em đang bận thì mình để dịp khác cũng được."


def dynamic_close_message(data: dict) -> str:
    em_name = maybe_add_name(data)
    return f"Cảm ơn {em_name} đã trò chuyện cùng Anh. Chúc em một ngày thật vui nhé."


def evaluate(data: dict) -> dict:
    messages = data.get("messages", [])
    anh_msgs = [m for m in messages if m.get("sender") == "anh"]
    em_msgs = [m for m in messages if m.get("sender") == "em"]

    anh_count = len(anh_msgs)
    em_count = len(em_msgs)

    reciprocity = round((em_count / anh_count), 2) if anh_count else 0.0

    em_question_count = sum(1 for m in em_msgs if "?" in (m.get("text") or ""))
    em_question_rate = round((em_question_count / em_count), 2) if em_count else 0.0

    em_dry_count = sum(1 for m in em_msgs if is_dry_reply(m.get("text", "")))
    em_dry_rate = round((em_dry_count / em_count), 2) if em_count else 0.0

    score = 50
    notes: list[str] = []

    if reciprocity >= 0.7:
        score += 20
        notes.append("Reciprocity is healthy.")
    elif reciprocity < 0.4 and anh_count >= 3:
        score -= 15
        notes.append("Low reciprocity detected.")

    if em_question_rate >= 0.2:
        score += 10
        notes.append("She asks back, two-way engagement is good.")

    if em_dry_rate > 0.5 and em_count >= 3:
        score -= 20
        notes.append("Dry replies are frequent.")

    if em_count == 0 and anh_count >= 2:
        score -= 15
        notes.append("No reply yet after multiple messages.")

    score = max(0, min(100, score))

    if em_count == 0 and anh_count >= 1:
        next_action = "single_follow_up"
    elif em_dry_rate > 0.6 and em_count >= 3:
        next_action = "close_politely"
    elif score >= 70:
        next_action = "move_to_invite"
    else:
        next_action = "continue_light"

    suggestion = suggest_next_message(data=data, next_action=next_action)

    metrics = {
        "anh_messages": anh_count,
        "em_messages": em_count,
        "reciprocity_ratio": reciprocity,
        "em_question_rate": em_question_rate,
        "em_dry_reply_rate": em_dry_rate,
    }

    evaluation = {
        "interest_score": score,
        "next_action": next_action,
        "next_message_suggestion": suggestion,
        "notes": notes,
    }

    data["metrics"] = metrics
    data["evaluation"] = evaluation
    return data


def suggest_next_message(data: dict, next_action: str) -> str:
    status = data.get("status", "new_match")

    if next_action == "close_politely":
        return dynamic_close_message(data)

    if next_action == "single_follow_up":
        return dynamic_followup_message(data)

    if next_action == "move_to_invite":
        return dynamic_invite_message(data)

    if status not in VALID_STATES:
        status = "new_match"

    if status in {"new_match", "engaged", "warm"}:
        return dynamic_continue_message(data)

    if status == "invite_sent":
        return dynamic_followup_message(data)

    return dynamic_close_message(data)


def cmd_init(args: argparse.Namespace) -> None:
    data, path, created = load_or_create(args.match_id, name=args.name or "")
    if created:
        save_log(path, data)
        print(f"[OK] Created log: {path}")
    else:
        print(f"[INFO] Log already exists: {path}")


def cmd_log(args: argparse.Namespace) -> None:
    sender = args.sender.strip().lower()
    state = args.state.strip().lower() if args.state else ""

    if sender not in VALID_SENDERS:
        raise ValueError("sender must be one of: anh, em, system")
    if state and state not in VALID_STATES:
        raise ValueError("state must be one of: new_match, engaged, warm, invite_sent, closed")

    data, path, _ = load_or_create(args.match_id, name=args.name or "")

    message = {
        "ts": now_iso(),
        "sender": sender,
        "text": args.text.strip(),
        "state": state or data.get("status", "new_match"),
        "tags": args.tags or [],
    }
    data.setdefault("messages", []).append(message)

    if state:
        data["status"] = state

    evaluate(data)
    save_log(path, data)
    print(f"[OK] Appended message to: {path}")
    print(f"[EVAL] score={data['evaluation']['interest_score']} next={data['evaluation']['next_action']}")
    print(f"[SUGGEST] {data['evaluation']['next_message_suggestion']}")


def cmd_eval(args: argparse.Namespace) -> None:
    path = log_path(args.match_id)
    if not path.exists():
        raise FileNotFoundError(f"Log not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    evaluate(data)
    save_log(path, data)

    out = {
        "match_id": data["match_id"],
        "status": data["status"],
        "metrics": data["metrics"],
        "evaluation": data["evaluation"],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_suggest(args: argparse.Namespace) -> None:
    path = log_path(args.match_id)
    if not path.exists():
        raise FileNotFoundError(f"Log not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    evaluate(data)
    save_log(path, data)

    if args.json_output:
        out = {
            "score": data["evaluation"].get("interest_score", 0),
            "next_action": data["evaluation"].get("next_action", "continue_light"),
            "suggestion": data["evaluation"].get("next_message_suggestion", ""),
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    print(data["evaluation"].get("next_message_suggestion", ""))


def build_ai_prompt_payload(data: dict) -> dict:
    messages = data.get("messages", [])
    last_messages = messages[-8:] if len(messages) > 8 else messages

    context = {
        "match_id": data.get("match_id"),
        "status": data.get("status"),
        "metrics": data.get("metrics", {}),
        "evaluation": data.get("evaluation", {}),
        "profile_snapshot": data.get("profile_snapshot", {}),
        "recent_messages": [
            {
                "sender": m.get("sender"),
                "text": m.get("text"),
                "state": m.get("state"),
                "ts": m.get("ts"),
            }
            for m in last_messages
        ],
        "user_preferences": {
            "language": "vi-VN",
            "addressing": {"user": "Anh", "female_counterpart": "em"},
            "must_have": [
                "Tự nhiên",
                "Ngắn gọn",
                "Dễ để em trả lời lại",
            ],
        },
    }

    user_instruction = (
        "Hãy đề xuất 1 tin nhắn tiếp theo tốt nhất cho Anh dựa trên context. "
        "Nếu next_action là close_politely thì ưu tiên kết thúc lịch sự. "
        "Nếu next_action là single_follow_up thì chỉ nhắn 1 câu follow-up nhẹ nhàng. "
        "Nếu next_action là move_to_invite thì có thể gợi ý hẹn cafe 45-60 phút, không gây áp lực."
    )

    return {
        "system_instruction": AI_SYSTEM_INSTRUCTION,
        "user_instruction": user_instruction,
        "context": context,
        "response_contract": {
            "format": "json",
            "keys": ["reply", "reasoning_brief", "confidence"],
        },
    }


def cmd_prompt(args: argparse.Namespace) -> None:
    path = log_path(args.match_id)
    if not path.exists():
        raise FileNotFoundError(f"Log not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    evaluate(data)
    save_log(path, data)

    payload = build_ai_prompt_payload(data)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Per-match Tinder chat logger with context + evaluation.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Initialize a log for one match")
    p_init.add_argument("--match-id", required=True)
    p_init.add_argument("--name", default="")
    p_init.set_defaults(func=cmd_init)

    p_log = sub.add_parser("log", help="Append one chat message")
    p_log.add_argument("--match-id", required=True)
    p_log.add_argument("--name", default="")
    p_log.add_argument("--sender", required=True, help="anh|em|system")
    p_log.add_argument("--text", required=True)
    p_log.add_argument("--state", default="", help="new_match|engaged|warm|invite_sent|closed")
    p_log.add_argument("--tags", nargs="*", default=[])
    p_log.set_defaults(func=cmd_log)

    p_eval = sub.add_parser("eval", help="Recompute evaluation for one match")
    p_eval.add_argument("--match-id", required=True)
    p_eval.set_defaults(func=cmd_eval)

    p_suggest = sub.add_parser("suggest", help="Return the next suggested message for one match")
    p_suggest.add_argument("--match-id", required=True)
    p_suggest.add_argument("--json", action="store_true", dest="json_output", help="Return JSON object with score, next_action, suggestion")
    p_suggest.set_defaults(func=cmd_suggest)

    p_prompt = sub.add_parser("prompt", help="Build AI instruction + context payload for LLM-based reply generation")
    p_prompt.add_argument("--match-id", required=True)
    p_prompt.set_defaults(func=cmd_prompt)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
