import jwt

from django.conf import settings
from django.contrib.auth.models import Permission
from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from reports.models.citation import Citation
from reports.models.report import Report
from research.models.conversation import ResearchConversation
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

    def test_report_detail_cleans_tool_markup_and_exposes_cite_metadata(self):
        self.report.content_markdown = (
            'Now let me write the report.\n\n'
            '<｜DSML｜tool_calls>\n'
            '<｜DSML｜invoke name="write_file">\n'
            '<｜DSML｜parameter name="path" string="true">/mnt/user-data/outputs/research_report.md</｜DSML｜parameter>\n'
            '<｜DSML｜parameter name="content" string="true"># 报告\n\n'
            '结论来自来源[@Example_Source]。'
        )
        self.report.save(update_fields=["content_markdown"])
        ResearchConversation.objects.create(
            task=self.task,
            thread_id="thread-report-citation",
            state_snapshot={
                "citations": [
                    {
                        "cite_key": "EXAMPLE_SOURCE",
                        "url": self.citation.source_url,
                        "title": self.citation.source_title,
                        "source_platform": "example.com",
                        "provider": "web_fetch",
                        "tool_name": "web_fetch",
                        "howpublished": "[EB/OL]",
                        "accessed_at": "2026-05-10T12:00:00",
                        "entry_type": "misc",
                    }
                ]
            },
        )

        response = self.client.get(f"/api/v1/reports/{self.report.id}", secure=True)

        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        self.assertTrue(payload["content_markdown"].startswith("# 报告"))
        self.assertNotIn("DSML", payload["content_markdown"])
        self.assertNotIn("Now let me write", payload["content_markdown"])
        self.assertEqual(payload["citations"][0]["index_number"], 1)
        self.assertEqual(payload["citations"][0]["cite_key"], "example_source")
        self.assertEqual(payload["citations"][0]["reproduction_code"], "")
        self.assertIn("@misc{example_source", payload["citations"][0]["bibtex"])
        self.assertIn("@misc{example_source", payload["references_bibtex"])
        self.assertIn("+08:00", payload["created_at"])

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
        self.assertEqual(payload["data"]["reproduction_code"], "")
        self.assertEqual(payload["data"]["source_type"], "NEWS")
        self.assertEqual(
            payload["data"]["published_at"],
            self.scraped_content.scraped_at.astimezone().isoformat(),
        )

    def test_url_less_citation_exposes_state_metadata_and_reproduction_code(self):
        self.report.content_markdown = (
            "# 报告\n\n"
            "网页来源提供背景信息[@EXAMPLE_SOURCE]，结构化数据来自 AkShare[@AKSHARE_AUTO_SALES]。"
        )
        self.report.save(update_fields=["content_markdown"])
        citation = Citation.objects.create(
            report=self.report,
            index_number=2,
            source_url="",
            source_title="AkShare 中国汽车销量数据",
            cited_text_snippet="销量数据摘要",
        )
        ResearchConversation.objects.create(
            task=self.task,
            thread_id="thread-url-less-citation",
            state_snapshot={
                "citations": [
                    {
                        "cite_key": "AKSHARE_AUTO_SALES",
                        "title": citation.source_title,
                        "source_platform": "akshare",
                        "source_category": "structured_dataset",
                        "provider": "akshare",
                        "tool_name": "akshare_tool",
                        "howpublished": "通过 AkShare 接口获取",
                        "reproduction_code": "import akshare as ak\nak.car_market_total_cpca()",
                    }
                ]
            },
        )

        detail_response = self.client.get(f"/api/v1/reports/{self.report.id}", secure=True)
        list_response = self.client.get(f"/api/v1/reports/{self.report.id}/citations", secure=True)
        item_response = self.client.get(
            f"/api/v1/reports/{self.report.id}/citations/{citation.id}",
            secure=True,
        )

        self.assertEqual(detail_response.status_code, 200)
        detail_payload = detail_response.json()["data"]
        url_less = next(item for item in detail_payload["citations"] if item["citation_id"] == str(citation.id))
        self.assertEqual(url_less["source_url"], "")
        self.assertEqual(url_less["cite_key"], "akshare_auto_sales")
        self.assertEqual(url_less["source_platform"], "akshare")
        self.assertEqual(url_less["source_type"], "structured_dataset")
        self.assertIn("ak.car_market_total_cpca", url_less["reproduction_code"])
        self.assertIn("@misc{akshare_auto_sales", url_less["bibtex"])

        list_payload = list_response.json()["data"]
        listed = next(item for item in list_payload["list"] if item["citation_id"] == str(citation.id))
        self.assertIn("ak.car_market_total_cpca", listed["reproduction_code"])

        item_payload = item_response.json()["data"]
        self.assertEqual(item_payload["source_type"], "structured_dataset")
        self.assertIn("ak.car_market_total_cpca", item_payload["reproduction_code"])
