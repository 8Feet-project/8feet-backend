import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0003_user_persona"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                help_text="Account manager who created or owns this user",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="managed_user_profiles",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
