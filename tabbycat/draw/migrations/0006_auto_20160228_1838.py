# -*- coding: utf-8 -*-
# Generated by Django 1.9.2 on 2016-02-28 18:38
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('draw', '0005_auto_20160109_1904'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='debate',
            options={},
        ),
        migrations.AlterModelOptions(
            name='debateteam',
            options={},
        ),
        migrations.AlterModelOptions(
            name='institutionvenuepreference',
            options={'ordering': ['priority']},
        ),
        migrations.AlterModelOptions(
            name='teamvenuepreference',
            options={'ordering': ['priority']},
        ),
    ]
