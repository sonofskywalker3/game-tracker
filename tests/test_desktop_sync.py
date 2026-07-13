"""Per-vendor sync with friendly, retry-aware error mapping."""
import requests

from desktop.sync import SyncResult, sync_payloads

_P1 = {"source": "playstation", "games": []}
_P2 = {"source": "xbox", "games": []}


def _http_error(status: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(response=resp)


def test_success_reports_server_summary() -> None:
    def push(payload: dict, base_url: str, token: str) -> dict:
        return {"summary": f"added 3, updated 211 ({payload['source']})"}
    results = sync_payloads([_P1], "https://s", "tok", push=push)
    assert results == [SyncResult("playstation", True, "added 3, updated 211 (playstation)", False)]


def test_401_is_not_retryable_and_run_continues() -> None:
    def push(payload: dict, base_url: str, token: str) -> dict:
        if payload["source"] == "playstation":
            raise _http_error(401)
        return {"summary": "ok"}
    r1, r2 = sync_payloads([_P1, _P2], "https://s", "bad", push=push)
    assert (r1.ok, r1.retryable) == (False, False)
    assert "token rejected" in r1.summary
    assert r2.ok


def test_timeout_and_5xx_are_retryable() -> None:
    def push_timeout(payload: dict, base_url: str, token: str) -> dict:
        raise requests.Timeout()
    def push_500(payload: dict, base_url: str, token: str) -> dict:
        raise _http_error(500)
    assert sync_payloads([_P1], "https://s", "t", push=push_timeout)[0].retryable
    r = sync_payloads([_P1], "https://s", "t", push=push_500)[0]
    assert r.retryable and r.summary.startswith("server busy")


def test_non_dict_response_does_not_abort_the_loop() -> None:
    def push(payload: dict, base_url: str, token: str) -> dict:
        if payload["source"] == "playstation":
            return ["unexpected"]  # type: ignore[return-value]
        return {"summary": "ok"}
    r1, r2 = sync_payloads([_P1, _P2], "https://s", "t", push=push)
    assert r1.ok is False and r1.retryable is True
    assert r2.ok is True
