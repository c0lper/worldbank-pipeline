import json

import pytest
import requests

from pipeline.api import ApiError, WorldBankClient
from tests.conftest import FakeResponse, FakeSession, obs, page_payload


def make_client(handler, max_retries=2):
    session = FakeSession(handler)
    waits = []
    client = WorldBankClient(session=session, per_page=2, max_retries=max_retries,
                             backoff_seconds=1, sleep=waits.append)
    return client, session, waits


def ok(payload):
    return FakeResponse(200, json.dumps(payload))


def test_follows_all_pages():
    rows = [obs("MEX", "SP.POP.TOTL", y, 1.0 + y) for y in (2024, 2023, 2022, 2021, 2020)]

    def handler(url):
        page = int(url.split("page=")[1].split("&")[0])
        chunk = rows[(page - 1) * 2: page * 2]
        return ok(page_payload(chunk, page=page, pages=3, per_page=2, total=5))

    client, session, _ = make_client(handler)
    pages = client.indicator(["MEX"], "SP.POP.TOTL", 2020, 2024)

    assert [p.page for p in pages] == [1, 2, 3]
    assert sum(len(p.records) for p in pages) == 5
    assert len(session.calls) == 3


def test_retries_server_errors_with_backoff_then_succeeds():
    responses = [FakeResponse(503, "busy"), FakeResponse(500, "oops"), ok(page_payload([]))]
    client, session, waits = make_client(lambda url: responses.pop(0))

    pages = client.countries(["MEX"])

    assert len(session.calls) == 3
    assert waits == [1, 2]  # exponential backoff
    assert pages[0].records == []


def test_retries_network_errors_and_invalid_json():
    responses = [requests.ConnectionError("down"), FakeResponse(200, "<html>error</html>"), ok(page_payload([]))]
    client, session, _ = make_client(lambda url: responses.pop(0))

    client.countries(["MEX"])

    assert len(session.calls) == 3


def test_gives_up_after_max_retries():
    client, session, _ = make_client(lambda url: FakeResponse(502, "bad gateway"), max_retries=2)

    with pytest.raises(ApiError, match="Giving up after 3 attempts"):
        client.countries(["MEX"])
    assert len(session.calls) == 3


def test_client_errors_are_not_retried():
    client, session, _ = make_client(lambda url: FakeResponse(404, "not found"))

    with pytest.raises(ApiError, match="HTTP 404"):
        client.countries(["MEX"])
    assert len(session.calls) == 1


def test_error_message_in_200_body_raises():
    body = [{"message": [{"id": "120", "key": "Invalid value", "value": "The provided parameter value is not valid"}]}]
    client, _, _ = make_client(lambda url: ok(body))

    with pytest.raises(ApiError, match="API error"):
        client.indicator(["MEX"], "BAD.CODE", 2020, 2024)


def test_detects_truncated_results():
    client, _, _ = make_client(lambda url: ok(page_payload([obs("MEX", "SP.POP.TOTL", 2024, 1.0)], total=7)))

    with pytest.raises(ApiError, match="total=7"):
        client.indicator(["MEX"], "SP.POP.TOTL", 2024, 2024)


def test_null_record_list_means_no_data():
    client, _, _ = make_client(lambda url: ok([{"page": 1, "pages": 1, "per_page": 50, "total": 0}, None]))

    pages = client.indicator(["MEX"], "SP.POP.TOTL", 2024, 2024)

    assert pages[0].records == []


def test_same_request_builds_same_url():
    client, _, _ = make_client(lambda url: ok(page_payload([])))
    a = client.build_url("country/MEX", {"page": 1, "format": "json", "date": "2018:2024"})
    b = client.build_url("country/MEX", {"date": "2018:2024", "format": "json", "page": 1})
    assert a == b
