import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gmail  # noqa: E402
from gmail import GmailService  # noqa: E402
from googleapiclient.errors import HttpError  # noqa: E402


def _service_with_pages(pages):
    """A fake Gmail service whose drafts().list returns `pages` in order."""
    calls = []

    def list_(**params):
        calls.append(params)
        page = pages[len(calls) - 1]
        req = MagicMock()
        req.execute.return_value = page
        return req

    def get_(userId, id, format):
        req = MagicMock()
        req.execute.return_value = {"message": {"id": "m" + id, "threadId": "t" + id}}
        return req

    drafts = MagicMock()
    drafts.list.side_effect = list_
    drafts.get.side_effect = get_
    service = MagicMock()
    service.users.return_value.drafts.return_value = drafts
    return service, calls


def _gmail(service):
    svc = GmailService.__new__(GmailService)
    svc.service = service
    svc.account_name = "test@"
    return svc


def _page(start, n, token=None):
    page = {"drafts": [{"id": str(i)} for i in range(start, start + n)]}
    if token:
        page["nextPageToken"] = token
    return page


class ListDraftsPagingTest(unittest.TestCase):
    def test_pages_past_fifty(self) -> None:
        service, calls = _service_with_pages([_page(0, 100, "p2"), _page(100, 30)])
        drafts = _gmail(service).list_drafts(200)
        self.assertEqual(len(drafts), 130)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["pageToken"], "p2")
        self.assertEqual(drafts[129]["draft_id"], "129")

    def test_stops_at_max_results(self) -> None:
        service, calls = _service_with_pages([_page(0, 100, "p2"), _page(100, 100, "p3")])
        drafts = _gmail(service).list_drafts(150)
        self.assertEqual(len(drafts), 150)
        self.assertEqual(calls[1]["maxResults"], 50)

    def test_hard_ceiling(self) -> None:
        pages = [_page(i * 100, 100, "p%d" % (i + 2)) for i in range(12)]
        service, calls = _service_with_pages(pages)
        drafts = _gmail(service).list_drafts(10_000)
        self.assertEqual(len(drafts), GmailService.MAX_DRAFTS)
        self.assertEqual(len(calls), GmailService.MAX_DRAFTS // 100)

    def test_empty_page_with_token_does_not_loop(self) -> None:
        service, calls = _service_with_pages([{"drafts": [], "nextPageToken": "again"}])
        self.assertEqual(_gmail(service).list_drafts(100), [])
        self.assertEqual(len(calls), 1)


def _http_error(status, body):
    resp = MagicMock()
    resp.status = status
    resp.reason = "err"
    return HttpError(resp, body)


_RATE_LIMIT_BODY = b'{"error": {"errors": [{"reason": "rateLimitExceeded"}]}}'


class BackoffTest(unittest.TestCase):
    def _request_failing(self, failures):
        state = {"n": 0}

        def make():
            req = MagicMock()

            def execute():
                state["n"] += 1
                if state["n"] <= len(failures):
                    raise failures[state["n"] - 1]
                return {"ok": True}

            req.execute.side_effect = execute
            return req

        return make, state

    @patch("gmail.time.sleep")
    def test_waits_out_a_rate_limit(self, sleep) -> None:
        make, state = self._request_failing([_http_error(403, _RATE_LIMIT_BODY)] * 2)
        self.assertEqual(gmail._execute_with_backoff(make), {"ok": True})
        self.assertEqual(state["n"], 3)
        self.assertEqual(sleep.call_count, 2)
        sleep.assert_called_with(gmail._RATE_LIMIT_WAIT_SECONDS)

    @patch("gmail.time.sleep")
    def test_429_is_retried(self, sleep) -> None:
        make, state = self._request_failing([_http_error(429, b"")])
        self.assertEqual(gmail._execute_with_backoff(make), {"ok": True})
        self.assertEqual(sleep.call_count, 1)

    @patch("gmail.time.sleep")
    def test_other_errors_are_not_retried(self, sleep) -> None:
        make, state = self._request_failing([_http_error(403, b'{"error": {"errors": [{"reason": "forbidden"}]}}')])
        with self.assertRaises(HttpError):
            gmail._execute_with_backoff(make)
        self.assertEqual(state["n"], 1)
        sleep.assert_not_called()

    @patch("gmail.time.sleep")
    def test_gives_up_after_the_last_attempt(self, sleep) -> None:
        make, state = self._request_failing([_http_error(403, _RATE_LIMIT_BODY)] * 10)
        with self.assertRaises(HttpError):
            gmail._execute_with_backoff(make)
        self.assertEqual(state["n"], gmail._RATE_LIMIT_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
