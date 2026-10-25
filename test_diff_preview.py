from backend.diff_preview import build_diff_preview


def make_preview(original, proposed, operation="modify", available=True):
    return build_diff_preview(
        path="src/example.py",
        operation=operation,
        original_content=original,
        proposed_content=proposed,
        original_content_available=available,
        unavailable_reason="Source could not be read." if not available else None,
    )


def test_new_file_diff_uses_dev_null_and_target_path():
    preview = make_preview("", "print('hello')\n", operation="create")
    assert preview["changed"] is True
    assert preview["reliable"] is True
    assert "--- /dev/null" in preview["diff"]
    assert "+++ b/src/example.py" in preview["diff"]
    assert "+print('hello')" in preview["diff"]


def test_existing_file_diff_has_additions_and_removals():
    preview = make_preview("value = 1\n", "value = 2\n")
    assert "--- a/src/example.py" in preview["diff"]
    assert "-value = 1" in preview["diff"]
    assert "+value = 2" in preview["diff"]


def test_identical_or_empty_content_is_not_marked_changed():
    assert make_preview("same\n", "same\n")["changed"] is False
    assert make_preview("", "", operation="create")["diff"] == ""


def test_unicode_multiline_and_missing_final_newline_are_preserved():
    preview = make_preview("café\nline", "café\nupdated")
    assert "café" in preview["diff"]
    assert "\\ No newline at end of file" in preview["diff"]
    assert "-line" in preview["diff"]
    assert "+updated" in preview["diff"]


def test_unavailable_original_does_not_produce_a_misleading_diff():
    preview = make_preview(None, "new content\n", available=False)
    assert preview["original_content_available"] is False
    assert preview["reliable"] is False
    assert preview["changed"] is False
    assert preview["diff"] == ""
    assert preview["original_content_unavailable_reason"] == "Source could not be read."
