from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.directory.journal_search import build_journal_search_index
from apps.directory.models import Journal


class JournalSearchViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="journal_search_user", password="1234")
        self.journal = Journal.objects.create(
            name="2D MATERIALS",
            issn="2053-1583",
            search_index=build_journal_search_index(["2D MATERIALS"], ["2053-1583"]),
            white_list_level=1,
        )

    def test_search_finds_journal_by_compact_issn(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("directory:journal_search"), {"q": "20531583"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["id"], self.journal.id)
