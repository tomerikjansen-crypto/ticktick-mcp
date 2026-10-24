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
            "inbox": {"tasks": []},
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
        self.assertIn("1 of 4 projects", res)

    def test_ingen_treff_gir_samlet_linje(self):
        res = self._kjor(lambda t: False)
        self.assertIn("0 tasks matching 'matched' across 4 projects", res)
        self.assertNotIn("Liste A", res)
        self.assertNotIn("Project 1", res)

    def test_alle_oppgaver_inkluderes(self):
        res = self._kjor(lambda t: True, "included")
        self.assertIn("Liste A", res)
        self.assertIn("Liste C", res)
        self.assertNotIn("Liste B", res)
        self.assertIn("2 of 4 projects", res)

    def test_tom_prosjektliste_leser_fortsatt_innboksen(self):
        # #923: tom prosjektliste gir ikke lenger tidlig retur, innboksen leses likevel.
        klient = MagicMock()
        klient.get_project_with_data.return_value = {"tasks": []}
        with patch.object(self.modul, "ticktick", klient):
            res = self.modul._get_project_tasks_by_filter([], lambda t: True, "x")
        klient.get_project_with_data.assert_called_once_with("inbox")
        self.assertIn("0 tasks matching 'x' across 1 projects", res)


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
        data = {"inbox": {"tasks": []}, **data}
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
        self.assertTrue(res.startswith("ADVARSEL: 2 av 4 lister kunne ikke leses"), res[:120])
        self.assertIn("Liste B", res.split("\n")[0])
        self.assertIn("Liste A", res)

    def test_delvis_feil_uten_treff_viser_varsel_og_ikke_bare_null_oppgaver(self):
        res = self._kjor({
            "p1": {"tasks": []},
            "p2": {"error": "500"},
            "p3": {"tasks": []},
        }, lambda t: False)
        self.assertTrue(res.startswith("ADVARSEL: 1 av 4 lister kunne ikke leses"))
        self.assertIn("0 tasks matching 'x'", res)

    def test_alle_lister_feilet_ser_ikke_ut_som_ingen_oppgaver(self):
        res = self._kjor({
            "p1": {"error": "500"},
            "p2": {"error": "500"},
            "p3": {"error": "500"},
            "inbox": {"error": "500"},
        })
        self.assertIn("3 av 4 lister kunne ikke leses, 1 ikke forsøkt", res)
        self.assertNotIn("0 tasks matching", res)
        self.assertIn("IKKE", res)

    def test_stopper_etter_tre_uleste_lister_paa_rad(self):
        prosjekter = [_prosjekt(f"q{n}", f"Q{n}") for n in range(1, 8)]
        klient = MagicMock()
        klient.get_project_with_data.return_value = {"error": "500"}
        with patch.object(self.modul, "ticktick", klient):
            res = self.modul._get_project_tasks_by_filter(prosjekter, lambda t: True, "x")
        self.assertEqual(klient.get_project_with_data.call_count, 3)
        self.assertTrue(res.startswith("ADVARSEL: 3 av 8 lister kunne ikke leses, 5 ikke forsøkt"),
                        res[:120])
        self.assertIn("IKKE", res)

    def test_to_uleste_og_en_lest_stopper_ikke(self):
        prosjekter = [_prosjekt(f"q{n}", f"Q{n}") for n in range(1, 7)]
        data = {"q1": {"error": "x"}, "q2": {"error": "x"}, "q3": {"tasks": [_oppgave("a")]},
                "q4": {"error": "x"}, "q5": {"error": "x"}, "q6": {"tasks": []},
                "inbox": {"tasks": []}}
        klient = MagicMock()
        klient.get_project_with_data.side_effect = lambda pid: data[pid]
        with patch.object(self.modul, "ticktick", klient):
            res = self.modul._get_project_tasks_by_filter(prosjekter, lambda t: True, "x")
        self.assertEqual(klient.get_project_with_data.call_count, 7)
        self.assertTrue(res.startswith("ADVARSEL: 4 av 7 lister kunne ikke leses (svaret"))
        self.assertNotIn("ikke forsøkt", res)

    def test_ingen_feil_gir_ingen_varsel(self):
        res = self._kjor({
            "p1": {"tasks": [_oppgave("a")]},
            "p2": {"tasks": []},
            "p3": {"tasks": []},
        })
        self.assertNotIn("kunne ikke leses", res)


def _inbox_oppgave(tittel, **ekstra):
    return {"id": f"i-{tittel}", "title": tittel, "projectId": "inbox131224991", **ekstra}


class TestInnboksen(unittest.TestCase):
    """#923: innboksen finnes ikke i prosjektlisten, men skal med i filterverktøyene
    via samme løkke som vanlige prosjekter (filtrering, telling, ADVARSEL, stoppregel)."""

    def setUp(self):
        from ticktick_mcp.src import server as modul
        self.modul = modul
        self.prosjekter = [_prosjekt("p1", "Liste A"), _prosjekt("p4", "Lukket", closed=True)]

    def _kjor(self, data, filter_func=lambda t: True, navn="x", prosjekter=None):
        klient = MagicMock()
        klient.get_project_with_data.side_effect = lambda pid: data[pid]
        with patch.object(self.modul, "ticktick", klient):
            res = self.modul._get_project_tasks_by_filter(
                self.prosjekter if prosjekter is None else prosjekter, filter_func, navn)
        return res, klient

    def test_innboksoppgave_kommer_med_i_sok(self):
        res, klient = self._kjor(
            {"p1": {"tasks": [_oppgave("annet")]}, "inbox": {"tasks": [_inbox_oppgave("worktree-hurtig")]}},
            lambda t: "worktree" in t["title"], "matching 'worktree'")
        self.assertIn("worktree-hurtig", res)
        self.assertIn("Innboks", res)
        self.assertIn("inbox131224991", res)  # oppgavens egen projectId beholdes
        self.assertNotIn("Liste A", res)
        self.assertIn("1 of 2 projects", res)

    def test_innboksoppgave_kommer_med_i_alle_oppgaver(self):
        res, _ = self._kjor({"p1": {"tasks": [_oppgave("a")]}, "inbox": {"tasks": [_inbox_oppgave("b")]}},
                            navn="included")
        self.assertIn("Liste A", res)
        self.assertIn("Innboks", res)
        self.assertIn("2 of 2 projects", res)

    def test_innboksoppgave_kommer_med_i_forfallsfilter(self):
        forfalt = _inbox_oppgave("forfalt", dueDate="2020-01-01T10:00:00.000+0000")
        res, _ = self._kjor(
            {"p1": {"tasks": [_oppgave("a")]}, "inbox": {"tasks": [forfalt, _inbox_oppgave("uten-dato")]}},
            self.modul._is_task_overdue, "overdue")
        self.assertIn("forfalt", res)
        self.assertNotIn("uten-dato", res)
        self.assertIn("Innboks", res)

    def test_innboksoppgave_uten_vanlige_prosjekter(self):
        res, klient = self._kjor({"inbox": {"tasks": [_inbox_oppgave("kun-innboks")]}}, prosjekter=[])
        self.assertIn("kun-innboks", res)
        self.assertNotIn("No projects found", res)
        self.assertIn("1 of 1 projects", res)

    def test_innboksen_leses_sist(self):
        res, klient = self._kjor({"p1": {"tasks": []}, "inbox": {"tasks": []}})
        self.assertEqual([c.args[0] for c in klient.get_project_with_data.call_args_list], ["p1", "inbox"])

    def test_ulesbar_innboks_gir_advarsel_og_riktig_telling(self):
        res, _ = self._kjor({"p1": {"tasks": [_oppgave("a")]}, "inbox": {"error": "500"}})
        self.assertTrue(res.startswith("ADVARSEL: 1 av 2 lister kunne ikke leses"), res[:120])
        self.assertIn("Innboks", res.split("\n")[0])
        self.assertIn("Liste A", res)

    def test_bare_innboks_og_den_er_ulesbar_ser_ikke_ut_som_ingen_oppgaver(self):
        res, _ = self._kjor({"inbox": {"error": "500"}}, prosjekter=[])
        self.assertIn("1 av 1 lister kunne ikke leses", res)
        self.assertIn("IKKE", res)
        self.assertNotIn("0 tasks matching", res)

    def test_tre_feil_paa_rad_gir_hoyst_tre_oppslag_ogsaa_med_innboksen(self):
        prosjekter = [_prosjekt(f"q{n}", f"Q{n}") for n in range(1, 4)]
        klient = MagicMock()
        klient.get_project_with_data.return_value = {"error": "500"}
        with patch.object(self.modul, "ticktick", klient):
            res = self.modul._get_project_tasks_by_filter(prosjekter, lambda t: True, "x")
        self.assertEqual(klient.get_project_with_data.call_count, 3)
        self.assertTrue(res.startswith("ADVARSEL: 3 av 4 lister kunne ikke leses, 1 ikke forsøkt"), res[:120])
        self.assertNotIn("inbox", [c.args[0] for c in klient.get_project_with_data.call_args_list])

    def test_lukkede_prosjekter_hoppes_over_men_innboksen_leses(self):
        res, klient = self._kjor({"p1": {"tasks": []}, "inbox": {"tasks": [_inbox_oppgave("i")]}})
        lest = [c.args[0] for c in klient.get_project_with_data.call_args_list]
        self.assertNotIn("p4", lest)
        self.assertIn("inbox", lest)

    def test_innboksblokken_peker_paa_oppgavens_egen_project_id_for_skrivekall(self):
        res, _ = self._kjor({"p1": {"tasks": []}, "inbox": {"tasks": [_inbox_oppgave("i")]}})
        self.assertIn("ID: inbox\n", res)
        self.assertIn("Bruk oppgavens egen Project ID i skrivekall", res)

    def test_vanlige_prosjekter_far_ikke_skrivekallnotatet(self):
        res, _ = self._kjor({"p1": {"tasks": [_oppgave("a")]}, "inbox": {"tasks": []}})
        self.assertIn("Liste A", res)
        self.assertNotIn("skrivekall", res)

    def test_innboks_i_prosjektlisten_legges_ikke_til_en_gang_til(self):
        prosjekter = [_prosjekt("p1", "Liste A"), _prosjekt("inbox123", "Inbox")]
        res, klient = self._kjor(
            {"p1": {"tasks": []}, "inbox123": {"tasks": [_inbox_oppgave("en-gang")]}},
            prosjekter=prosjekter)
        self.assertEqual([c.args[0] for c in klient.get_project_with_data.call_args_list], ["p1", "inbox123"])
        self.assertEqual(res.count("Title: en-gang"), 1)
        self.assertNotIn("Innboks", res)
        self.assertIn("1 of 2 projects", res)

    def test_innboks_legges_til_naar_ingen_prosjekt_har_inbox_id(self):
        # "inbox" er ikke et prefiks av andre ID-er ("p1"), og prosjekter med liknende navn utløser ikke vakten.
        prosjekter = [_prosjekt("p1", "Inbox-liste")]
        res, klient = self._kjor({"p1": {"tasks": []}, "inbox": {"tasks": [_inbox_oppgave("x")]}},
                                 prosjekter=prosjekter)
        self.assertEqual([c.args[0] for c in klient.get_project_with_data.call_args_list], ["p1", "inbox"])

    def test_tom_innboks_gir_ingen_tom_blokk(self):
        res, _ = self._kjor({"p1": {"tasks": [_oppgave("a")]}, "inbox": {"tasks": []}})
        self.assertNotIn("Innboks", res)
        self.assertIn("1 of 2 projects", res)
        self.assertNotIn("0 tasks", res)


if __name__ == "__main__":
    unittest.main()
