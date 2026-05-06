import jwt

from django.conf import settings
from django.contrib.auth.models import Permission
from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from reports.models.citation import Citation
from reports.models.report import Report
from research.models.research_task import ResearchTask
from research.models.scraped_content import ScrapedContent


class ReportCitationDetailApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="report-reader",
            password="test-pass-123",
        )
        permission = Permission.objects.get(
            content_type__app_label="reports",
            codename="view_report",
        )
        self.user.user_permissions.add(permission)

        self.task = ResearchTask.objects.create(
            user=self.user,
            title="测试调研任务",
            object_name="腾讯控股",
            object_type="COMPANY",
            status="COMPLETED",
        )
        self.report = Report.objects.create(
            task=self.task,
            title="测试调研报告",
            summary="摘要",
            content_markdown="# 报告",
            content_brief="# 摘要",
        )
        self.citation = Citation.objects.create(
            report=self.report,
            index_number=1,
            source_url="https://example.com/source",
            source_title="示例来源",
            cited_text_snippet="这是引用摘要",
        )
        self.scraped_content = ScrapedContent.objects.create(
            task=self.task,
            source_url=self.citation.source_url,
            source_title=self.citation.source_title,
            source_type="NEWS",
            content_text="抓取到的内容",
            relevance_score=0.96,
        )
        token = jwt.encode(
            {"user_id": self.user.id, "type": "access_token"},
            settings.SECRET_KEY,
            algorithm="HS256",
        )
        self.client = Client(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_report_citation_detail_includes_source_metadata_from_scraped_content(self):
        response = self.client.get(
            f"/api/v1/reports/{self.report.id}/citations/{self.citation.id}",
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["citation_id"], str(self.citation.id))
        self.assertEqual(payload["data"]["excerpt"], "这是引用摘要")
        self.assertEqual(payload["data"]["source_type"], "NEWS")
        self.assertEqual(
            payload["data"]["published_at"],
            self.scraped_content.scraped_at.isoformat(),
        )
