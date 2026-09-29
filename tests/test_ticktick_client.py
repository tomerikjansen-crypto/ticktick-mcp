"""
Tester for TickTickClient - forretningslogikk (ingen nettverkskall).

Dekker tre scenarier fra F048:
1. tokens.json leses og gir forrang over .env
2. Manglende access_token gir ValueError
3. 401-svar trigger refresh-forsøk via _make_request
"""

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open


@contextmanager
def i_tom_mappe():
    """Kjoer i en tom midlertidig mappe (for .env-skriving). Gir mappestien."""
    gammel = os.getcwd()
    with tempfile.TemporaryDirectory() as mappe:
        os.chdir(mappe)
        try:
            yield mappe
        finally:
            os.chdir(gammel)


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


class TestRefreshTokenNullAutoritativ(unittest.TestCase):
    """Fila er autoritativ for refresh_token, ogsaa naar den er eksplisitt null.

    Bruker en ekte midlertidig tokenfil (TOKEN_FILE pekes til tmp-sti) og
    oppdiktede TEST-verdier. Speiler dashbordets ticktick-token.mjs, der
    null betyr "ingen refresh-token".
    """

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fil = Path(self._tmp.name) / "tokens.json"

    def _skriv(self, data):
        self.fil.write_text(json.dumps(data), encoding="utf-8")

    def _opprett_klient(self, env=None):
        miljo = {
            "TICKTICK_ACCESS_TOKEN": "TEST-access-env",
            "TICKTICK_REFRESH_TOKEN": "TEST-refresh-env",
            "TICKTICK_CLIENT_ID": "TEST-cid",
            "TICKTICK_CLIENT_SECRET": "TEST-csecret",
        }
        if env is not None:
            miljo = env
        with patch.dict("os.environ", miljo, clear=True), \
                patch("ticktick_mcp.src.ticktick_client.TOKEN_FILE", self.fil), \
                patch("ticktick_mcp.src.ticktick_client.load_dotenv"):
            from ticktick_mcp.src.ticktick_client import TickTickClient
            return TickTickClient()

    def test_fil_med_null_overstyrer_gammel_verdi_i_miljo(self):
        self._skriv({"access_token": "TEST-access-fil", "refresh_token": None})
        klient = self._opprett_klient()
        self.assertEqual(klient.access_token, "TEST-access-fil")
        self.assertIsNone(klient.refresh_token,
                          "null i fila skal gi None, ikke gammel miljoeverdi")

    def test_fil_uten_noekkel_faller_tilbake_paa_miljo(self):
        self._skriv({"access_token": "TEST-access-fil"})
        klient = self._opprett_klient()
        self.assertEqual(klient.refresh_token, "TEST-refresh-env")

    def test_fil_med_streng_brukes(self):
        self._skriv({"access_token": "TEST-access-fil", "refresh_token": "TEST-refresh-fil"})
        klient = self._opprett_klient()
        self.assertEqual(klient.refresh_token, "TEST-refresh-fil")

    def test_uten_fil_faller_tilbake_paa_miljo(self):
        klient = self._opprett_klient()  # self.fil finnes ikke
        self.assertEqual(klient.refresh_token, "TEST-refresh-env")

    def test_fil_uten_access_token_er_ikke_autoritativ(self):
        self._skriv({"access_token": "", "refresh_token": None})
        klient = self._opprett_klient()
        self.assertEqual(klient.access_token, "TEST-access-env")
        self.assertEqual(klient.refresh_token, "TEST-refresh-env")

    def _respons(self, status_code):
        mock = MagicMock()
        mock.status_code = status_code
        mock.text = "{}"
        mock.json.return_value = {}
        if status_code >= 400:
            from requests.exceptions import HTTPError
            mock.raise_for_status.side_effect = HTTPError(response=mock)
        else:
            mock.raise_for_status.return_value = None
        return mock

    def test_adopsjon_etter_401_med_null_gir_none(self):
        klient = _lag_klient_direkte(access_token="TEST-gammel-access",
                                     refresh_token="TEST-gammel-refresh")
        self._skriv({"access_token": "TEST-ny-access", "refresh_token": None})
        klient._send = MagicMock(side_effect=[self._respons(401), self._respons(200)])
        with patch("ticktick_mcp.src.ticktick_client.TOKEN_FILE", self.fil):
            klient._make_request("GET", "/project")
        self.assertEqual(klient.access_token, "TEST-ny-access")
        self.assertIsNone(klient.refresh_token,
                          "adopsjon etter 401 skal ikke bevare gammel refresh-verdi naar fila har null")

    def _401_med_fil(self, klient, fil_data):
        """Kjoer en 401 mot en tokenfil. Gir (resultat, requests.post-mocken)."""
        self._skriv(fil_data)
        klient._send = MagicMock(return_value=self._respons(401))
        with patch("ticktick_mcp.src.ticktick_client.TOKEN_FILE", self.fil), \
                patch("ticktick_mcp.src.ticktick_client.requests.post") as post:
            resultat = klient._make_request("GET", "/project")
        return resultat, post

    def test_401_fil_med_null_og_nyere_tidsstempel_adopteres_uten_refresh_forsoek(self):
        naa = datetime.now(timezone.utc)
        klient = _lag_klient_direkte(access_token="TEST-samme", refresh_token="TEST-gammel-refresh",
                                     _fil_oppdatert=naa - timedelta(minutes=5))
        resultat, post = self._401_med_fil(klient, {
            "access_token": "TEST-samme", "refresh_token": None,
            "oppdatert": naa.isoformat()})
        post.assert_not_called()
        self.assertIn("error", resultat)
        self.assertIsNone(klient.refresh_token)

    def test_401_fil_med_samme_access_og_uten_nyere_tidsstempel_adopteres_ikke(self):
        """Funn 4: en fil som ikke er nyere skal ikke nullstille refresh-tokenen i minnet."""
        naa = datetime.now(timezone.utc)
        for oppdatert in (None, (naa - timedelta(minutes=5)).isoformat(), naa.isoformat()):
            with self.subTest(oppdatert=oppdatert):
                klient = _lag_klient_direkte(access_token="TEST-samme",
                                             refresh_token="TEST-gammel-refresh",
                                             _fil_oppdatert=naa)
                fil = {"access_token": "TEST-samme", "refresh_token": None}
                if oppdatert:
                    fil["oppdatert"] = oppdatert
                respons = MagicMock()
                respons.json.return_value = {"access_token": "TEST-fornyet"}
                respons.raise_for_status.return_value = None
                self._skriv(fil)
                klient._send = MagicMock(side_effect=[self._respons(401), self._respons(200)])
                with patch("ticktick_mcp.src.ticktick_client.TOKEN_FILE", self.fil), \
                        patch("ticktick_mcp.src.ticktick_client.requests.post",
                              return_value=respons) as post, \
                        i_tom_mappe():
                    klient._make_request("GET", "/project")
                post.assert_called_once()  # egen refresh med beholdt refresh-token
                self.assertEqual(post.call_args.kwargs["data"]["refresh_token"],
                                 "TEST-gammel-refresh")

    def test_401_eldre_fil_med_annen_access_adopteres_ikke(self):
        naa = datetime.now(timezone.utc)
        klient = _lag_klient_direkte(access_token="TEST-ny-i-minnet",
                                     refresh_token="TEST-rotert-refresh", _fil_oppdatert=naa)
        resultat, post = self._401_med_fil(klient, {
            "access_token": "TEST-gammel-i-fil", "refresh_token": "TEST-doed-refresh",
            "oppdatert": (naa - timedelta(hours=1)).isoformat()})
        self.assertEqual(klient.access_token, "TEST-ny-i-minnet")
        self.assertEqual(klient.refresh_token, "TEST-rotert-refresh")

    def test_lagring_til_env_gjor_ikke_null_til_streng(self):
        klient = _lag_klient_direkte()
        with i_tom_mappe() as ws:
            klient._save_tokens_to_env({"access_token": "TEST-a", "refresh_token": None})
            innhold = (Path(ws) / ".env").read_text(encoding="utf-8")
        self.assertNotIn("None", innhold)
        self.assertNotIn("TICKTICK_REFRESH_TOKEN", innhold)

    def test_lagring_til_env_beholder_eksisterende_refresh_ved_null_og_tom(self):
        klient = _lag_klient_direkte()
        for svar in (None, ""):
            with self.subTest(refresh=svar), i_tom_mappe() as ws:
                (Path(ws) / ".env").write_text("TICKTICK_REFRESH_TOKEN=TEST-eksisterende\n",
                                               encoding="utf-8")
                klient._save_tokens_to_env({"access_token": "TEST-a", "refresh_token": svar})
                innhold = (Path(ws) / ".env").read_text(encoding="utf-8")
                self.assertIn("TICKTICK_REFRESH_TOKEN=TEST-eksisterende\n", innhold)


class TestRefreshGrenenFaktisk(unittest.TestCase):
    """Kjoerer den FAKTISKE _refresh_access_token med simulert HTTP-svar.

    requests.post er mocket (ingen nett), TOKEN_FILE peker til en tmp-fil og
    cwd er en tom tmp-mappe (.env skrives relativt). Kun TEST-verdier.
    Kontrollerer minne, delt tokenfil og en ny klientoppstart.
    """

    def setUp(self):
        import os
        import tempfile
        from contextlib import ExitStack
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fil = Path(self._tmp.name) / "tokens.json"
        self.fil.write_text(json.dumps({
            "access_token": "TEST-access-gammel",
            "refresh_token": "TEST-refresh-gammel",
        }), encoding="utf-8")

        gammel_cwd = os.getcwd()
        os.chdir(self._tmp.name)
        self.addCleanup(os.chdir, gammel_cwd)

        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict("os.environ", {
            "TICKTICK_ACCESS_TOKEN": "TEST-access-env",
            "TICKTICK_REFRESH_TOKEN": "TEST-refresh-env",
            "TICKTICK_CLIENT_ID": "TEST-cid",
            "TICKTICK_CLIENT_SECRET": "TEST-csecret",
        }, clear=True))
        stack.enter_context(patch("ticktick_mcp.src.ticktick_client.TOKEN_FILE", self.fil))
        stack.enter_context(patch("ticktick_mcp.src.ticktick_client.load_dotenv"))

    def _ny_klient(self):
        from ticktick_mcp.src.ticktick_client import TickTickClient
        return TickTickClient()

    def _refresh_med_svar(self, svar):
        klient = self._ny_klient()
        self.assertEqual(klient.refresh_token, "TEST-refresh-gammel")
        respons = MagicMock()
        respons.raise_for_status.return_value = None
        respons.json.return_value = svar
        with patch("ticktick_mcp.src.ticktick_client.requests.post",
                   return_value=respons) as post:
            self.assertTrue(klient._refresh_access_token())
        post.assert_called_once()
        return klient

    def _fil(self):
        return json.loads(self.fil.read_text(encoding="utf-8"))

    def _env_fil(self):
        return (Path(self._tmp.name) / ".env").read_text(encoding="utf-8")

    def _sjekk_gammel_refresh_beholdt(self, klient):
        """Et OAuth-svar uten faktisk ny refresh-token betyr "ikke rotert": behold den gamle."""
        self.assertEqual(klient.access_token, "TEST-access-ny")
        self.assertEqual(klient.refresh_token, "TEST-refresh-gammel", "minne")
        self.assertEqual(self._fil()["refresh_token"], "TEST-refresh-gammel", "delt tokenfil")
        self.assertEqual(self._fil()["access_token"], "TEST-access-ny")
        self.assertNotIn("None", self._env_fil())
        self.assertEqual(self._ny_klient().refresh_token, "TEST-refresh-gammel", "ny oppstart")

    def test_refresh_svar_med_null_beholder_gammel_verdi(self):
        klient = self._refresh_med_svar({"access_token": "TEST-access-ny", "refresh_token": None})
        self._sjekk_gammel_refresh_beholdt(klient)

    def test_refresh_svar_med_tom_streng_beholder_gammel_verdi(self):
        klient = self._refresh_med_svar({"access_token": "TEST-access-ny", "refresh_token": ""})
        self._sjekk_gammel_refresh_beholdt(klient)

    def test_refresh_svar_uten_noekkel_beholder_gammel_verdi(self):
        klient = self._refresh_med_svar({"access_token": "TEST-access-ny"})
        self._sjekk_gammel_refresh_beholdt(klient)

    def test_refresh_svar_uten_access_token_avvises(self):
        """Funn 6: svar uten access_token skal ikke nullstille gamle verdier."""
        klient = self._ny_klient()
        for svar in ({}, {"refresh_token": "TEST-refresh-ny"},
                     {"access_token": None}, {"access_token": ""}):
            with self.subTest(svar=svar):
                respons = MagicMock()
                respons.raise_for_status.return_value = None
                respons.json.return_value = svar
                with patch("ticktick_mcp.src.ticktick_client.requests.post",
                           return_value=respons):
                    self.assertFalse(klient._refresh_access_token())
                self.assertEqual(klient.access_token, "TEST-access-gammel")
                self.assertEqual(klient.refresh_token, "TEST-refresh-gammel")
                self.assertEqual(klient.headers["Authorization"], "Bearer TEST-access-gammel")
                self.assertEqual(self._fil()["access_token"], "TEST-access-gammel")
                self.assertEqual(self._fil()["refresh_token"], "TEST-refresh-gammel")

    def test_feilende_filskriving_gir_ikke_tap_av_refresh_i_minnet(self):
        """Funn 4/8: OSError ved skriving. Minnet beholder den roterte tokenen, og en
        eldre fil adopteres ikke etter en senere 401."""
        self.fil.write_text(json.dumps({
            "access_token": "TEST-access-gammel", "refresh_token": "TEST-refresh-gammel",
            "oppdatert": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        }), encoding="utf-8")
        klient = self._ny_klient()
        respons = MagicMock()
        respons.raise_for_status.return_value = None
        respons.json.return_value = {"access_token": "TEST-access-ny",
                                     "refresh_token": "TEST-refresh-rotert"}
        with patch("ticktick_mcp.src.ticktick_client.requests.post", return_value=respons), \
                patch("pathlib.Path.replace", side_effect=OSError("TEST-skrivefeil")):
            self.assertTrue(klient._refresh_access_token())
        self.assertEqual(klient.refresh_token, "TEST-refresh-rotert")
        self.assertEqual(self._fil()["refresh_token"], "TEST-refresh-gammel", "fila er uendret")

        # Senere 401: den gamle fila (ulik access_token, eldre tidsstempel) skal ikke adopteres
        klient._send = MagicMock(side_effect=[self._respons_401(), self._respons_401()])
        with patch("ticktick_mcp.src.ticktick_client.requests.post") as post:
            klient._make_request("GET", "/project")
        self.assertEqual(klient.access_token, "TEST-access-ny")
        self.assertEqual(klient.refresh_token, "TEST-refresh-rotert")
        self.assertEqual(post.call_args.kwargs["data"]["refresh_token"], "TEST-refresh-rotert")

    @staticmethod
    def _respons_401():
        mock = MagicMock(status_code=401)
        from requests.exceptions import HTTPError
        mock.raise_for_status.side_effect = HTTPError(response=mock)
        return mock

    def test_korrupt_fil_faller_tilbake_som_ved_manglende_fil(self):
        """Funn 7: JSON-rot som ikke er et objekt, ugyldig JSON og ugyldig UTF-8."""
        for innhold in (b"[]", b"null", b"\"tekst\"", b"42", b"{ikke json", b"\xff\xfe\x00\xd8"):
            with self.subTest(innhold=innhold):
                self.fil.write_bytes(innhold)
                klient = self._ny_klient()  # skal ikke kaste
                self.assertEqual(klient.access_token, "TEST-access-env")
                self.assertEqual(klient.refresh_token, "TEST-refresh-env")

    def test_korrupt_fil_etter_401_kaster_ikke(self):
        klient = self._ny_klient()
        self.fil.write_bytes(b"[]")
        klient._send = MagicMock(side_effect=[self._respons_401(), self._respons_401()])
        with patch("ticktick_mcp.src.ticktick_client.requests.post"):
            resultat = klient._make_request("GET", "/project")
        self.assertIn("error", resultat)
        self.assertEqual(klient.refresh_token, "TEST-refresh-gammel")

    def test_refresh_svar_med_ny_streng(self):
        klient = self._refresh_med_svar(
            {"access_token": "TEST-access-ny", "refresh_token": "TEST-refresh-ny"})
        self.assertEqual(klient.refresh_token, "TEST-refresh-ny")
        self.assertEqual(self._fil()["refresh_token"], "TEST-refresh-ny")
        self.assertEqual(self._ny_klient().refresh_token, "TEST-refresh-ny")

    def test_tom_streng_i_fil_ved_oppstart_gir_none(self):
        self.fil.write_text(json.dumps(
            {"access_token": "TEST-access-fil", "refresh_token": ""}), encoding="utf-8")
        self.assertIsNone(self._ny_klient().refresh_token,
                          "tom streng i fila skal ikke falle tilbake til miljoe")

    def test_tom_streng_i_fil_etter_401_gir_none(self):
        klient = self._ny_klient()
        self.fil.write_text(json.dumps(
            {"access_token": "TEST-access-ny", "refresh_token": ""}), encoding="utf-8")
        respons_401 = MagicMock(status_code=401)
        respons_ok = MagicMock(status_code=200, text="{}")
        respons_ok.json.return_value = {}
        respons_ok.raise_for_status.return_value = None
        klient._send = MagicMock(side_effect=[respons_401, respons_ok])
        klient._make_request("GET", "/project")
        self.assertEqual(klient.access_token, "TEST-access-ny")
        self.assertIsNone(klient.refresh_token)


class TestAuthSkriverTokenfilOgEnv(unittest.TestCase):
    """authenticate/auth.py skal skrive .env uten "None" OG den delte tokenfila
    (som vinner ved oppstart), i samme format som klienten. Kun TEST-verdier."""

    def _lagre(self, tokens, eksisterende_env=None):
        from ticktick_mcp.src.auth import TickTickAuth
        with patch.dict("os.environ", {}, clear=True), \
                patch("ticktick_mcp.src.auth.load_dotenv"), \
                i_tom_mappe() as mappe:
            fil = Path(mappe) / "delt" / "tokens.json"
            if eksisterende_env:
                (Path(mappe) / ".env").write_text(eksisterende_env, encoding="utf-8")
            auth = TickTickAuth(client_id="TEST-cid", client_secret="TEST-csecret")
            auth.tokens = tokens
            with patch("ticktick_mcp.src.ticktick_client.TOKEN_FILE", fil):
                auth._save_tokens_to_env()
            env = (Path(mappe) / ".env").read_text(encoding="utf-8")
            data = json.loads(fil.read_text(encoding="utf-8")) if fil.exists() else None
        return env, data

    def test_null_refresh_gir_ikke_none_i_env_og_null_i_tokenfil(self):
        env, data = self._lagre({"access_token": "TEST-a", "refresh_token": None},
                                "TICKTICK_REFRESH_TOKEN=TEST-gammel\n")
        self.assertNotIn("None", env)
        self.assertIn("TICKTICK_REFRESH_TOKEN=\n", env)
        self.assertEqual(data["access_token"], "TEST-a")
        self.assertIsNone(data["refresh_token"])
        self.assertEqual(data["kilde"], "python-auth")
        self.assertIsNotNone(datetime.fromisoformat(data["oppdatert"]))
        self.assertEqual(set(data), {"access_token", "refresh_token", "oppdatert", "kilde"})

    def test_refresh_uten_noekkel_gir_null_i_tokenfil(self):
        env, data = self._lagre({"access_token": "TEST-a"})
        self.assertNotIn("None", env)
        self.assertIsNone(data["refresh_token"])

    def test_refresh_streng_skrives_begge_steder(self):
        env, data = self._lagre({"access_token": "TEST-a", "refresh_token": "TEST-r"})
        self.assertIn("TICKTICK_REFRESH_TOKEN=TEST-r\n", env)
        self.assertEqual(data["refresh_token"], "TEST-r")

    def test_ny_innlogging_via_auth_faar_effekt_ved_klientoppstart(self):
        """Funn 3: fila vinner over .env, saa auth maa skrive den for at innloggingen skal virke."""
        from ticktick_mcp.src.auth import TickTickAuth
        from ticktick_mcp.src.ticktick_client import TickTickClient
        with patch.dict("os.environ", {}, clear=True), \
                patch("ticktick_mcp.src.auth.load_dotenv"), \
                patch("ticktick_mcp.src.ticktick_client.load_dotenv"), \
                i_tom_mappe() as mappe:
            fil = Path(mappe) / "tokens.json"
            fil.write_text(json.dumps({"access_token": "TEST-utgaatt",
                                       "refresh_token": None}), encoding="utf-8")
            auth = TickTickAuth(client_id="TEST-cid", client_secret="TEST-csecret")
            auth.tokens = {"access_token": "TEST-ny-innlogging", "refresh_token": "TEST-ny-refresh"}
            with patch("ticktick_mcp.src.ticktick_client.TOKEN_FILE", fil):
                auth._save_tokens_to_env()
                klient = TickTickClient()
        self.assertEqual(klient.access_token, "TEST-ny-innlogging")
        self.assertEqual(klient.refresh_token, "TEST-ny-refresh")


class TestNormaliserRefreshToken(unittest.TestCase):
    """Funn 10: regelen "null eller tom betyr ingen" ligger i en hjelper."""

    def test_regelen(self):
        from ticktick_mcp.src.ticktick_client import normaliser_refresh_token
        self.assertEqual(normaliser_refresh_token("TEST-r"), "TEST-r")
        for verdi in (None, "", "   ", 0, 42, [], {}, False):
            self.assertIsNone(normaliser_refresh_token(verdi), repr(verdi))


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
