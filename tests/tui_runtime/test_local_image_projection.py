"""Local image refs are projected only when the recorded bytes still match a regular file."""
import base64


def _record(path, data):
    return [{"type": "text", "text": f"Describe the fixture\n\n[Image attached at: {path}]"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(data).decode()}}]


def test_native_image_projection_requires_matching_local_bytes(tmp_path):
    from agent.context_references import format_reference_value
    from tui_runtime import server

    path = tmp_path / "fixture image.png"
    data = b"\x89PNG\r\n\x1a\nsynthetic-fixture"
    path.write_bytes(data)
    record = _record(path, data)
    assert server._user_image_display_text(record) == "Describe the fixture\n\n@image:" + format_reference_value(str(path))
    path.write_bytes(b"changed")
    assert server._user_image_display_text(record) is None


def test_native_image_projection_refuses_nonfiles_and_oversized_files(tmp_path):
    from tui_runtime import server

    assert server._user_image_display_text(_record(tmp_path, b"fixture")) is None
    path = tmp_path / "oversized.png"
    with path.open("wb") as stream:
        stream.truncate(16 * 1024 * 1024 + 1)
    assert server._user_image_display_text(_record(path, b"fixture")) is None
