from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0008_backfill_research_completed_operation_logs"),
    ]

    operations = [
        migrations.AddField(
            model_name="favorite",
            name="remark",
            field=models.CharField(blank=True, default="", help_text="收藏备注", max_length=255),
        ),
    ]
