"""Synthetic prose/non-prose boundaries; short replies must retain their words."""
from operator_voice.kernel.normalize import strip_non_prose, tokenize_words


def test_non_prose_lines_removed_and_short_prose_preserved():
    text = "\n".join([
        "```python", "print('synthetic')", "```",
        '  File "/tmp/synthetic.py", line 12, in run',
        "https://example.test/a", "/tmp/synthetic/file.txt", "$ uv run task",
        "Read the plan at https://example.test/plan please.", "Sounds good",
        "`some_command()`", "See `value`", "2026-01-01 12:00:00 INFO synthetic log",
    ])
    kept, removed = strip_non_prose(text)
    assert kept == "Read the plan at  please.\nSounds good"
    assert removed == 10
    assert tokenize_words(kept) == ["read", "the", "plan", "at", "please", "sounds", "good"]


def test_plain_short_lines_and_prose_with_inline_code():
    kept, removed = strip_non_prose("ok\nnot yet\nWe should check `value` before shipping.")
    assert kept == "ok\nnot yet\nWe should check  before shipping."
    assert removed == 0


def test_digest_boundaries_and_ordinary_long_lists():
    from operator_voice.kernel.normalize import is_link_digest
    def text(lines, words):
        return '\n'.join(['https://example.com'] * lines) + '\n' + 'word ' * words
    assert is_link_digest(text(20, 1000))
    assert not is_link_digest(text(19, 1000))
    assert not is_link_digest(text(20, 999))
    ordinary = '\n'.join('- ' + 'word ' * 40 + (' https://example.com' if i < 5 else '') for i in range(32))
    assert not is_link_digest(ordinary)
