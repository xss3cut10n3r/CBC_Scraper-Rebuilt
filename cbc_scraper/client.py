"""Read-only site client; browser request text is parsed, never executed."""

import os
import re
import shlex
from http.cookies import SimpleCookie
from pathlib import Path

import requests

BASE_URL = "https://nsa-codebreaker.org"


class ScraperError(Exception):
    pass


def request_headers(text):
    text = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    ).strip()
    if "PASTE_COOKIE_VALUE_HERE" in text or not text:
        raise ScraperError(
            "Login template is not filled in. Replace PASTE_COOKIE_VALUE_HERE in edit_this_request.txt with your browser Cookie header, then save and retry."
        )
    if text.lower().startswith("cookie:"):
        return {"cookie": text.split(":", 1)[1].strip()}
    # Copied commands are parsed, never executed. Firefox has two copy formats.
    tokens = _curl_tokens(text)
    headers = {}
    for token_index, token in enumerate(tokens[:-1]):
        if token in ("-H", "--header"):
            key, separator, value = tokens[token_index + 1].partition(":")
            if separator and key.lower() in ("cookie", "x-csrftoken", "x-csrf-token"):
                headers[key.lower()] = value.strip()
        elif token in ("-b", "--cookie"):
            headers["cookie"] = tokens[token_index + 1]
    if not headers:
        raise ScraperError("Request file contains no supported cookie or CSRF header.")
    return headers


def _curl_tokens(text):
    """Split Copy as cURL (Windows) or Copy as cURL (POSIX) into arguments."""
    if _has_windows_quote(text):
        return _windows_curl_tokens(text)
    return _posix_curl_tokens(text)


def _has_windows_quote(text):
    # ^" is cmd's escaped quote. The same two characters can appear inside a POSIX value.
    index = 0
    length = len(text)
    while index < length:
        if text.startswith("$'", index):
            index = _skip_ansi_c_quotes(text, index)
            continue
        if text[index] in "\"'":
            index = _skip_posix_quotes(text, index)
            continue
        if text.startswith('^"', index):
            return True
        index += 1
    return False


def _skip_ansi_c_quotes(text, index):
    index += 2
    length = len(text)
    while index < length:
        if text.startswith("\\'", index) or text.startswith("\\\\", index):
            index += 2
            continue
        if text[index] == "'":
            return index + 1
        index += 1
    return length


def _skip_posix_quotes(text, index):
    quote = text[index]
    index += 1
    length = len(text)
    while index < length and text[index] != quote:
        if quote == '"' and text[index] == "\\" and index + 1 < length:
            index += 2
            continue
        index += 1
    return index + 1


def _posix_curl_tokens(text):
    # $'...' is bash ANSI-C quoting. Firefox uses it when a value contains ! or '.
    text = re.sub(
        r"\$'((?:\\'|[^'])*)'",
        lambda match: _shlex_quote(_decode_ansi_c(match.group(1))),
        text,
    )
    return shlex.split(text.replace("\\\n", " "))


def _shlex_quote(value):
    return "'" + value.replace("'", "'\\''") + "'"


def _decode_ansi_c(body):
    characters = []
    index = 0
    length = len(body)
    while index < length:
        if body[index] != "\\":
            characters.append(body[index])
            index += 1
            continue
        if index + 1 >= length:
            raise ValueError("Truncated ANSI-C escape in cURL command.")
        escape = body[index + 1]
        if escape in "\\'":
            characters.append(escape)
            index += 2
        elif escape == "n":
            characters.append("\n")
            index += 2
        elif escape == "r":
            characters.append("\r")
            index += 2
        elif escape == "x" and index + 3 < length:
            characters.append(chr(int(body[index + 2 : index + 4], 16)))
            index += 4
        elif escape == "u" and index + 5 < length:
            characters.append(chr(int(body[index + 2 : index + 6], 16)))
            index += 6
        elif escape in "01234567":
            digits = escape
            index += 2
            while index < length and len(digits) < 3 and body[index] in "01234567":
                digits += body[index]
                index += 1
            characters.append(chr(int(digits, 8)))
        else:
            characters.append(escape)
            index += 2
    return "".join(characters)


def _windows_curl_tokens(text):
    # Firefox wraps each argument in ^"..."^" and continues lines with ^.
    tokens = []
    index = 0
    length = len(text)
    while index < length:
        while index < length and text[index] in " \t\n":
            index += 1
        if index >= length:
            break
        if text[index] == "^" and (index + 1 == length or text[index + 1] == "\n"):
            index += 1
            if index < length and text[index] == "\n":
                index += 1
            continue
        if text.startswith('^"', index):
            token, index = _read_windows_quoted(text, index)
            tokens.append(token)
            continue
        start = index
        while index < length and text[index] not in " \t\n":
            index += 1
        tokens.append(text[start:index])
    return tokens


def _read_windows_quoted(text, index):
    # Undo cmd caret escapes, then the backslash escapes Firefox applies first.
    index += 2
    characters = []
    length = len(text)
    while index < length:
        if text.startswith('^\\^"', index):
            characters.append('"')
            index += 4
            continue
        if text.startswith("^\\^\\", index):
            characters.append("\\")
            index += 4
            continue
        if text.startswith('^"', index):
            return "".join(characters), index + 2
        if text[index] == "^" and index + 1 < length:
            if text[index + 1] == "\n":
                characters.append("\n")
                index += 2
                if index < length and text[index] == "\n":
                    index += 1
                continue
            characters.append(text[index + 1])
            index += 2
            continue
        characters.append(text[index])
        index += 1
    raise ValueError("Unclosed Windows cURL quote.")


class Client:
    def __init__(self, request_file=None):
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "CBC-Scraper/0.2", "X-Requested-With": "XMLHttpRequest"}
        )
        headers = (
            request_headers(Path(request_file).read_text()) if request_file else {}
        )
        cookie = headers.pop("cookie", os.environ.get("CBC_COOKIE", ""))
        cookies = SimpleCookie()
        cookies.load(cookie)
        if cookie and not cookies:
            raise ScraperError(
                "Could not parse Cookie header; supply name=value pairs."
            )
        for name, morsel in cookies.items():
            self.session.cookies.set(
                name, morsel.value, domain="nsa-codebreaker.org", path="/"
            )
        self.session.headers.update(headers)
        if os.environ.get("CBC_CSRF_TOKEN"):
            self.session.headers["X-CSRFToken"] = os.environ["CBC_CSRF_TOKEN"]

    def fetch(self, path, data=None):
        try:
            response = self.session.request(
                "POST" if data is not None else "GET",
                BASE_URL + path,
                data=data,
                timeout=(10, 45),
                allow_redirects=False,
            )
            if response.status_code in (301, 302, 303, 307, 308, 401, 403):
                raise ScraperError(
                    "The website did not accept your saved login (it may have expired). Run cbc-scraper setup to save a fresh browser request, then retry."
                )
            response.raise_for_status()
            return response
        except requests.RequestException:
            raise ScraperError(
                "Site request failed (network error or HTTP failure); cached data was preserved."
            ) from None

    def bootstrap(self):
        html = self.fetch("/leaderboard").text
        patterns = [
            r"""setRequestHeader\(["']X-CSRFToken["']\s*,\s*["']([^"']+)""",
            r"""name=["']csrf_token["'][^>]*value=["']([^"']+)""",
            r"""name=["']csrf-token["'][^>]*content=["']([^"']+)""",
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.I)
            if match:
                self.session.headers["X-CSRFToken"] = match.group(1)
                break
        self.session.headers["Referer"] = BASE_URL + "/leaderboard"
        return html

    def json(self, path, data=None):
        try:
            value = self.fetch(path, data).json()
        except ValueError:
            raise ScraperError(
                "Expected JSON but received a page; refresh authentication or check the site API."
            ) from None
        if not isinstance(value, dict):
            raise ScraperError("Unexpected API response shape.")
        return value

    def board(self, board_column, board_row, year=None):
        path = (
            f"/data/histboard/{year}/{board_column}/{board_row}"
            if year
            else f"/data/board/{board_column}/{board_row}"
        )
        rows, start = [], 0
        while True:
            result = self.json(path, {"draw": 1, "start": start, "length": -1})
            batch = result.get("data")
            if not isinstance(batch, list):
                raise ScraperError("Leaderboard response has no data array.")
            total = result.get("recordsFiltered", result.get("recordsTotal"))
            if total is None:
                return rows + batch  # Client-side tables return everything.
            rows.extend(batch)
            if len(rows) >= int(total):
                return rows
            if not batch or start > 100000:
                raise ScraperError(
                    "Leaderboard pagination stopped before all rows were received."
                )
            start += len(batch)

    def submissions(self):
        rows = []
        for page in range(1, 10001):
            result = self.json("/my-submissions" + (f"/{page}" if page > 1 else "/"))
            batch = result.get("submissions")
            if not isinstance(batch, list):
                raise ScraperError("Submission response has no submissions array.")
            rows.extend(batch)
            if not result.get("next"):
                if "total_count" in result and len(rows) != int(result["total_count"]):
                    raise ScraperError(
                        "Submission count changed or pagination is incomplete; retry."
                    )
                return rows
            if not batch:
                break
        raise ScraperError("Submission pagination did not finish.")
