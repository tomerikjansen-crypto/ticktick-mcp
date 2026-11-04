"""
Steg 13 / E0: tokenet bor bare i ~/.ticktick/tokens.json.

Dekker:
1. Oppstart (server.initialize_client og cli.check_auth_setup) godtar en gyldig
   tokenfil med tomt miljoe, og feiler kontrollert uten begge.
2. cli kaller aldri input() uten terminal og skriver ingenting til stdout.
3. Etter fornyelse og etter innlogging finnes verken access- eller
   refresh-verdien i noen .env i arbeidsmappa (raabytes).

Alt kjoerer mot tmp_path. Den ekte tokenfila og ekte .env roeres aldri:
TOKEN_FILE, hjemmemappe og arbeidsmappe pekes til tmp_path, miljoeet toemmes
og load_dotenv er satt ut. Kun TEST-verdier, ingen nettkall.
"""

import io
import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ticktick_mcp.src import ticktick_client as klientmodul

KLIENT = "ticktick_mcp.src.ticktick_client"


@pytest.fixture
def isolert(tmp_path, monkeypatch):
    """Tomt miljoe, tmp-arbeidsmappe og tokenfil i tmp. Gir stien til tokenfila (ikke skrevet)."""
    for navn in ("TICKTICK_ACCESS_TOKEN", "TICKTICK_REFRESH_TOKEN",
                 "TICKTICK_CLIENT_ID", "TICKTICK_CLIENT_SECRET"):
        monkeypatch.delenv(navn, raising=False)
    monkeypatch.setenv("TICKTICK_BASE_URL", "https://ticktick.invalid/open/v1")
    monkeypatch.setenv("TICKTICK_TOKEN_URL", "https://ticktick.invalid/oauth/token")
    monkeypatch.chdir(tmp_path)
    fil = tmp_path / "hjem" / "tokens.json"
    fil.parent.mkdir()
    monkeypatch.setattr(f"{KLIENT}.TOKEN_FILE", fil)
    monkeypatch.setattr(f"{KLIENT}.load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr("ticktick_mcp.src.auth.load_dotenv", lambda *a, **k: False)
    return fil


def _skriv_tokenfil(fil, access="TEST-fil-access", refresh="TEST-fil-refresh"):
    fil.write_text(json.dumps({"access_token": access, "refresh_token": refresh}),
                   encoding="utf-8")


def _env_filer(rot):
    """Alle filer som heter .env eller .env.* under rot."""
    return [p for p in rot.rglob(".env*") if p.is_file()]


# --- 1. Oppstart ------------------------------------------------------------------

def test_initialize_client_starter_med_bare_tokenfil_og_tomt_miljo(isolert):
    from ticktick_mcp.src import server
    _skriv_tokenfil(isolert)
    with patch.object(klientmodul.TickTickClient, "get_projects", return_value=[{"id": "p1"}]):
        assert server.initialize_client() is True
    assert server.ticktick.access_token == "TEST-fil-access"
    assert server.ticktick.refresh_token == "TEST-fil-refresh"


def test_initialize_client_feiler_kontrollert_uten_fil_og_miljo(isolert, caplog):
    from ticktick_mcp.src import server
    with caplog.at_level(logging.ERROR, logger="ticktick_mcp.src.server"), \
            patch.object(server, "TickTickClient") as klientklasse:
        assert server.initialize_client() is False
    klientklasse.assert_not_called()
    assert "ticktick-auth.mjs" in caplog.text


def test_initialize_client_ugyldig_fil_gir_ikke_miljofallback(isolert, monkeypatch):
    """Fila er fasit: en eksisterende, men ugyldig fil gir ikke fallback til miljoeet."""
    from ticktick_mcp.src import server
    isolert.write_text("{ikke json", encoding="utf-8")
    monkeypatch.setenv("TICKTICK_ACCESS_TOKEN", "TEST-miljo-access")
    with patch.object(server, "TickTickClient") as klientklasse:
        assert server.initialize_client() is False
    klientklasse.assert_not_called()


def test_initialize_client_bruker_miljoet_bare_naar_fila_mangler(isolert, monkeypatch):
    from ticktick_mcp.src import server
    monkeypatch.setenv("TICKTICK_ACCESS_TOKEN", "TEST-miljo-access")
    with patch.object(klientmodul.TickTickClient, "get_projects", return_value=[]):
        assert server.initialize_client() is True
    assert server.ticktick.access_token == "TEST-miljo-access"


def test_check_auth_setup_godtar_tokenfil_uten_miljo(isolert):
    from ticktick_mcp import cli
    assert cli.check_auth_setup() is False
    _skriv_tokenfil(isolert)
    assert cli.check_auth_setup() is True


def test_check_auth_setup_ugyldig_fil_er_ikke_nok_selv_med_miljo(isolert, monkeypatch):
    from ticktick_mcp import cli
    isolert.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("TICKTICK_ACCESS_TOKEN", "TEST-miljo-access")
    assert cli.check_auth_setup() is False


# --- 2. cli uten terminal ----------------------------------------------------------

class _IkkeTerminal(io.StringIO):
    def isatty(self):
        return False


def _kjor_cli(monkeypatch, argv):
    from ticktick_mcp import cli
    monkeypatch.setattr(sys, "argv", ["ticktick-mcp", *argv])
    monkeypatch.setattr(sys, "stdin", _IkkeTerminal())

    def forbudt(*a, **k):
        raise AssertionError("input() ble kalt uten terminal")

    monkeypatch.setattr("builtins.input", forbudt)
    return cli


def test_cli_uten_terminal_og_uten_tilgang_avslutter_med_kode_1_paa_stderr(
        isolert, monkeypatch, capsys):
    cli = _kjor_cli(monkeypatch, ["run"])
    with patch.object(cli, "server_main") as server_main, pytest.raises(SystemExit) as avslutning:
        cli.main()
    assert avslutning.value.code == 1
    server_main.assert_not_called()
    ut = capsys.readouterr()
    assert ut.out == "", "stdout er MCP-protokollen og skal vaere tom"
    assert "ticktick-auth.mjs" in ut.err


def test_cli_uten_argumenter_oppfoerer_seg_som_run(isolert, monkeypatch, capsys):
    cli = _kjor_cli(monkeypatch, [])
    with patch.object(cli, "server_main"), pytest.raises(SystemExit) as avslutning:
        cli.main()
    assert avslutning.value.code == 1
    assert capsys.readouterr().out == ""


def test_cli_med_tokenfil_starter_serveren_uten_stdout_og_uten_input(
        isolert, monkeypatch, capsys):
    _skriv_tokenfil(isolert)
    cli = _kjor_cli(monkeypatch, ["run"])
    with patch.object(cli, "server_main") as server_main:
        cli.main()
    server_main.assert_called_once()
    assert capsys.readouterr().out == ""


def _prosess(tmp_path, tokenfil_innhold=None):
    """Ekte cli-prosess med stdin som roer (som MCP-stdio; NUL-enheten rapporterer isatty() paa Windows),
    tmp som hjemmemappe og arbeidsmappe, tomt TICKTICK-miljoe."""
    import os
    import subprocess
    import ticktick_mcp
    hjem = tmp_path / "prosesshjem"
    (hjem / ".ticktick").mkdir(parents=True)
    if tokenfil_innhold is not None:
        (hjem / ".ticktick" / "tokens.json").write_text(tokenfil_innhold, encoding="utf-8")
    arbeid = tmp_path / "prosessarbeid"
    arbeid.mkdir()
    miljo = {k: v for k, v in os.environ.items() if not k.startswith("TICKTICK_")}
    miljo.update({"USERPROFILE": str(hjem), "HOME": str(hjem),
                  "TICKTICK_BASE_URL": "https://ticktick.invalid/open/v1",
                  "TICKTICK_TOKEN_URL": "https://ticktick.invalid/oauth/token",
                  "PYTHONPATH": str(Path(ticktick_mcp.__file__).parent.parent)})
    return subprocess.run([sys.executable, "-m", "ticktick_mcp.cli", "run"], cwd=arbeid, env=miljo,
                          input="", capture_output=True, text=True, timeout=120)


def test_cli_som_prosess_uten_tilgang_gir_kode_1_tom_stdout_og_melding_paa_stderr(tmp_path):
    ferdig = _prosess(tmp_path)
    assert ferdig.returncode == 1
    assert ferdig.stdout == ""
    assert "ticktick-auth.mjs" in ferdig.stderr


def test_cli_som_prosess_med_bare_tokenfil_kommer_forbi_tilgangssjekken(tmp_path):
    """Tokenfila alene slipper gjennom til API-kallet (som feiler mot .invalid): stdout er fortsatt tom."""
    ferdig = _prosess(tmp_path, json.dumps({"access_token": "TEST-x", "refresh_token": None}))
    assert ferdig.stdout == ""
    assert "Ingen TickTick-tilgang" not in ferdig.stderr


# --- 3. Ingen tokenkopier i .env ----------------------------------------------------

VERDIER = (b"TEST-access-gammel", b"TEST-refresh-gammel", b"TEST-access-ny", b"TEST-refresh-ny")


def _sjekk_ingen_tokenverdier_i_env(rot):
    for env in _env_filer(rot):
        raa = env.read_bytes()
        for verdi in VERDIER:
            assert verdi not in raa, f"{env} inneholder {verdi!r}"
        assert b"TICKTICK_ACCESS_TOKEN" not in raa
        assert b"TICKTICK_REFRESH_TOKEN" not in raa


def test_fornyelse_lager_ingen_env_med_tokenverdier(isolert, tmp_path, monkeypatch):
    monkeypatch.setenv("TICKTICK_CLIENT_ID", "TEST-cid")
    monkeypatch.setenv("TICKTICK_CLIENT_SECRET", "TEST-csecret")
    _skriv_tokenfil(isolert, "TEST-access-gammel", "TEST-refresh-gammel")
    klient = klientmodul.TickTickClient()
    respons = MagicMock()
    respons.raise_for_status.return_value = None
    respons.json.return_value = {"access_token": "TEST-access-ny", "refresh_token": "TEST-refresh-ny"}
    with patch(f"{KLIENT}.requests.post", return_value=respons):
        assert klient._refresh_access_token() is True
    assert json.loads(isolert.read_text(encoding="utf-8"))["access_token"] == "TEST-access-ny"
    assert _env_filer(tmp_path) == [], "fornyelse skal ikke lage eller endre noen .env"
    _sjekk_ingen_tokenverdier_i_env(tmp_path)


def test_fornyelse_lar_eksisterende_env_staa_uendret(isolert, tmp_path, monkeypatch):
    monkeypatch.setenv("TICKTICK_CLIENT_ID", "TEST-cid")
    monkeypatch.setenv("TICKTICK_CLIENT_SECRET", "TEST-csecret")
    env = tmp_path / ".env"
    env.write_bytes(b"# kommentar\nTICKTICK_CLIENT_ID=TEST-cid\nlinje uten likhetstegn\n")
    foer = env.read_bytes()
    _skriv_tokenfil(isolert, "TEST-access-gammel", "TEST-refresh-gammel")
    klient = klientmodul.TickTickClient()
    respons = MagicMock()
    respons.raise_for_status.return_value = None
    respons.json.return_value = {"access_token": "TEST-access-ny", "refresh_token": "TEST-refresh-ny"}
    with patch(f"{KLIENT}.requests.post", return_value=respons):
        assert klient._refresh_access_token() is True
    assert env.read_bytes() == foer


@pytest.mark.parametrize("eksisterende_env", [None, b"TICKTICK_CLIENT_ID=TEST-cid\n",
                                              b"# min .env\nANNET=1"])
def test_innlogging_skriver_tokenparet_bare_til_tokenfila(
        isolert, tmp_path, monkeypatch, eksisterende_env):
    from ticktick_mcp.src.auth import TickTickAuth
    if eksisterende_env is not None:
        (tmp_path / ".env").write_bytes(eksisterende_env)
    auth = TickTickAuth(client_id="TEST-cid", client_secret="TEST-csecret")
    auth.auth_code = "TEST-kode"
    respons = MagicMock()
    respons.raise_for_status.return_value = None
    respons.json.return_value = {"access_token": "TEST-access-ny", "refresh_token": "TEST-refresh-ny"}
    with patch("ticktick_mcp.src.auth.requests.post", return_value=respons):
        melding = auth.exchange_code_for_token()
    assert "successful" in melding.lower()
    data = json.loads(isolert.read_text(encoding="utf-8"))
    assert (data["access_token"], data["refresh_token"]) == ("TEST-access-ny", "TEST-refresh-ny")
    assert set(data) == {"access_token", "refresh_token", "oppdatert", "kilde"}
    _sjekk_ingen_tokenverdier_i_env(tmp_path)
    assert "tokens.json" in melding
    if eksisterende_env is not None:
        # Eksisterende innhold er bevart (bare manglende klientverdier legges til).
        assert (tmp_path / ".env").read_bytes().startswith(eksisterende_env)


def test_innlogging_uten_nye_klientverdier_roerer_ikke_env(isolert, tmp_path):
    from ticktick_mcp.src.auth import TickTickAuth
    env = tmp_path / ".env"
    env.write_bytes(b"TICKTICK_CLIENT_ID=TEST-cid\nTICKTICK_CLIENT_SECRET=TEST-csecret\n")
    foer = env.read_bytes()
    auth = TickTickAuth(client_id="TEST-cid", client_secret="TEST-csecret")
    auth.tokens = {"access_token": "TEST-access-ny", "refresh_token": "TEST-refresh-ny"}
    assert auth._save_tokens() == (True, True)
    assert env.read_bytes() == foer
