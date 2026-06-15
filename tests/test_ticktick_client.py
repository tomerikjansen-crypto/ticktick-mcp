"""
Tester for TickTickClient - forretningslogikk (ingen nettverkskall).

Dekker tre scenarier fra F048:
1. tokens.json leses og gir forrang over .env
2. Manglende access_token gir ValueError
3. 401-svar trigger refresh-forsøk via _make_request
"""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open


# Hjelper: bygg en ferdig klient uten __init__-logikk
def _lag_klient_direkte(**attrs):
    """Oppretter TickTickClient uten at __init__ kjøres (unngår dotenv/FS-avhengigheter)."""
    from ticktick_mcp.src.ticktick_client import TickTickClient
    obj = object.__new__(TickTickClient)
    # Minimale defaults
    obj.access_token = "test-token"
    obj.refresh_token = "test-refresh"
    obj.client_id = "test-client"
    obj.client_secret = "test-secret"
    obj.base_url = "https://api.ticktick.com/open/v1"
    obj.token_url = "https://ticktick.com/oauth/token"
    obj.headers = {"Authorization": "Bearer test-token", "Content-Type": "application/json"}
    for k, v in attrs.items():
        setattr(obj, k, v)
    return obj


class TestTokenFilForrang(unittest.TestCase):
    """Scenario 1: tokens.json leses og gir forrang over .env-verdier."""

    def test_token_fil_overstyrer_env(self):
        """access_token fra tokens.json skal erstatte .env-verdien."""
        from ticktick_mcp.src import ticktick_client as modul

        token_data = json.dumps({
            "access_token": "token-fra-fil",
            "refresh_token": "refresh-fra-fil",
        })

        with patch.dict("os.environ", {
            "TICKTICK_ACCESS_TOKEN": "token-fra-env",
            "TICKTICK_REFRESH_TOKEN": "refresh-fra-env",
            "TICKTICK_CLIENT_ID": "cid",
            "TICKTICK_CLIENT_SECRET": "csecret",
        }):
            with patch("ticktick_mcp.src.ticktick_client.TOKEN_FILE") as mock_path:
                mock_path.exists.return_value = True
                mock_path.read_text.return_value = token_data

                with patch("ticktick_mcp.src.ticktick_client.load_dotenv"):
                    from ticktick_mcp.src.ticktick_client import TickTickClient
                    klient = TickTickClient()

        self.assertEqual(klient.access_token, "token-fra-fil",
                         "access_token skal komme fra tokens.json, ikke .env")
        self.assertEqual(klient.refresh_token, "refresh-fra-fil",
                         "refresh_token skal komme fra tokens.json, ikke .env")

    def test_tom_token_fil_faller_tilbake_paa_env(self):
        """Når tokens.json mangler access_token, beholdes .env-verdien."""
        token_data = json.dumps({"access_token": "", "refresh_token": ""})

        with patch.dict("os.environ", {
            "TICKTICK_ACCESS_TOKEN": "token-fra-env",
            "TICKTICK_REFRESH_TOKEN": "refresh-fra-env",
            "TICKTICK_CLIENT_ID": "cid",
            "TICKTICK_CLIENT_SECRET": "csecret",
        }):
            with patch("ticktick_mcp.src.ticktick_client.TOKEN_FILE") as mock_path:
                mock_path.exists.return_value = True
                mock_path.read_text.return_value = token_data

                with patch("ticktick_mcp.src.ticktick_client.load_dotenv"):
                    from ticktick_mcp.src.ticktick_client import TickTickClient
                    klient = TickTickClient()

        self.assertEqual(klient.access_token, "token-fra-env",
                         "Når tokens.json har tom access_token, skal .env-verdien beholdes")

    def test_manglende_token_fil_faller_tilbake_paa_env(self):
        """Når tokens.json ikke finnes, beholdes .env-verdien."""
        with patch.dict("os.environ", {
            "TICKTICK_ACCESS_TOKEN": "token-fra-env",
            "TICKTICK_CLIENT_ID": "cid",
            "TICKTICK_CLIENT_SECRET": "csecret",
        }):
            with patch("ticktick_mcp.src.ticktick_client.TOKEN_FILE") as mock_path:
                mock_path.exists.return_value = False

                with patch("ticktick_mcp.src.ticktick_client.load_dotenv"):
                    from ticktick_mcp.src.ticktick_client import TickTickClient
                    klient = TickTickClient()

        self.assertEqual(klient.access_token, "token-fra-env")


class TestManglendToken(unittest.TestCase):
    """Scenario 2: Manglende access_token skal gi ValueError."""

    def test_ingen_token_gir_value_error(self):
        """__init__ skal kaste ValueError når access_token mangler overalt."""
        with patch.dict("os.environ", {}, clear=True):
            with patch("ticktick_mcp.src.ticktick_client.TOKEN_FILE") as mock_path:
                mock_path.exists.return_value = False

                with patch("ticktick_mcp.src.ticktick_client.load_dotenv"):
                    from ticktick_mcp.src.ticktick_client import TickTickClient
                    with self.assertRaises(ValueError) as ctx:
                        TickTickClient()

        self.assertIn("TICKTICK_ACCESS_TOKEN", str(ctx.exception),
                      "Feilmeldingen skal nevne TICKTICK_ACCESS_TOKEN")

    def test_kun_refresh_token_uten_access_token_gir_error(self):
        """Refresh token uten access token er ikke nok - skal fortsatt feile."""
        with patch.dict("os.environ", {"TICKTICK_REFRESH_TOKEN": "bare-refresh"}, clear=True):
            with patch("ticktick_mcp.src.ticktick_client.TOKEN_FILE") as mock_path:
                mock_path.exists.return_value = False

                with patch("ticktick_mcp.src.ticktick_client.load_dotenv"):
                    from ticktick_mcp.src.ticktick_client import TickTickClient
                    with self.assertRaises(ValueError):
                        TickTickClient()


class TestRefreshVed401(unittest.TestCase):
    """Scenario 3: 401-svar trigger refresh-forsøk i _make_request."""

    def _lag_respons(self, status_code, json_data=None, text=""):
        """Lager en minimal mock-respons."""
        mock = MagicMock()
        mock.status_code = status_code
        mock.text = text if json_data is None else json.dumps(json_data)
        mock.json.return_value = json_data or {}
        if status_code >= 400:
            from requests.exceptions import HTTPError
            mock.raise_for_status.side_effect = HTTPError(response=mock)
        else:
            mock.raise_for_status.return_value = None
        return mock

    def test_401_trigger_token_refresh(self):
        """Ved 401 skal _make_request kalle _refresh_access_token og prøve på nytt."""
        klient = _lag_klient_direkte()

        respons_401 = self._lag_respons(401)
        respons_ok = self._lag_respons(200, {"id": "prosjekt-1"})

        # Første _send-kall gir 401, andre (etter refresh) gir 200
        # _read_token_file returnerer None slik at vi går til ekte refresh-flyt
        klient._read_token_file = MagicMock(return_value=None)
        klient._refresh_access_token = MagicMock(return_value=True)
        klient._send = MagicMock(side_effect=[respons_401, respons_ok])

        resultat = klient._make_request("GET", "/project")

        self.assertEqual(klient._refresh_access_token.call_count, 1,
                         "_refresh_access_token skal kalles nøyaktig en gang ved 401")
        self.assertEqual(klient._send.call_count, 2,
                         "_send skal kalles to ganger: først 401, deretter etter refresh")
        self.assertEqual(resultat, {"id": "prosjekt-1"})

    def test_401_delt_fil_har_nytt_token_brukes_foer_refresh(self):
        """Hvis delt tokenfil har nytt token, skal det brukes før _refresh_access_token kalles."""
        klient = _lag_klient_direkte(access_token="gammelt-token")

        respons_401 = self._lag_respons(401)
        respons_ok = self._lag_respons(200, {"id": "prosjekt-2"})

        # Delt fil har et nytt, annerledes token
        klient._read_token_file = MagicMock(return_value={
            "access_token": "nytt-token-fra-fil",
            "refresh_token": "refresh-fra-fil",
        })
        klient._refresh_access_token = MagicMock(return_value=True)
        klient._send = MagicMock(side_effect=[respons_401, respons_ok])

        resultat = klient._make_request("GET", "/project")

        # Ny token fra fil skal brukes - _refresh_access_token kalles IKKE
        self.assertEqual(klient._refresh_access_token.call_count, 0,
                         "_refresh_access_token skal ikke kalles når delt fil har nytt token")
        self.assertEqual(klient.access_token, "nytt-token-fra-fil",
                         "access_token skal oppdateres fra delt fil")
        self.assertEqual(resultat, {"id": "prosjekt-2"})

    def test_401_uten_refresh_token_gir_feil_respons(self):
        """Når refresh feiler og 401 vedvarer, skal raise_for_status kaste HTTPError."""
        from requests.exceptions import HTTPError
        klient = _lag_klient_direkte()

        respons_401_forst = self._lag_respons(401)
        respons_401_igjen = self._lag_respons(401)

        klient._read_token_file = MagicMock(return_value=None)
        klient._refresh_access_token = MagicMock(return_value=False)
        klient._send = MagicMock(side_effect=[respons_401_forst, respons_401_igjen])

        resultat = klient._make_request("GET", "/project")

        # Når refresh feiler og raise_for_status kaster, fanges det og returneres som error-dict
        self.assertIn("error", resultat,
                      "Vedvarende 401 etter mislykket refresh skal returnere error-dict")

    def test_vellykket_200_gir_ingen_refresh(self):
        """Normal 200-respons skal ikke utlose refresh-logikk."""
        klient = _lag_klient_direkte()
        respons_ok = self._lag_respons(200, [{"name": "Innboks"}])
        klient._send = MagicMock(return_value=respons_ok)
        klient._refresh_access_token = MagicMock()

        resultat = klient._make_request("GET", "/project")

        klient._refresh_access_token.assert_not_called()
        self.assertEqual(resultat, [{"name": "Innboks"}])


if __name__ == "__main__":
    unittest.main()
