# -*- coding: utf-8 -*-
# Generated by Django 1.9.1 on 2016-01-03 19:27
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('draw', '0001_initial'),
        ('participants', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='teamvenuepreference',
            name='team',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='participants.Team'),
        ),
    ]
