# -*- coding: utf-8 -*-
# Generated by Django 1.10.1 on 2016-09-10 11:40
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('breakqual', '0010_auto_20160823_1051'),
    ]

    operations = [
        migrations.AlterField(
            model_name='breakcategory',
            name='rule',
            field=models.CharField(choices=[('standard', 'Standard'), ('aida-1996', 'AIDA 1996'), ('aida-2016-easters', 'AIDA 2016 (Easters)'), ('aida-2016-australs', 'AIDA 2016 (Australs)'), ('wadl-div-first', 'WADL division winners first'), ('wadl-div-guaranteed', 'WADL division winners guaranteed')], default='standard', help_text='Rule for how the break is calculated (most tournaments should use "Standard")', max_length=25),
        ),
    ]