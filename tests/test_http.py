import unittest
from unittest.mock import patch

import requests

from apple_music_migrator.http import HttpClient


class HttpClientTests(unittest.TestCase):
    def test_min_interval_applies_before_each_request(self) -> None:
        response = requests.Response()
        response.status_code = 200
        client = HttpClient(
            timeout=1,
            max_retries=0,
            user_agent="test",
            min_interval=2.0,
        )

        with (
            patch.object(client.session, "request", return_value=response) as request,
            patch("apple_music_migrator.http.time.monotonic", side_effect=[0.0, 0.0, 0.5, 2.0]),
            patch("apple_music_migrator.http.time.sleep") as sleep,
        ):
            client.request("GET", "https://example.test")
            client.request("GET", "https://example.test")

        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(1.5)


if __name__ == "__main__":
    unittest.main()
