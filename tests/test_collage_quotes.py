from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.collage import CollageService, layout
from app.errors import AppError
from app.main import create_app
from app.quotes import Wikiquote, clean_wikitext, parse_quotes
from tests.conftest import FakeImages, FakeLLM, FakeSearch, FakeTMDB


@pytest.fixture
def stills(monkeypatch):
    calls: list[str] = []

    async def fake_download(self, client, url):
        calls.append(url)
        return Image.new("RGB", (1280, 720), (30 + len(calls) * 20, 60, 90))

    monkeypatch.setattr(CollageService, "_download", fake_download)
    return calls


def collage(env, entry, quotes):
    return env.client.post(f"/api/entries/{entry['id']}/collage", json={"quotes": quotes})


def test_collage_with_several_quotes_makes_a_poster(env, stills, tmp_path):
    entry = env.add_entry(2)
    res = collage(env, entry, ["我们之间，隔着一条走廊。", "如果有多一张船票。", "那些消逝了的岁月。"])
    assert res.status_code == 201
    poster = res.json()
    assert poster["quote"].count("\n") == 2 and poster["mood"] == "剧照拼贴"
    img = Image.open(tmp_path / "posters" / poster["filename"])
    assert img.size == (1024, 1536)
    assert env.client.get(poster["url"]).status_code == 200
    assert len(stills) >= 3


def test_single_quote_uses_stills_and_never_calls_the_image_model(env, stills):
    entry = env.add_entry(1)
    assert collage(env, entry, ["一句话"]).status_code == 201
    assert env.images.prompts == []


def test_collage_quote_limits_and_empty(env, stills):
    entry = env.add_entry(1)
    too_many = collage(env, entry, ["a", "b", "c", "d", "e"])
    assert too_many.status_code == 422 and too_many.json()["error_code"] == "request_invalid"
    long = collage(env, entry, ["长" * 121])
    assert long.status_code == 422 and long.json()["error_code"] == "poster_quote_too_long"
    blank = collage(env, entry, ["   "])
    assert blank.status_code == 422 and blank.json()["error_code"] == "poster_quote_empty"
    assert collage(env, {"id": 999}, ["x"]).status_code == 404


def test_collage_needs_a_tmdb_id_and_stills(env, stills):
    manual = env.client.post("/api/entries", json={"title": "手动添加", "year": 2020}).json()
    res = collage(env, manual, ["x"])
    assert res.status_code == 422 and res.json()["error_code"] == "entry_has_no_tmdb_id"
    FakeTMDB.still_count = 0
    try:
        entry = env.add_entry(1)
        assert collage(env, entry, ["x"]).json()["error_code"] == "stills_not_found"
    finally:
        FakeTMDB.still_count = 5


def test_collage_reports_download_failures_instead_of_a_blank_poster(env, monkeypatch):
    async def broken(self, client, url):
        raise AppError("still_unreachable", "Cannot reach the TMDB image server. ConnectError: boom", 502)
    monkeypatch.setattr(CollageService, "_download", broken)
    entry = env.add_entry(1)
    res = collage(env, entry, ["x"])
    assert res.status_code == 502 and res.json()["error_code"] == "still_unreachable"
    assert env.client.get(f"/api/entries/{entry['id']}").json()["posters"] == []


def test_layouts_fill_the_top_area_without_overlap():
    for n in (1, 2, 3, 4):
        cells = layout(n)
        assert len(cells) == n
        for l, t, r, b in cells:
            assert 0 <= l < r <= 1024 and 0 <= t < b <= int(1536 * 0.62)
        for i, a in enumerate(cells):
            for b in cells[i + 1:]:
                assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]


# ---- Wikiquote ------------------------------------------------------------
WIKI = """== Cast ==
* [[Robin Williams]] as Keating
== John Keating ==
* We don't read and write [[poetry]] because it's cute.<ref>x</ref>
** Said to the class
* Carpe diem. Seize the day, boys.
* short
* Carpe diem. Seize the day, boys.
== Neil ==
* I was made for [[acting|acting]]! That's what I was '''made''' for.
"""


def test_parse_quotes_keeps_top_level_lines_and_cleans_markup():
    got = parse_quotes(WIKI)
    assert [(q["speaker"], q["text"]) for q in got] == [
        ("John Keating", "We don't read and write poetry because it's cute."),
        ("John Keating", "Carpe diem. Seize the day, boys."),
        ("Neil", "I was made for acting! That's what I was made for."),
    ]
    assert clean_wikitext("[[a|b]] {{tpl}} <br/>x") == "b \nx"


class FakeWikiquote(Wikiquote):
    def __init__(self, pages=None, error=None):
        super().__init__()
        self.pages, self.error, self.asked = pages or {}, error, []

    async def find(self, titles):
        self.asked.append(list(titles))
        if self.error:
            raise self.error
        groups = []
        for lang, title in dict.fromkeys(titles):
            if (lang, title) in self.pages:
                url = f"https://{lang}.wikiquote.org/wiki/{title}"
                groups.append({"lang": lang, "source": url, "quotes": [
                    {**q, "source": url, "lang": lang} for q in parse_quotes(self.pages[(lang, title)])]})
        return {"groups": groups, "tried": [f"{l}:{t}" for l, t in titles]}


def app_with(env, wikiquote):
    return TestClient(create_app(env.settings, FakeLLM(), FakeImages(), FakeTMDB(), FakeSearch(), wikiquote))


def test_quote_candidates_use_english_title_and_mark_saved(env):
    wq = FakeWikiquote({("en", "In the Mood for Love"): WIKI})
    client = app_with(env, wq)
    entry = client.post("/api/entries", json={"tmdb_id": 2, "title": "花样年华", "year": 2000}).json()
    client.post(f"/api/entries/{entry['id']}/clips", json={"text": "Carpe diem. Seize the day, boys."})
    res = client.get(f"/api/entries/{entry['id']}/quote-candidates").json()
    assert wq.asked[0][0] == ("en", "In the Mood for Love")
    assert ("zh", "花样年华") in wq.asked[0]
    quotes = res["groups"][0]["quotes"]
    assert [q["saved"] for q in quotes] == [False, True, False]
    assert res["groups"][0]["source"].startswith("https://en.wikiquote.org/")


def test_quote_candidates_empty_and_error_are_explicit(env):
    entry = env.add_entry(1)
    client = app_with(env, FakeWikiquote())
    assert client.get(f"/api/entries/{entry['id']}/quote-candidates").json()["groups"] == []
    limited = app_with(env, FakeWikiquote(error=AppError("quotes_rate_limited", "Wikiquote is rate limiting", 429)))
    res = limited.get(f"/api/entries/{entry['id']}/quote-candidates")
    assert res.status_code == 429 and res.json()["error_code"] == "quotes_rate_limited"
