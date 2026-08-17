from unittest.mock import Mock, patch

from crypto_signals.telegram_api import send_message


@patch("crypto_signals.telegram_api.requests.post")
def test_send_message_posts_expected_payload(mock_post):
    mock_post.return_value = Mock(json=lambda: {"ok": True})
    result = send_message("TOKEN123", "999", "hello world")

    mock_post.assert_called_once_with(
        "https://api.telegram.org/botTOKEN123/sendMessage",
        json={"chat_id": "999", "text": "hello world"},
        timeout=20,
    )
    assert result == {"ok": True}
