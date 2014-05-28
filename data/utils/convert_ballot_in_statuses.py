"""Convert a database that used the old STATUS_BALLOT_IN ("B") convention
for Debate.result_status, to the new convention that uses the new field
Debate.ballot_in = True with Debate.result_status = STATUS_NONE.
Only run this script after migrating.
"""

import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
import sys
sys.path.append(os.path.abspath(os.path.join(os.environ.get("VIRTUAL_ENV"), "..")))
import debate.models as m

import argparse
parser = argparse.ArgumentParser(description=__doc__)
parser.parse_args()

for debate in m.Debate.objects.all():
    if debate.result_status == "B":
        print debate
        debate.result_status = debate.STATUS_NONE
        debate.ballot_in = True
        debate.save()