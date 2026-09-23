from inference_gateway.__main__ import _parser, _terminal_link


def test_start_defaults_to_foreground_without_opening_browser():
    args = _parser().parse_args(["start"])
    assert args.background is False
    assert args.open is False


def test_start_background_is_explicit():
    args = _parser().parse_args(["start", "--background"])
    assert args.background is True


def test_terminal_link_uses_osc8_for_interactive_terminals():
    url = "http://127.0.0.1:8080"
    assert _terminal_link(url, interactive=True) == f"\033]8;;{url}\033\\{url}\033]8;;\033\\"
    assert _terminal_link(url, interactive=False) == url
