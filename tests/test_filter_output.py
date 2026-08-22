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


if __name__ == "__main__":
    unittest.main()
