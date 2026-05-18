from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0005_alter_alert_options'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='favorite',
            name='folder',
        ),
    ]
