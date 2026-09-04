from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("papers", "0002_paper_pub_sort_date")]

    operations = [
        migrations.RemoveConstraint(
            model_name="paper",
            name="paper_doi_unique",
        ),
    ]
