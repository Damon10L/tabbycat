# -*- coding: utf-8 -*-
# Generated by Django 1.10.1 on 2016-09-10 10:57
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tournaments', '0015_current_round_set_null_on_delete'),
    ]

    operations = [
        migrations.AlterField(
            model_name='round',
            name='draw_type',
            field=models.CharField(choices=[('R', 'Random'), ('M', 'Manual'), ('D', 'Round-robin'), ('P', 'Power-paired'), ('F', 'First elimination'), ('B', 'Subsequent elimination')], help_text='Which draw method to use', max_length=1),
        ),
        migrations.AlterField(
            model_name='round',
            name='seq',
            field=models.IntegerField(help_text='A number that determines the order of the round, should count consecutively from 1 for the first round'),
        ),
    ]
