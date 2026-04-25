# Generated manually to sync llm_manager models with runtime code.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('llm_manager', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='llmconfig',
            name='context_window',
            field=models.PositiveIntegerField(default=4096, help_text='上下文窗口大小 (Tokens)'),
        ),
        migrations.AddField(
            model_name='llmconfig',
            name='max_output_tokens',
            field=models.PositiveIntegerField(default=2048, help_text='单次最大输出 Tokens'),
        ),
        migrations.AddField(
            model_name='llmconfig',
            name='input_price_1m',
            field=models.DecimalField(decimal_places=4, default=0.0, help_text='输入价格 (每百万 Tokens)', max_digits=10),
        ),
        migrations.AddField(
            model_name='llmconfig',
            name='output_price_1m',
            field=models.DecimalField(decimal_places=4, default=0.0, help_text='输出价格 (每百万 Tokens)', max_digits=10),
        ),
        migrations.AddField(
            model_name='llmconfig',
            name='is_online',
            field=models.BooleanField(default=True, help_text='当前运行状态 (正常/离线)'),
        ),
        migrations.AlterField(
            model_name='llmconfig',
            name='api_key_encrypted',
            field=models.CharField(blank=True, help_text='加密存储的 API Key', max_length=512, null=True),
        ),
        migrations.AlterField(
            model_name='llmconfig',
            name='description',
            field=models.TextField(blank=True, help_text='推荐场景描述', null=True),
        ),
        migrations.AlterField(
            model_name='llmconfig',
            name='params',
            field=models.JSONField(default=dict, help_text='其他调用参数: temperature, stop_sequences 等'),
        ),
        migrations.AlterField(
            model_name='modelpermission',
            name='llm_config',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='user_permissions', to='llm_manager.llmconfig'),
        ),
        migrations.AlterField(
            model_name='modelpermission',
            name='user',
            field=models.ForeignKey(blank=True, help_text='关联特定用户', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='llm_permissions', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='modelpermission',
            name='role',
            field=models.CharField(blank=True, help_text='关联用户角色，为空则只匹配指定用户', max_length=16, null=True),
        ),
        migrations.AddField(
            model_name='modelpermission',
            name='is_active',
            field=models.BooleanField(default=True, help_text='该用户是否可使用此模型'),
        ),
        migrations.AddField(
            model_name='modelpermission',
            name='daily_quota',
            field=models.PositiveIntegerField(default=100, help_text='该用户每日调用上限 (次数)'),
        ),
        migrations.AddField(
            model_name='modelpermission',
            name='priority_weight',
            field=models.SmallIntegerField(default=1, help_text='调度优先级 (权重越高越优先)'),
        ),
        migrations.AddField(
            model_name='modelpermission',
            name='custom_params_override',
            field=models.JSONField(blank=True, default=dict, help_text='针对该用户的专属参数覆盖 (如不同的 temp)'),
        ),
        migrations.RemoveField(
            model_name='modelpermission',
            name='can_use',
        ),
        migrations.AlterUniqueTogether(
            name='modelpermission',
            unique_together={('llm_config', 'user'), ('llm_config', 'role')},
        ),
        migrations.AddField(
            model_name='modelobjectmapping',
            name='usage_type',
            field=models.CharField(choices=[('GENERAL', '通用/对话'), ('REASONING', '逻辑推理/拆解'), ('SUMMARIZE', '内容摘要/提炼')], default='GENERAL', help_text='建议用途', max_length=16),
        ),
        migrations.AddField(
            model_name='modelobjectmapping',
            name='priority',
            field=models.IntegerField(default=0, help_text='推荐优先级 (数值越大越优先)'),
        ),
        migrations.AlterUniqueTogether(
            name='modelobjectmapping',
            unique_together={('llm_config', 'object_type', 'usage_type')},
        ),
        migrations.CreateModel(
            name='ModelUsage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('request_id', models.CharField(help_text='关联业务请求 ID (X-Request-Id)', max_length=64, unique=True, db_index=True)),
                ('usage_type', models.CharField(blank=True, help_text='用途: REASONING, SUMMARIZE 等', max_length=32, null=True)),
                ('prompt_tokens', models.PositiveIntegerField(default=0)),
                ('completion_tokens', models.PositiveIntegerField(default=0)),
                ('total_tokens', models.PositiveIntegerField(default=0)),
                ('cost', models.DecimalField(decimal_places=6, default=0.0, help_text='本次调用产生的估算费用 (元)', max_digits=12)),
                ('latency_ms', models.PositiveIntegerField(default=0, help_text='响应耗时 (毫秒)')),
                ('status_code', models.IntegerField(default=200, help_text='HTTP 状态码')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('llm_config', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='usage_logs', to='llm_manager.llmconfig')),
                ('user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='llm_usages', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': '模型使用记录',
                'verbose_name_plural': '模型使用记录',
                'db_table': 'model_usage',
                'ordering': ['-created_at'],
            },
        ),
    ]
