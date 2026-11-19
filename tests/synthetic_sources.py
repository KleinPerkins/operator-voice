"""Synthetic retained-adapter fixtures. Never use operator text here."""
from pathlib import Path

from operator_voice.slack.materialize import materialize, parse_channel_file


def write_slack_notes(root, notes, *, author_id="U_TEST", conversations=1) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if conversations < 1:
        raise ValueError("conversations must be positive")
    offset = sum(len(parse_channel_file(p.read_text())) for p in root.glob("*.md"))
    materialize(root, author_id=author_id, messages=[
        {"channel": f"notes-{(offset + i) % conversations:04d}",
         "message_ts": f"{1700000000 + offset + i}.000100", "author_id": author_id,
         "text": text}
        for i, text in enumerate(notes)
    ])
    return root
