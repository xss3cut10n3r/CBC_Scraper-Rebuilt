import re
import unittest
from unittest.mock import Mock
from cbc_scraper.client import Client, ScraperError, request_headers
from cbc_scraper.stats import leaderboard, submissions


class Tests(unittest.TestCase):
    def test_cookie_names_and_no_execution(self):
        headers = request_headers(
            "curl https://example.invalid -H 'Cookie: cbweb_session=abc; remember_token=def' -H 'Authorization: ignored'"
        )
        self.assertEqual(headers, {"cookie": "cbweb_session=abc; remember_token=def"})

    def test_posix_and_windows_curl_round_trip(self):
        cookie = "remember_token=abc|def&g; cbweb_session=a\"b\\c%d$e"
        csrf = "tok!en's"
        headers = (("Cookie", cookie), ("X-CSRFToken", csrf))
        for windows in (False, True):
            for line_ending in ("\n", "\r\n"):
                copied = _firefox_curl(headers, windows).replace("\n", line_ending)
                parsed = request_headers("# saved from devtools\n" + copied)
                self.assertEqual(parsed["cookie"], cookie)
                self.assertEqual(parsed["x-csrftoken"], csrf)

    def test_windows_curl_one_header_stays_one_line(self):
        cookie = "cbweb_session=abc|def"
        copied = _firefox_curl((("Cookie", cookie),), windows=True)
        self.assertNotIn("\n", copied)
        self.assertEqual(request_headers(copied)["cookie"], cookie)

    def test_posix_quote_can_contain_windows_marker(self):
        copied = "curl 'https://nsa-codebreaker.org' -H 'Cookie: session=keep^\"this'"
        self.assertEqual(request_headers(copied)["cookie"], 'session=keep^"this')

    def test_posix_ansi_c_can_contain_windows_marker(self):
        cookie = "session=it's ^\"kept"
        copied = _firefox_curl((("Cookie", cookie),), windows=False)
        self.assertEqual(request_headers(copied)["cookie"], cookie)

    def test_earliest_and_missing(self):
        raw = {
            "Participants": [["A", 10], ["B", 20]],
            "Task 0": [["A", 2, 2, 200], ["B", 1, 1, 100], ["C", 1, 1, None]],
        }
        stats = leaderboard(raw, 2026)
        self.assertEqual(
            [school["school"] for school in stats["tasks"]["Task 0"]["fastest"]],
            ["B", "A"],
        )
        self.assertEqual(stats["tasks"]["Task 0"]["schools"][0]["solve_rate"], 20)

    def test_old_solver_column(self):
        self.assertEqual(
            leaderboard({"Task 1": [["A", 99, 3, 100]]}, 2020)["tasks"]["Task 1"][
                "solvers"
            ],
            3,
        )

    def test_unknown_not_success(self):
        result = submissions(
            [
                {
                    "task": "task8",
                    "at": "2026-01-01T00:00:00Z",
                    "response": "something new",
                },
                {"task": "task8", "at": "2026-01-01T02:00:00Z"},
            ]
        )
        self.assertEqual(result["task8"]["status"], "Unknown")
        self.assertEqual(result["task8"]["attempt_span_hours"], 2)

    def test_full_board(self):
        client = Client()
        client.json = Mock(return_value={"data": [["A", 1]]})
        self.assertEqual(client.board(1, 0), [["A", 1]])

    def test_paginated_board(self):
        client = Client()
        client.json = Mock(
            side_effect=[
                {"data": [["A", 1]], "recordsTotal": 2},
                {"data": [["B", 2]], "recordsTotal": 2},
            ]
        )
        self.assertEqual(len(client.board(1, 0)), 2)

    def test_incomplete_submissions(self):
        client = Client()
        client.json = Mock(
            return_value={"submissions": [], "total_count": 2, "next": None}
        )
        with self.assertRaises(ScraperError):
            client.submissions()

    def test_redirect_not_followed(self):
        client = Client()
        client.session.request = Mock(return_value=Mock(status_code=302))
        with self.assertRaises(ScraperError):
            client.fetch("/leaderboard")
        self.assertFalse(client.session.request.call_args.kwargs["allow_redirects"])


def _firefox_curl(headers, windows):
    """Build the command Firefox copies. Mirrors devtools/client/shared/curl.js."""
    escape = _firefox_windows_argument if windows else _firefox_posix_argument
    parts = [escape("https://nsa-codebreaker.org/challenge")]
    for name, value in headers:
        header = f"{name}: {value}"
        if re.fullmatch(r"[A-Za-z-]+", header):
            parts.append("-H " + header)
        else:
            parts.append("-H " + escape(header))
    if len(parts) >= 3:
        joiner = " ^\n  " if windows else " \\\n  "
    else:
        joiner = " "
    command = "curl.exe " if windows else "curl "
    return command + joiner.join(parts)


def _firefox_windows_argument(value):
    safe = set("-\t\n\r\f\v _: =+~/.' ,?;()*`")
    safe.update("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    encoded = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = []
    for character in encoded:
        if character in safe:
            escaped.append(character)
        else:
            escaped.append("^" + character)
    encoded = re.sub(r"%(?=[A-Za-z0-9_])", "%^", "".join(escaped))
    encoded = re.sub(r"[^\S\r\n]", " ", encoded)
    encoded = re.sub(r"\r\n|\r|\n", "^\n\n", encoded)
    return '^"' + encoded + '^"'


def _firefox_posix_argument(value):
    if re.search(r"[^\x20-\x7E]|!|'", value):

        def escape_character(match):
            code = ord(match.group(0))
            if code < 256:
                return f"\\x{code:02x}"
            return f"\\u{code:04x}"

        body = (
            value.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("!", "\\041")
        )
        body = re.sub(r"[^\x20-\x7E]", escape_character, body)
        return "$'" + body + "'"
    return "'" + value + "'"


if __name__ == "__main__":
    unittest.main()
