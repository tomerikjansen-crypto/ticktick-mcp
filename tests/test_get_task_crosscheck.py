"""
Tester for kryssjekk-logikken i get_task (GH-issue #385).

Bakgrunn: GET /project/{p}/task/{t} (enkeltoppslag) viser status 0 ("Active")
ogsaa for en oppgave som er slettet - TickTicks Open API skiller ikke aktiv fra
slettet der. Bevist mekanisk 27.09.2026: en TEST-oppgave opprettet og deretter
slettet fortsatte aa svare status 0 med identisk feltsett som foer sletting.

GET /project/{p}/data returnerer kun "undone tasks under project"
(ticktick-openapi.md), saa en aktiv oppgave skal alltid finnes der. Fiksen
bruker /data som fasit: mangler oppgaven der, vises den som "trolig slettet"
i stedet for "Active". Feiler selve kryssjekken, vises status ALDRI stille
som "Active" - kalleren far en tydelig "kunne ikke kryssjekke"-tekst.
"""

import unittest
from unittest.mock import MagicMock, patch


def _oppgave(**overstyr):
    grunn = {
        "id": "t-1",
        "title": "En oppgave",
        "projectId": "p-1",
        "priority": 3,
        "status": 0,
    }
    grunn.update(overstyr)
    return grunn


class TestVerifyTaskActive(unittest.TestCase):
    """Tester _verify_task_active() direkte (ren logikk, ingen nettverk)."""

    def setUp(self):
        from ticktick_mcp.src import server as modul
        self.modul = modul

    def test_aktiv_oppgave_bekreftes(self):
        """Oppgaven finnes blant prosjektets uavsluttede oppgaver -> bekreftet."""
        klient = MagicMock()
        klient.get_project_with_data.return_value = {
            "project": {"id": "p-1"},
            "tasks": [_oppgave(id="t-1")],
        }
        with patch.object(self.modul, "ticktick", klient):
            resultat = self.modul._verify_task_active("p-1", "t-1")
        self.assertEqual(resultat, "bekreftet")

    def test_slettet_oppgave_mangler_i_prosjektdata(self):
        """Oppgaven er IKKE i /data (papirkurv) -> trolig_slettet, ikke stille aktiv."""
        klient = MagicMock()
        klient.get_project_with_data.return_value = {
            "project": {"id": "p-1"},
            "tasks": [_oppgave(id="annen-oppgave")],
        }
        with patch.object(self.modul, "ticktick", klient):
            resultat = self.modul._verify_task_active("p-1", "t-1")
        self.assertEqual(resultat, "trolig_slettet")

    def test_tom_oppgaveliste_gir_trolig_slettet(self):
        klient = MagicMock()
        klient.get_project_with_data.return_value = {"project": {"id": "p-1"}, "tasks": []}
        with patch.object(self.modul, "ticktick", klient):
            resultat = self.modul._verify_task_active("p-1", "t-1")
        self.assertEqual(resultat, "trolig_slettet")

    def test_feilrespons_gir_krysssjekk_feilet_ikke_aktiv(self):
        """API-feil (dict med 'error') skal ALDRI tolkes som 'oppgaven er aktiv'."""
        klient = MagicMock()
        klient.get_project_with_data.return_value = {"error": "401 Unauthorized"}
        with patch.object(self.modul, "ticktick", klient):
            resultat = self.modul._verify_task_active("p-1", "t-1")
        self.assertEqual(resultat, "krysssjekk_feilet")

    def test_exception_gir_krysssjekk_feilet(self):
        """Nettverksfeil (unntak) skal ogsaa gi krysssjekk_feilet, ikke krasje."""
        klient = MagicMock()
        klient.get_project_with_data.side_effect = ConnectionError("nettverksfeil")
        with patch.object(self.modul, "ticktick", klient):
            resultat = self.modul._verify_task_active("p-1", "t-1")
        self.assertEqual(resultat, "krysssjekk_feilet")

    def test_uventet_format_paa_tasks_gir_krysssjekk_feilet(self):
        klient = MagicMock()
        klient.get_project_with_data.return_value = {"project": {"id": "p-1"}, "tasks": "ikke-en-liste"}
        with patch.object(self.modul, "ticktick", klient):
            resultat = self.modul._verify_task_active("p-1", "t-1")
        self.assertEqual(resultat, "krysssjekk_feilet")

    def test_innboks_prosjekt_id_behandles_generisk(self):
        """Innboksens spesielle prosjekt-id sendes urort til /data - samme kontrakt."""
        klient = MagicMock()
        klient.get_project_with_data.return_value = {
            "project": {"id": "inbox116243349"},
            "tasks": [_oppgave(id="t-1", projectId="inbox116243349")],
        }
        with patch.object(self.modul, "ticktick", klient):
            resultat = self.modul._verify_task_active("inbox116243349", "t-1")
        klient.get_project_with_data.assert_called_once_with("inbox116243349")
        self.assertEqual(resultat, "bekreftet")


class TestFormatTaskStatusBekreftelse(unittest.TestCase):
    """Tester at format_task viser riktig statustekst per bekreftelse-tilstand."""

    def setUp(self):
        from ticktick_mcp.src import server as modul
        self.format_task = modul.format_task

    def test_ingen_bekreftelse_viser_uendret_active(self):
        """Bakoverkompatibilitet: status_bekreftelse=None (default) -> uendret 'Active'."""
        res = self.format_task(_oppgave())
        self.assertIn("Status: Active", res)
        self.assertNotIn("USIKKER", res)
        self.assertNotIn("slettet", res)

    def test_completed_overstyrer_bekreftelse(self):
        """En faktisk fullført oppgave (status 2) skal aldri vise slettet-tekst."""
        res = self.format_task(_oppgave(status=2), status_bekreftelse="trolig_slettet")
        self.assertIn("Status: Completed", res)

    def test_bekreftet_viser_active(self):
        res = self.format_task(_oppgave(), status_bekreftelse="bekreftet")
        self.assertIn("Status: Active", res)
        self.assertNotIn("USIKKER", res)

    def test_trolig_slettet_viser_ikke_bare_active(self):
        """Kjernen i fiksen: en slettet oppgave skal ALDRI fremstaa som ren 'Active'."""
        res = self.format_task(_oppgave(), status_bekreftelse="trolig_slettet")
        self.assertIn("Trolig slettet", res)
        self.assertIn("papirkurv", res)
        # Skal ikke vaere den uskyldige, ubetingede "Status: Active\n"-linja
        self.assertNotIn("Status: Active\n", res)

    def test_krysssjekk_feilet_viser_usikker_ikke_stille_active(self):
        res = self.format_task(_oppgave(), status_bekreftelse="krysssjekk_feilet")
        self.assertIn("USIKKER", res)
        self.assertNotIn("Status: Active\n", res)


class TestGetTaskToolBrukerKryssjekk(unittest.TestCase):
    """Integrasjonstest av get_task()-verktøyet: kaller kryssjekk kun for ikke-fullførte."""

    def setUp(self):
        from ticktick_mcp.src import server as modul
        self.modul = modul

    def test_get_task_kryssjekker_ikke_fullfort_oppgave(self):
        klient = MagicMock()
        klient.get_task.return_value = _oppgave(status=0)
        klient.get_project_with_data.return_value = {"project": {"id": "p-1"}, "tasks": []}
        with patch.object(self.modul, "ticktick", klient):
            resultat = self._kjor_async(self.modul.get_task("p-1", "t-1"))
        klient.get_project_with_data.assert_called_once_with("p-1")
        self.assertIn("Trolig slettet", resultat)

    def test_get_task_hopper_over_kryssjekk_for_fullfort_oppgave(self):
        """Ingen grunn til aa kryssjekke en oppgave API-et alt sier er fullført."""
        klient = MagicMock()
        klient.get_task.return_value = _oppgave(status=2)
        with patch.object(self.modul, "ticktick", klient):
            resultat = self._kjor_async(self.modul.get_task("p-1", "t-1"))
        klient.get_project_with_data.assert_not_called()
        self.assertIn("Status: Completed", resultat)

    @staticmethod
    def _kjor_async(coro):
        import asyncio
        return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
