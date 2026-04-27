from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('llm_manager', '0002_sync_llm_manager_runtime_models'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='llmconfig',
            name='model_id',
        ),
    ]
