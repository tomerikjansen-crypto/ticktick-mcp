"""
Tester for _get_project_tasks_by_filter i server.py (flagg 112).

Prosjekter uten treff skal utelates fra output. Ved null treff totalt
skal en samlet oppsummeringslinje vises i stedet for en tom blokk per prosjekt.
"""

import unittest
from unittest.mock import MagicMock, patch


def _prosjekt(pid, navn, closed=False):
    return {"id": pid, "name": navn, "closed": closed}


def _oppgave(tittel):
    return {"id": f"t-{tittel}", "title": tittel, "projectId": "p"}


class TestFilterOutput(unittest.TestCase):

    def setUp(self):
        from ticktick_mcp.src import server as modul
        self.modul = modul
        self.prosjekter = [
            _prosjekt("p1", "Liste A"),
            _prosjekt("p2", "Liste B"),
            _prosjekt("p3", "Liste C"),
        ]
        data = {
            "p1": {"tasks": [_oppgave("worktree-bug"), _oppgave("annet")]},
            "p2": {"tasks": []},
            "p3": {"tasks": [_oppgave("irrelevant")]},
        }
        klient = MagicMock()
        klient.get_project_with_data.side_effect = lambda pid: data[pid]
        self.klient = klient

    def _kjor(self, filter_func, navn="matched"):
        with patch.object(self.modul, "ticktick", self.klient):
            return self.modul._get_project_tasks_by_filter(self.prosjekter, filter_func, navn)

    def test_prosjekter_uten_treff_utelates(self):
        res = self._kjor(lambda t: "worktree" in t["title"])
        self.assertIn("Liste A", res)
        self.assertIn("worktree-bug", res)
        self.assertNotIn("Liste B", res)
        self.assertNotIn("Liste C", res)
        self.assertNotIn("0 tasks", res)
        self.assertIn("1 of 3 projects", res)

    def test_ingen_treff_gir_samlet_linje(self):
        res = self._kjor(lambda t: False)
        self.assertIn("0 tasks matching 'matched' across 3 projects", res)
        self.assertNotIn("Liste A", res)
        self.assertNotIn("Project 1", res)

    def test_alle_oppgaver_inkluderes(self):
        res = self._kjor(lambda t: True, "included")
        self.assertIn("Liste A", res)
        self.assertIn("Liste C", res)
        self.assertNotIn("Liste B", res)
        self.assertIn("2 of 3 projects", res)

    def test_tom_prosjektliste(self):
        res = self._kjor(lambda t: True)
        self.assertEqual(
            self.modul._get_project_tasks_by_filter([], lambda t: True, "x"),
            "No projects found.",
        )


class TestUlesteLister(unittest.TestCase):
    """#850: lister som ikke kunne leses skal meldes øverst i svaret, og et svar
    der alle lister feilet skal ikke se ut som «ingen oppgaver»."""

    def setUp(self):
        from ticktick_mcp.src import server as modul
        self.modul = modul
        self.prosjekter = [
            _prosjekt("p1", "Liste A"),
            _prosjekt("p2", "Liste B"),
            _prosjekt("p3", "Liste C"),
            _prosjekt("p4", "Lukket", closed=True),
        ]

    def _kjor(self, data, filter_func=lambda t: True):
        klient = MagicMock()
        klient.get_project_with_data.side_effect = lambda pid: data[pid]
        with patch.object(self.modul, "ticktick", klient):
            return self.modul._get_project_tasks_by_filter(self.prosjekter, filter_func, "x")

    def test_delvis_feil_gir_varsel_overst_og_beholder_treff(self):
        res = self._kjor({
            "p1": {"tasks": [_oppgave("a")]},
            "p2": {"error": "500 Server Error"},
            "p3": {"error": "429"},
        })
        self.assertTrue(res.startswith("ADVARSEL: 2 av 3 lister kunne ikke leses"), res[:120])
        self.assertIn("Liste B", res.split("\n")[0])
        self.assertIn("Liste A", res)

    def test_delvis_feil_uten_treff_viser_varsel_og_ikke_bare_null_oppgaver(self):
        res = self._kjor({
            "p1": {"tasks": []},
            "p2": {"error": "500"},
            "p3": {"tasks": []},
        }, lambda t: False)
        self.assertTrue(res.startswith("ADVARSEL: 1 av 3 lister kunne ikke leses"))
        self.assertIn("0 tasks matching 'x'", res)

    def test_alle_lister_feilet_ser_ikke_ut_som_ingen_oppgaver(self):
        res = self._kjor({
            "p1": {"error": "500"},
            "p2": {"error": "500"},
            "p3": {"error": "500"},
        })
        self.assertIn("3 av 3 lister kunne ikke leses", res)
        self.assertNotIn("0 tasks matching", res)
        self.assertIn("IKKE", res)

    def test_stopper_etter_tre_uleste_lister_paa_rad(self):
        prosjekter = [_prosjekt(f"q{n}", f"Q{n}") for n in range(1, 8)]
        klient = MagicMock()
        klient.get_project_with_data.return_value = {"error": "500"}
        with patch.object(self.modul, "ticktick", klient):
            res = self.modul._get_project_tasks_by_filter(prosjekter, lambda t: True, "x")
        self.assertEqual(klient.get_project_with_data.call_count, 3)
        self.assertTrue(res.startswith("ADVARSEL: 3 av 7 lister kunne ikke leses, 4 ikke forsøkt"),
                        res[:120])
        self.assertIn("IKKE", res)

    def test_to_uleste_og_en_lest_stopper_ikke(self):
        prosjekter = [_prosjekt(f"q{n}", f"Q{n}") for n in range(1, 7)]
        data = {"q1": {"error": "x"}, "q2": {"error": "x"}, "q3": {"tasks": [_oppgave("a")]},
                "q4": {"error": "x"}, "q5": {"error": "x"}, "q6": {"tasks": []}}
        klient = MagicMock()
        klient.get_project_with_data.side_effect = lambda pid: data[pid]
        with patch.object(self.modul, "ticktick", klient):
            res = self.modul._get_project_tasks_by_filter(prosjekter, lambda t: True, "x")
        self.assertEqual(klient.get_project_with_data.call_count, 6)
        self.assertTrue(res.startswith("ADVARSEL: 4 av 6 lister kunne ikke leses (svaret"))
        self.assertNotIn("ikke forsøkt", res)

    def test_ingen_feil_gir_ingen_varsel(self):
        res = self._kjor({
            "p1": {"tasks": [_oppgave("a")]},
            "p2": {"tasks": []},
            "p3": {"tasks": []},
        })
        self.assertNotIn("kunne ikke leses", res)


if __name__ == "__main__":
    unittest.main()
