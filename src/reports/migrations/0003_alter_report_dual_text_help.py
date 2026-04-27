from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0002_reportexportrecord"),
    ]

    operations = [
        migrations.AlterField(
            model_name="report",
            name="content_markdown",
            field=models.TextField(help_text="详细报告文本 (Markdown 内容)"),
        ),
        migrations.AlterField(
            model_name="report",
            name="content_brief",
            field=models.TextField(blank=True, help_text="简版报告文本 (Markdown 内容)", null=True),
        ),
    ]
