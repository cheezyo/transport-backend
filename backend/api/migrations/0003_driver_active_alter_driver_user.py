# Fixed migration: add Driver.active, alter Driver.user, map status->active
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def map_status_to_active(apps, schema_editor):
    Driver = apps.get_model('api', 'Driver')
    for d in Driver.objects.all():
        # status kan være None; vi tolker alt annet enn 'active' som False
        status = (d.status or '').lower()
        d.active = (status == 'active')
        d.save(update_fields=['active'])


class Migration(migrations.Migration):

    dependencies = [
        ('api',
         '0002_priceplan_stop1_surcharge_priceplan_stop2_surcharge_and_more'
         ),  # ← din forrige
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='driver',
            name='active',
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name='driver',
            name='user',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(map_status_to_active, migrations.RunPython.noop),
    ]
