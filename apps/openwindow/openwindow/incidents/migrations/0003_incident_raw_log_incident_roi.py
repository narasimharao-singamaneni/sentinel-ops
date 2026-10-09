# Generated for the end-to-end-rca-pipeline change.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('incidents', '0002_alter_incident_citations_alter_incident_confidence_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='incident',
            name='raw_log',
            field=models.TextField(blank=True, help_text="Raw log/evidence text reported with the incident (nullable)", null=True),
        ),
        migrations.AddField(
            model_name='incident',
            name='roi',
            field=models.JSONField(blank=True, help_text="Per-incident ROI object: runbook_class, manual_estimate_minutes, tool_time_minutes, time_saved_minutes, breakdown (nullable)", null=True),
        ),
    ]
