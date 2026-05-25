from django.db import migrations, models


def populate_model_usage_snapshots(apps, schema_editor):
    ModelUsage = apps.get_model("llm_manager", "ModelUsage")
    for usage in ModelUsage.objects.select_related("llm_config").all():
        config = usage.llm_config
        if not config:
            continue
        updates = []
        if not usage.model_name_snapshot:
            usage.model_name_snapshot = config.name or ""
            updates.append("model_name_snapshot")
        if not usage.provider_snapshot:
            usage.provider_snapshot = config.provider or ""
            updates.append("provider_snapshot")
        if updates:
            usage.save(update_fields=updates)


class Migration(migrations.Migration):

    dependencies = [
        ("llm_manager", "0003_remove_llmconfig_model_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="modelusage",
            name="model_name_snapshot",
            field=models.CharField(blank=True, default="", help_text="调用发生时的模型名称快照", max_length=128),
        ),
        migrations.AddField(
            model_name="modelusage",
            name="provider_snapshot",
            field=models.CharField(blank=True, default="", help_text="调用发生时的供应商快照", max_length=64),
        ),
        migrations.RunPython(populate_model_usage_snapshots, migrations.RunPython.noop),
    ]
