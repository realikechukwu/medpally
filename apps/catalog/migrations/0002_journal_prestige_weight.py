# Generated manually for an additive production-safe schema change.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("catalog", "0001_initial")]
    operations = [migrations.AddField(
        model_name="journal", name="prestige_weight", field=models.SmallIntegerField(default=2),
    )]
