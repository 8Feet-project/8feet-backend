import jwt
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import Permission
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from unittest.mock import Mock, patch

from reports.interface.report_interface import export_report_file, run_export_job
from reports.models.citation import Citation, ReportFollowup
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
        followup_permission = Permission.objects.get(
            content_type__app_label="reports",
            codename="followup_report",
        )
        self.user.user_permissions.add(followup_permission)

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
                        "authority_score": 4,
                        "authority_reason": "测试用高可信来源",
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
        self.assertEqual(payload["citations"][0]["authority_score"], 4)
        self.assertEqual(payload["citations"][0]["authority_label"], "专业高可信来源")
        self.assertEqual(payload["citations"][0]["authority_reason"], "测试用高可信来源")
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

    def test_report_detail_backfills_missing_citation_rows_from_state_keys(self):
        self.report.content_markdown = "# 报告\n\n结论来自模型线程保留的引用[@THREAD_SOURCE]。"
        self.report.save(update_fields=["content_markdown"])
        self.citation.delete()
        ResearchConversation.objects.create(
            task=self.task,
            thread_id="thread-state-only-citation",
            state_snapshot={
                "citations": [
                    {
                        "cite_key": "THREAD_SOURCE",
                        "url": "https://example.com/thread-source",
                        "title": "线程来源",
                        "source_platform": "example.com",
                        "provider": "web_fetch",
                        "tool_name": "web_fetch",
                        "authority_score": 4,
                        "summary": "线程来源摘要",
                        "accessed_at": "2026-05-10T12:00:00",
                    }
                ]
            },
        )

        response = self.client.get(f"/api/v1/reports/{self.report.id}", secure=True)

        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        self.assertEqual(len(payload["citations"]), 1)
        citation = payload["citations"][0]
        self.assertEqual(citation["citation_id"], "state-thread_source")
        self.assertEqual(citation["index_number"], 1)
        self.assertEqual(citation["cite_key"], "thread_source")
        self.assertEqual(citation["source_title"], "线程来源")
        self.assertEqual(citation["source_url"], "https://example.com/thread-source")
        self.assertEqual(citation["authority_label"], "专业高可信来源")

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
                        "authority_score": 4,
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
        self.assertEqual(url_less["authority_label"], "专业高可信来源")
        self.assertIn("ak.car_market_total_cpca", url_less["reproduction_code"])
        self.assertIn("@misc{akshare_auto_sales", url_less["bibtex"])

        list_payload = list_response.json()["data"]
        listed = next(item for item in list_payload["list"] if item["citation_id"] == str(citation.id))
        self.assertEqual(listed["authority_label"], "专业高可信来源")
        self.assertIn("ak.car_market_total_cpca", listed["reproduction_code"])

        item_payload = item_response.json()["data"]
        self.assertEqual(item_payload["source_type"], "structured_dataset")
        self.assertEqual(item_payload["authority_label"], "专业高可信来源")
        self.assertIn("ak.car_market_total_cpca", item_payload["reproduction_code"])

    def test_structured_tool_citation_backfills_reproduction_code_from_tool_params(self):
        self.report.content_markdown = "# 报告\n\n网页来源提供背景信息[@EXAMPLE_SOURCE]，监管检索未发现处罚记录[@CSRC_MARKET_BANS]。"
        self.report.save(update_fields=["content_markdown"])
        citation = Citation.objects.create(
            report=self.report,
            index_number=2,
            source_url="",
            source_title="csrc market_bans data",
            cited_text_snippet="无市场禁入记录",
        )
        ResearchConversation.objects.create(
            task=self.task,
            thread_id="thread-csrc-citation",
            state_snapshot={
                "citations": [
                    {
                        "cite_key": "CSRC_MARKET_BANS",
                        "title": citation.source_title,
                        "source_platform": "csrc",
                        "source_category": "web",
                        "provider": "csrc",
                        "tool_name": "csrc_enforcement_data",
                        "endpoint": "market_bans",
                        "tool_params": {
                            "endpoint": "market_bans",
                            "start_date": "2018-01-01",
                            "end_date": "2026-12-31",
                            "keyword": "宁德时代",
                            "page": 1,
                            "limit": 5,
                            "include_content": True,
                        },
                    }
                ]
            },
        )

        response = self.client.get(f"/api/v1/reports/{self.report.id}/citations/{citation.id}", secure=True)

        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        self.assertEqual(payload["source_type"], "structured_financial_data")
        self.assertIn("requests.get", payload["reproduction_code"])
        self.assertIn("www.csrc.gov.cn/searchList", payload["reproduction_code"])
        self.assertIn("keyword = '宁德时代'", payload["reproduction_code"])
        self.assertNotIn("efeet.tools", payload["reproduction_code"])

    def test_append_followup_restarts_task_conversation(self):
        ResearchConversation.objects.create(
            task=self.task,
            thread_id="thread-report-followup",
            system_message="system",
        )
        followup = ReportFollowup.objects.create(
            report=self.report,
            user=self.user,
            question="原始追问",
            answer="旧答案",
        )
        runtime = Mock()
        runtime.build_followup_prompt.side_effect = lambda message: f"followup:{message}"
        runtime.enqueue_task_run.return_value = (True, None)

        with patch("research.interface.research_interface._safe_research_runtime", return_value=(runtime, None)):
            response = self.client.post(
                f"/api/v1/reports/{self.report.id}/qa/{followup.id}/append",
                data='{"append_text":"请补充风险因素"}',
                content_type="application/json",
                secure=True,
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        self.assertEqual(payload["qa"]["status"], "pending")
        self.assertIn("请补充风险因素", payload["qa"]["question"])
        runtime.build_followup_prompt.assert_called_once_with("请补充风险因素")
        runtime.enqueue_task_run.assert_called_once_with(
            self.task.id,
            prompt="followup:请补充风险因素",
            create_report=False,
            queued_step_name="开始处理追问",
            run_metadata={"report_followup_id": followup.id},
        )

    def test_report_export_download_serves_local_file(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(REPORT_EXPORT_ROOT=tmpdir):
            success, message, data = export_report_file(self.report.id, "md", "full")
            self.assertTrue(success, message)

            result = run_export_job(int(data["export_id"]))

            self.assertEqual(result["status"], "completed")
            self.assertEqual(
                result["download_url"],
                f"/api/v1/reports/exports/{data['export_id']}/download",
            )
            status_response = self.client.get(
                f"/api/v1/reports/exports/{data['export_id']}/status",
                secure=True,
            )
            self.assertEqual(status_response.status_code, 200)
            status_payload = status_response.json()["data"]
            self.assertEqual(status_payload["download_url"], result["download_url"])
            self.assertFalse(status_payload["storage_path"].startswith("http"))
            self.assertTrue((Path(tmpdir) / status_payload["storage_path"]).exists())

            download_response = self.client.get(result["download_url"], secure=True)
            self.assertEqual(download_response.status_code, 200)
            self.assertIn("attachment", download_response.headers["Content-Disposition"])
            body = b"".join(download_response.streaming_content).decode("utf-8")
            self.assertIn("# 测试调研报告", body)

    def test_docx_export_uses_pandoc(self):
        def fake_run(command, **kwargs):
            output_path = Path(command[command.index("-o") + 1])
            output_path.write_bytes(b"docx-content")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as tmpdir, override_settings(REPORT_EXPORT_ROOT=tmpdir):
            with patch("reports.interface.export_utils.subprocess.run", side_effect=fake_run) as run_mock:
                success, message, data = export_report_file(self.report.id, "docx", "full")
                self.assertTrue(success, message)

                result = run_export_job(int(data["export_id"]))

            self.assertEqual(result["status"], "completed")
            command = run_mock.call_args.args[0]
            self.assertEqual(command[0], "pandoc")
            self.assertIn("-f", command)
            self.assertIn("gfm", command)
            self.assertNotIn("--pdf-engine=weasyprint", command)

    def test_pdf_export_uses_pandoc_with_weasyprint(self):
        def fake_run(command, **kwargs):
            output_path = Path(command[command.index("-o") + 1])
            output_path.write_bytes(b"pdf-content")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as tmpdir, override_settings(REPORT_EXPORT_ROOT=tmpdir):
            with patch("reports.interface.export_utils.subprocess.run", side_effect=fake_run) as run_mock:
                success, message, data = export_report_file(self.report.id, "pdf", "full")
                self.assertTrue(success, message)

                result = run_export_job(int(data["export_id"]))

            self.assertEqual(result["status"], "completed")
            command = run_mock.call_args.args[0]
            self.assertEqual(command[0], "pandoc")
            self.assertIn("--pdf-engine=weasyprint", command)

    def test_export_api_runs_job_synchronously(self):
        def fake_run(command, **kwargs):
            output_path = Path(command[command.index("-o") + 1])
            output_path.write_bytes(b"docx-content")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as tmpdir, override_settings(REPORT_EXPORT_ROOT=tmpdir):
            with patch("reports.interface.export_utils.subprocess.run", side_effect=fake_run):
                response = self.client.post(
                    f"/api/v1/reports/{self.report.id}/export",
                    data='{"format":"docx","report_mode":"full"}',
                    content_type="application/json",
                    secure=True,
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()["data"]
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["format"], "docx")
            self.assertTrue(payload["download_url"])
            self.assertTrue(payload["storage_path"])
