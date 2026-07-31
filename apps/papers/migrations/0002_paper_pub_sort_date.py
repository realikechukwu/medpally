from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("papers", "0001_initial")]
    operations = [
        migrations.AddField(model_name="paper", name="pub_sort_date", field=models.DateField(null=True)),
        migrations.RunSQL(
            "UPDATE papers_paper SET pub_sort_date = COALESCE(pub_date, entrez_date) WHERE pub_sort_date IS NULL",
            migrations.RunSQL.noop,
        ),
        migrations.AlterField(model_name="paper", name="pub_sort_date", field=models.DateField()),
        migrations.AddIndex(model_name="paper", index=models.Index(
            condition=models.Q(("is_visible", True), ("summary_status", "ok")), fields=["-pub_sort_date", "-id"], name="paper_pub_sort_idx"
        )),
    ]
