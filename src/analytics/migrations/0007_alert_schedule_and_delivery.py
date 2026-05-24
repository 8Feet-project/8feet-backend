from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('research', '0004_researchtask_parent_role'),
        ('analytics', '0006_remove_favorite_folder'),
    ]

    operations = [
        migrations.AddField(
            model_name='alert',
            name='notify_in_app',
            field=models.BooleanField(default=True, help_text='是否站内消息推送'),
        ),
        migrations.AddField(
            model_name='alert',
            name='next_run_at',
            field=models.DateTimeField(blank=True, db_index=True, help_text='下次计划触发时间', null=True),
        ),
        migrations.AddField(
            model_name='alert',
            name='last_task',
            field=models.ForeignKey(blank=True, help_text='最近一次由该提醒触发的调研任务', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='triggered_alerts', to='research.researchtask'),
        ),
        migrations.AddField(
            model_name='usermessage',
            name='action_url',
            field=models.CharField(blank=True, default='', help_text='消息点击跳转地址', max_length=512),
        ),
    ]
