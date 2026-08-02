from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("feed", "0002_featured_paper")]
    operations = [
        migrations.AddField(
            model_name="userpaperstate",
            name="searched_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="userpaperstate",
            index=models.Index(
                condition=models.Q(searched_at__isnull=False),
                fields=["user", "-searched_at"],
                name="user_paper_search_idx",
            ),
        ),
    ]
