"""
Tester for kompakt-modus i format_task.

Bakgrunn: get_project_tasks på en liste med 61 oppgaver returnerte 71 632 tegn og
sprengte MCP-ens token-tak, slik at kalleren måtte lese resultatet fra fil. Måling
av den faktiske outputen viste at oppgavebeskrivelsene (`content`) sto for 82,2 %
av tegnene - median 879 tegn, største 2 750 - mens metadata sto for 16,5 %.

Fiksen trunkerer `content` og kollapser subtask-lister KUN når en oppgave vises som
del av en liste. Enkeltoppgave-visning (get_task, create_task, update_task) er
uendret, så ingen kaller mister data den hadde før.
"""

import unittest


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


class TestFormatTaskKompakt(unittest.TestCase):

    def setUp(self):
        from ticktick_mcp.src import server as modul
        self.modul = modul
        self.format_task = modul.format_task
        self.grense = modul.KOMPAKT_CONTENT_GRENSE

    # --- Standardoppførsel skal være uendret (bakoverkompatibilitet) ---

    def test_standard_beholder_hele_content(self):
        lang = "A" * (self.grense + 500)
        res = self.format_task(_oppgave(content=lang))
        self.assertIn(lang, res)
        self.assertNotIn("...", res)

    def test_standard_lister_hver_subtask(self):
        oppg = _oppgave(items=[
            {"title": "Første", "status": 1},
            {"title": "Andre", "status": 0},
        ])
        res = self.format_task(oppg)
        self.assertIn("Første", res)
        self.assertIn("Andre", res)

    # --- Kompakt-modus ---

    def test_kompakt_trunkerer_lang_content(self):
        lang = "B" * (self.grense + 500)
        res = self.format_task(_oppgave(content=lang), kompakt=True)
        self.assertNotIn(lang, res)
        self.assertIn("B" * self.grense, res)

    def test_kompakt_oppgir_full_lengde_og_veien_til_resten(self):
        lang = "C" * 2750
        res = self.format_task(_oppgave(content=lang), kompakt=True)
        self.assertIn("2750", res, "full lengde må oppgis så kalleren vet hva den mangler")
        self.assertIn("get_task", res, "kalleren må få vite hvordan den henter resten")

    def test_kompakt_lar_kort_content_staa_urort(self):
        kort = "D" * (self.grense - 1)
        res = self.format_task(_oppgave(content=kort), kompakt=True)
        self.assertIn(kort, res)
        self.assertNotIn("get_task", res)

    def test_kompakt_beholder_alle_metadatafelt(self):
        oppg = _oppgave(content="E" * 3000, startDate="2026-08-27T08:00:00+0000",
                        dueDate="2026-08-28T08:00:00+0000")
        res = self.format_task(oppg, kompakt=True)
        for forventet in ("ID: t-1", "Title: En oppgave", "Project ID: p-1",
                          "Priority: Medium", "Status: Active",
                          "Start Date:", "Due Date:"):
            self.assertIn(forventet, res, f"{forventet} skal aldri trunkeres bort")

    def test_kompakt_kollapser_subtasks_til_antall(self):
        oppg = _oppgave(items=[{"title": f"Deloppgave {i}", "status": 0} for i in range(12)])
        res = self.format_task(oppg, kompakt=True)
        self.assertIn("12", res)
        self.assertNotIn("Deloppgave 7", res)

    def test_kompakt_uten_content_krasjer_ikke(self):
        res = self.format_task(_oppgave(), kompakt=True)
        self.assertIn("Title: En oppgave", res)

    def test_kompakt_paa_grensen_trunkerer_ikke(self):
        akkurat = "F" * self.grense
        res = self.format_task(_oppgave(content=akkurat), kompakt=True)
        self.assertIn(akkurat, res)
        self.assertNotIn("get_task", res)


class TestListerBrukerKompakt(unittest.TestCase):
    """Listene skal be om kompakt; enkeltoppgave-visning skal ikke."""

    def setUp(self):
        from unittest.mock import MagicMock, patch
        from ticktick_mcp.src import server as modul
        self.modul = modul
        self.patch = patch
        self.MagicMock = MagicMock
        self.lang = "G" * 3000

    def test_filtrert_liste_trunkerer(self):
        prosjekter = [{"id": "p1", "name": "Liste A", "closed": False}]
        data = {"p1": {"tasks": [_oppgave(content=self.lang)]}}
        klient = self.MagicMock()
        klient.get_project_with_data.side_effect = lambda pid: data[pid]
        with self.patch.object(self.modul, "ticktick", klient):
            res = self.modul._get_project_tasks_by_filter(
                prosjekter, lambda t: True, "matched")
        self.assertNotIn(self.lang, res)
        self.assertIn("get_task", res)


if __name__ == "__main__":
    unittest.main()
