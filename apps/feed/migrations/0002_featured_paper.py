import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("catalog", "0002_journal_prestige_weight"), ("feed", "0001_initial")]
    operations = [
        migrations.CreateModel(
            name="FeaturedPaper",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("week_start", models.DateField()), ("rank", models.SmallIntegerField()),
                ("score", models.FloatField()), ("selected_at", models.DateTimeField(auto_now_add=True)),
                ("paper", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="featured_links", to="papers.paper")),
                ("specialty", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="featured_papers", to="catalog.specialty")),
            ],
            options={
                "indexes": [models.Index(fields=["specialty", "-week_start", "rank"], name="featured_specialty_week_idx")],
                "constraints": [
                    models.UniqueConstraint(fields=("specialty", "week_start", "paper"), name="featured_paper_unique"),
                    models.UniqueConstraint(fields=("specialty", "week_start", "rank"), name="featured_rank_unique"),
                ],
            },
        ),
    ]
