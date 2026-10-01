from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.directory.journal_trends import trending_journals
from apps.directory.models import ArticleType, Journal
from apps.submissions.models import Submission


class JournalTrendsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="journal-trends-author")
        self.article_type = ArticleType.objects.create(code="journal-trends", name="Статья")
        self.now = timezone.now()

    def _choice(self, journal, *, days_ago):
        submission = Submission.objects.create(
            title="Материал",
            author=self.user,
            article_type=self.article_type,
            journal=journal,
        )
        Submission.objects.filter(pk=submission.pk).update(
            created_at=self.now - timedelta(days=days_ago)
        )

    def test_two_choices_this_week_beat_ten_from_two_months_ago(self):
        old = Journal.objects.create(name="Старый журнал", issn="1111-1111")
        rising = Journal.objects.create(name="Новый журнал", issn="2222-2222")
        for _ in range(10):
            self._choice(old, days_ago=60)
        for _ in range(2):
            self._choice(rising, days_ago=3)

        ranked = trending_journals(now=self.now)

        self.assertEqual(ranked[0]["journal"], rising)
        self.assertEqual(ranked[0]["week_count"], 2)
        self.assertEqual(ranked[1]["journal"], old)
        self.assertEqual(ranked[1]["week_count"], 0)

    def test_ten_active_journals_are_returned_and_inactive_are_excluded(self):
        journals = [Journal.objects.create(name=f"Журнал {index:02d}") for index in range(12)]
        inactive = journals[-1]
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])
        self._choice(journals[10], days_ago=1)
        self._choice(inactive, days_ago=1)

        ranked = trending_journals(now=self.now)

        self.assertEqual(len(ranked), 10)
        self.assertEqual(ranked[0]["journal"], journals[10])
        self.assertNotIn(inactive, [item["journal"] for item in ranked])
        self.assertEqual(ranked[1]["week_count"], 0)

    def test_endpoint_requires_login_and_exposes_name_issn_and_activity(self):
        journal = Journal.objects.create(name="Журнал недели", issn="1234-5678")
        self._choice(journal, days_ago=1)
        url = reverse("directory:journal_trends")

        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.user)
        response = self.client.get(url, {"article_type": self.article_type.pk})

        self.assertEqual(response.status_code, 200)
        item = response.json()["results"][0]
        self.assertEqual(item["id"], journal.pk)
        self.assertEqual(item["name"], "Журнал недели")
        self.assertEqual(item["issn"], "1234-5678")
        self.assertEqual(item["week_count"], 1)

        create_response = self.client.get(reverse("submissions:create"))
        self.assertContains(create_response, "data-journal-trends")
        self.assertContains(create_response, url)
