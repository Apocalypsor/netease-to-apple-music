import unittest

from apple_music_migrator.apple_music import AppleMusicClient
from apple_music_migrator.config import PublicAuth, RuntimeConfig, Settings, WebAuth
from apple_music_migrator.models import Song


class FakeHttpClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict]] = []

    def get_json(self, url: str, **kwargs: dict) -> dict:
        self.calls.append((url, kwargs))
        return self.payload


class AppleMusicSearchTests(unittest.TestCase):
    def test_extracts_song_resources_from_web_resource_map(self) -> None:
        song = {
            "id": "song-1",
            "type": "songs",
            "attributes": {"name": "Example", "artistName": "Artist"},
        }
        payload = {
            "results": {
                "songs": {"data": [{"id": "song-1", "type": "songs"}]}
            },
            "resources": {"songs": {"song-1": song}},
        }

        candidates = AppleMusicClient._catalog_search_candidates(payload)

        self.assertEqual(candidates, [song])
        self.assertEqual(AppleMusicClient._song_candidates(candidates), [song])

    def test_web_search_uses_catalog_song_resource(self) -> None:
        song = {
            "id": "song-1",
            "type": "songs",
            "attributes": {"name": "Example", "artistName": "Artist"},
        }
        settings = Settings(
            source_playlist_id=1,
            storefront="us",
            target_playlist_id="p.test",
            transport="web",
            public=PublicAuth("", ""),
            web=WebAuth("Bearer test", "media", "cookie", "2646.4.0-external"),
            runtime=RuntimeConfig(request_delay=8.0, search_limit=21),
        )
        client = AppleMusicClient(settings)
        fake_http = FakeHttpClient(
            {"results": {"songs": {"data": [song]}}}
        )
        client.http = fake_http

        match = client.search(Song(1, 0, "Example", "Artist"))

        self.assertEqual(match.catalog_id, "song-1")
        self.assertEqual(fake_http.calls[0][0], client.search_url)
        self.assertEqual(fake_http.calls[0][1]["params"]["limit"], 21)


if __name__ == "__main__":
    unittest.main()
