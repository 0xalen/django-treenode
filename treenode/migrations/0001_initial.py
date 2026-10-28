from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="TreeNodeCacheRevision",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("model_label", models.CharField(max_length=255, unique=True)),
                ("revision", models.PositiveBigIntegerField(default=0)),
            ],
            options={
                "verbose_name": "Tree node cache revision",
                "verbose_name_plural": "Tree node cache revisions",
            },
        )
    ]
