from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import MultipleObjectsReturned, ObjectDoesNotExist
from django.utils import formats
from django.utils.encoding import force_text
from django.utils.translation import ugettext as _

from adjallocation.allocation import AdjudicatorAllocation
from adjallocation.utils import adjudicator_conflicts_display
from draw.models import Debate, DebateTeam
from participants.models import Team
from participants.utils import get_side_counts
from standings.templatetags.standingsformat import metricformat, rankingformat
from tournaments.utils import aff_abbr, aff_name, aff_team, get_position_name, neg_name
from utils.misc import reverse_tournament
from venues.utils import venue_conflicts_display

from .mixins import SuperuserRequiredMixin


class BaseTableBuilder:
    """Class for building tables that can be easily inserted into Vue tables,
    Designed to be used with VueTableTemplateView.

    In the docstrings for this class:
    - A *header dict* is a dict that contains a value under `"key"` that is a
      string, and may optionally contain entries under `"tooltip"`, `"icon"`,
      `"visible-sm"`, `"visible-md"` and `"visible-lg"`.
    - A *cell dict* is a dict that contains a value under `"text"` that is a
      string, and may optionally contain entries under `"sort"`, `"icon"`,
      `"emoji"`, `"popover"` and `"link"`.

    """

    def __init__(self, **kwargs):
        self.headers = []
        self.data = []
        self.title = kwargs.get('title', "")
        self.table_class = kwargs.get('table_class', "")
        self.sort_key = kwargs.get('sort_key', '')
        self.sort_order = kwargs.get('sort_order', '')
        self.popovers = kwargs.get('popovers', True)

    @staticmethod
    def _convert_header(header):
        if isinstance(header, dict):
            header['key'] = force_text(header['key'])
            return header
        else:
            return {'key': force_text(header)}

    @staticmethod
    def _convert_cell(cell):
        if isinstance(cell, dict):
            if 'text' in cell:
                cell['text'] = force_text(cell['text'])
            return cell
        else:
            cell_dict = {}
            if isinstance(cell, int) or isinstance(cell, float):
                cell_dict['sort'] = cell
            cell_dict['text'] = force_text(cell)
            return cell_dict

    def add_column(self, header, data):
        """Adds a column to the table.

        - `header` must be either a string or a header dict (see class docstring).
        - `data` must be a list of cells (in the column). Each cell should
          either be a string or a cell dict (see class docstring). If this is
          not the first column, then there must be as many elements in `data` as
          there are in the existing columns.
        """
        if len(self.data) > 0 and len(data) != len(self.data):
            raise ValueError("data contains {new:d} rows, existing table has {existing:d}".format(
                new=len(data), existing=len(self.data)))

        header = self._convert_header(header)
        self.headers.append(header)

        data = map(self._convert_cell, data)
        if len(self.data) == 0:
            self.data = [[cell] for cell in data]
        else:
            for row, cell in zip(self.data, data):
                row.append(cell)

    def add_boolean_column(self, header, data):
        """Convenience function for adding a column based on boolean data.

        - `header` must be either a string or a header dict.
        - `data` must be an iterable of booleans.
        """
        cells = [{
            'icon': 'glyphicon-ok' if datum else '',
            'sort':  1 if datum else 2,
        } for datum in data]
        self.add_column(header, cells)

    def add_columns(self, headers, data):
        """Adds columns to the table.

        This method is intended for situations where it is easier to process
        data by row while adding it to the table, than it is to process data
        by column. In the latter case, use `add_column()` instead.

        - `headers` must be a list of strings or header dicts (or both).
        - `data` must be a list of lists of cells, where each cell is a string
          or cell dict. Each inner list corresponds to a row, and all inner
          lists must contain the same number of elements, which must also match
          the number of elements in `headers`. If there are no existing columns
          in the table, then there must be as many inner lists as there are
          existing columns.
        """
        if len(self.data) > 0 and len(data) != len(self.data):
            raise ValueError("data contains {new:d} rows, existing table has {existing:d}".format(
                new=len(data), existing=len(self.data)))

        headers = map(self._convert_header, headers)
        self.headers.extend(headers)

        if len(self.data) == 0:
            self.data = [[self._convert_cell(cell) for cell in row] for row in data]
        else:
            for row, cells in zip(self.data, data):
                cells = map(self._convert_cell, cells)
                row.extend(cells)

    def jsondict(self):
        """Returns the JSON dict for the table."""
        if self.popovers is False:
            self._strip_popovers()
        return {
            'head': self.headers,
            'data': self.data,
            'title': self.title,
            'class': self.table_class,
            'sort_key': self.sort_key,
            'sort_order': self.sort_order
        }

    def _strip_popovers(self):
        """Strips all popovers from the table. Used as an override in views
        where popovers make no sense, like those intended for the projector
        in general assembly."""
        for row in self.data:
            for cell in row:
                if 'popover' in cell:
                    del cell['popover']


class TabbycatTableBuilder(BaseTableBuilder):
    """Extends TableBuilder to add convenience functions specific to
    Tabbycat."""

    ADJ_SYMBOLS = {
        AdjudicatorAllocation.POSITION_CHAIR: "Ⓒ",
        AdjudicatorAllocation.POSITION_TRAINEE: "Ⓣ",
    }

    ADJ_POSITION_NAMES = {
        AdjudicatorAllocation.POSITION_CHAIR: "chair",
        AdjudicatorAllocation.POSITION_PANELLIST: "panellist",
        AdjudicatorAllocation.POSITION_TRAINEE: "trainee",
    }

    def __init__(self, view=None, **kwargs):
        """Constructor.
        - If `tournament` is specified, it becomes the default tournament for
          the builder.
        - If `admin` is True (default is False), then relevant links will go
          to the admin version rather than the public version.
        - If `view` is specified, then `tournament` and `admin` are inferred
          from `view`. This option is provided for convenience.
        """
        if 'tournament' not in kwargs and hasattr(view, 'get_tournament'):
            self.tournament = view.get_tournament()
        else:
            self.tournament = kwargs.get('tournament')

        if 'admin' not in kwargs and isinstance(view, SuperuserRequiredMixin) or \
                (isinstance(view, LoginRequiredMixin) and view.request.user.is_superuser):
            self.admin = True
        else:
            self.admin = kwargs.get('admin', False)

        return super().__init__(**kwargs)

    @property
    def _show_record_links(self):
        return self.admin or self.tournament.pref('public_record')

    @property
    def _show_speakers_in_draw(self):
        return self.tournament.pref('show_speakers_in_draw') or self.admin

    def _adjudicator_record_link(self, adj):
        adj_short_name = adj.name.split(" ")[0]
        if self.admin:
            return {
                'text': "View %s's Adjudication Record" % adj_short_name,
                'link': reverse_tournament('participants-adjudicator-record',
                    self.tournament, kwargs={'pk': adj.pk})
            }
        elif self.tournament.pref('public_record'):
            return {
                'text': "View %s's Adjudication Record" % adj_short_name,
                'link': reverse_tournament('participants-public-adjudicator-record',
                    self.tournament, kwargs={'pk': adj.pk})
            }
        else:
            return {'text': '', 'link': False}

    def _team_record_link(self, team):
        if self.admin:
            return {
                'text': "View %s's Team Record" % team.short_name,
                'link': reverse_tournament('participants-team-record', self.tournament, kwargs={'pk': team.pk})
            }
        elif self.tournament.pref('public_record'):
            return {
                'text': "View %s's Team Record" % team.short_name,
                'link': reverse_tournament('participants-public-team-record', self.tournament, kwargs={'pk': team.pk})
            }
        else:
            return {'text': '', 'link': False}

    def _team_cell(self, team, hide_emoji=True):
        cell = {
            'text': team.short_name,
            'emoji': team.emoji if self.tournament.pref('show_emoji') and not hide_emoji else None,
            'sort': team.short_name,
            'class': 'team-name',
            'popover': {'title': team.long_name, 'content': []}
        }
        if self._show_speakers_in_draw:
            cell['popover']['content'].append({'text': [" " + s.name for s in team.speakers]})
        if self._show_record_links:
            cell['popover']['content'].append(self._team_record_link(team))
        return cell

    def _result_cell(self, ts, compress=False, show_score=False, show_ballots=False):
        if not hasattr(ts, 'debate_team') or not hasattr(ts.debate_team.opponent, 'team'):
            return {'text': '-'}

        opp = ts.debate_team.opponent.team
        opp_vshort = '<i class="emoji">' + opp.emoji + '</i>' if opp.emoji else "…"

        cell = {
            'text': " vs " + (opp_vshort if compress else opp.short_name),
            'popover': {'content': [{'text': ''}], 'title': ''}
        }

        if ts.win is True:
            cell['icon'] = "glyphicon-arrow-up text-success"
            cell['sort'] = 1
            cell['popover']['title'] = "Won against " + opp.long_name
        elif ts.win is False:
            cell['icon'] = "glyphicon-arrow-down text-danger"
            cell['sort'] = 2
            cell['popover']['title'] = "Lost to " + opp.long_name
        else: # None
            cell['icon'] = ""
            cell['sort'] = 3
            cell['popover']['title'] = "No result for debate against " + opp.long_name

        if show_score:
            cell['subtext'] = metricformat(ts.score)
            cell['popover']['content'].append(
                {'text': 'Received <strong>%s</strong> team points' % metricformat(ts.score)})

        if show_ballots:
            cell['popover']['content'].append(
                {'text': 'View Debate Ballot', 'link': reverse_tournament('results-public-scoresheet-view',
                    self.tournament, kwargs={'pk': ts.debate_team.debate.id})})

        if self._show_speakers_in_draw:
            cell['popover']['content'].append({'text': "Speakers in <strong>" + opp.short_name + "</strong>: " + ", ".join([s.name for s in opp.speakers])})

        if self._show_record_links:
            cell['popover']['content'].append(
                self._team_record_link(opp))

        return cell

    def add_round_column(self, rounds, key="Round"):
        data = [{
            'sort': round.seq,
            'text': round.abbreviation,
            'tooltip': round.name,
        } for round in rounds]
        self.add_column(key, data)

    def add_adjudicator_columns(self, adjudicators, hide_institution=False,
            hide_metadata=False, subtext=None):

        adj_data = []
        for adj in adjudicators:
            cell = {'text': adj.name}
            if self._show_record_links:
                cell['popover'] = {'content': [self._adjudicator_record_link(adj)]}
            if subtext is 'institution':
                cell['subtext'] = adj.institution.code
            adj_data.append(cell)
        self.add_column("Name", adj_data)

        if self.tournament.pref('show_adjudicator_institutions') and not hide_institution:
            self.add_column({
                'key': "Institution",
                'icon': 'glyphicon-home',
                'tooltip': "Institution",
            }, [adj.institution.code for adj in adjudicators])

        if not hide_metadata:
            adjcore_header = {
                'key': 'adjcore',
                'tooltip': "Member of the Adjudication Core",
                'icon': 'glyphicon-sunglasses',
            }
            self.add_boolean_column(adjcore_header, [adj.adj_core for adj in adjudicators])

            independent_header = {
                'key': 'independent',
                'tooltip': "Independent Adjudicator",
                'icon': 'glyphicon-knight',
            }
            self.add_boolean_column(independent_header, [adj.independent for adj in adjudicators])

        if self.tournament.pref('show_unaccredited'):
            accreddited_header = {
                'key': 'accredited',
                'tooltip': "Is Accredited",
                'icon': 'glyphicon-leaf',
            }
            self.add_boolean_column(accreddited_header, [adj.novice for adj in adjudicators])

    def add_debate_adjudicators_column(self, debates, key="Adjudicators", show_splits=False, highlight_adj=None):
        da_data = []

        def construct_text(adjs_data):
            adjs_list = []
            for a in adjs_data:
                adj_str = a['adj'].name
                symbol = self.ADJ_SYMBOLS.get(a['position'])
                if symbol:
                    adj_str += " " + symbol
                if a.get('split', False):
                    adj_str += " <span class='text-danger'>💢</span>"
                if a['adj'] == highlight_adj:
                    adj_str = "<strong>" + adj_str + "</strong>"
                adjs_list.append(adj_str)
            return ', '.join(adjs_list)

        def construct_popover(adjs_data):
            popover_data = []
            for a in adjs_data:
                descriptors = []
                if a['position'] != AdjudicatorAllocation.POSITION_ONLY:
                    descriptors.append(self.ADJ_POSITION_NAMES[a['position']])
                descriptors.append("from %s" % a['adj'].institution.code)
                if a.get('split', False):
                    descriptors.append("<span class='text-danger'>in minority</span>")

                popover_data.append({'text': "%s (%s)" % (a['adj'].name, ", ".join(descriptors))})
                if self._show_record_links:
                    popover_data.append(self._adjudicator_record_link(a['adj']))

            return popover_data

        for debate in debates:
            adjs_data = []
            if show_splits and (self.admin or self.tournament.pref('show_splitting_adjudicators')) and debate.confirmed_ballot:
                for adj, position, split in debate.confirmed_ballot.ballot_set.adjudicator_results:
                    adjs_data.append(
                        {'adj': adj, 'position': position, 'split': bool(split)})
            else:
                for adj, position in debate.adjudicators.with_positions():
                    adjs_data.append(
                        {'adj': adj, 'position': position})

            if not debate.adjudicators.has_chair and debate.adjudicators.is_panel:
                adjs_data[0]['type'] = 'O'

            da_data.append({
                'text': construct_text(adjs_data),
                'popover': {
                    'title': 'Debate Adjudicators',
                    'content' : construct_popover(adjs_data)
                }
            })

        self.add_column(key, da_data)

    def add_motion_column(self, motions, key="Motion", show_order=False):
        if show_order and self.tournament.pref('enable_motions'):
            self.add_column("Order", [{
                'text': motion.seq,
                'sort': motion.round.seq + (motion.seq * 0.1)
            } for motion in motions])

        motion_data = [{
            'text': motion.reference if motion.reference else '?',
            'popover': {'content' : [{'text': motion.text}]}
        } if motion else "—" for motion in motions]
        self.add_column(key, motion_data)

    def add_team_columns(self, teams, break_categories=False, hide_emoji=False,
                         show_divisions=True, hide_institution=False, key="Team"):

        if self.tournament.pref('enable_divisions') and show_divisions:
            divisions_header = {
                'key': 'Division',
                'icon': 'glyphicon-th-list',
                'tooltip': 'Division'
            }
            divisions = ['D' + t.division.name if t.division else '' for t in teams]
            self.add_column(divisions_header, divisions)

        team_data = [self._team_cell(team, hide_emoji=hide_emoji)
            for team in teams]
        self.add_column(key, team_data)

        if break_categories:
            self.add_column("Categories", [", ".join(bc.name for bc in team.break_categories) for team in teams])

        if self.tournament.pref('show_team_institutions') and not hide_institution:
            self.add_column({
                'key': "Institution",
                'icon': 'glyphicon-home',
                'tooltip': "Institution",
            }, [team.institution.code for team in teams])

    def add_speaker_columns(self, speakers, key="Name"):
        self.add_column(key, [speaker.name for speaker in speakers])
        if self.tournament.pref('show_novices'):
            novice_header = {
                'key': "Novice",
                'icon': 'glyphicon-leaf',
                'tooltip': "Novice Status",
            }
            self.add_boolean_column(novice_header, [speaker.novice for speaker in speakers])

    def add_room_rank_columns(self, debates):
        header = {
            'key': "Room rank",
            'icon': 'glyphicon-stats',
            'tooltip': 'Room rank of this debate'
        }
        self.add_column(header, [debate.room_rank for debate in debates])

    def add_debate_bracket_columns(self, debates):
        header = {
            'key': "Bracket",
            'icon': 'glyphicon-stats',
            'tooltip': 'Bracket of this debate'
        }

        def _fmt(x):
            if int(x) == x:
                return int(x)
            return x

        self.add_column(header, [_fmt(debate.bracket) for debate in debates])

    def add_debate_venue_columns(self, debates, with_times=True):
        if self.tournament.pref('enable_divisions') and len(debates) > 0:
            if debates[0].round.stage is debates[0].round.STAGE_PRELIMINARY:
                divisions_header = {
                    'key': 'Division',
                    'icon': 'glyphicon-th-list',
                    'tooltip': 'Division'
                }
                divisions_data = ['D' + d.division.name if d.division else '' for d in debates]
                self.add_column(divisions_header, divisions_data)

        venue_header = {
            'key': "Venue",
            'icon': 'glyphicon-map-marker',
        }
        if self.tournament.pref('enable_venue_groups'):
            venue_data = [
                debate.division.venue_group.short_name if debate.division
                else (debate.venue.group.short_name + ' ' + debate.venue.name) if debate.venue and debate.venue.group
                else debate.venue.name if debate.venue
                else ''
                for debate in debates
            ]
        else:
            venue_data = [debate.venue.name if debate.venue else '' for debate in debates]
        self.add_column(venue_header, venue_data)

        if with_times and self.tournament.pref('enable_debate_scheduling'):
            times_headers = ["Date", "Time"]
            times_data = []
            for debate in debates:
                if debate.aff_team.type == Team.TYPE_BYE or debate.neg_team.type == Team.TYPE_BYE:
                    times_data.append(["", "Bye"])
                elif debate.result_status == Debate.STATUS_POSTPONED:
                    times_data.append(["", "Postponed"])
                elif debate.confirmed_ballot and debate.confirmed_ballot.forfeit:
                    times_data.append(["", "Forfeit"])
                elif debate.time:
                    times_data.append([
                        formats.date_format(debate.time, "D jS F"),
                        formats.date_format(debate.time, "h:i A")])
                else:
                    times_data.append(["", ""])
            self.add_columns(times_headers, times_data)

    def add_draw_conflicts_columns(self, debates):
        venue_conflicts_by_debate = venue_conflicts_display(debates)  # dict of {debate: [conflicts]}
        adjudicator_conflicts_by_debate = adjudicator_conflicts_display(debates)  # dict of {debate: [conflicts]}

        conflicts_by_debate = []
        for debate in debates:
            # conflicts is a list of (level, message) tuples
            conflicts = [("warning", flag) for flag in debate.get_flags_display()]
            history = debate.history
            if history > 0:
                conflicts.append(("warning", "Teams have met " +
                    ("once" if history == 1 else "twice" if history == 2 else "%d times" % history)))
            if debate.aff_team.institution_id == debate.neg_team.institution_id:
                conflicts.append(("warning", "Teams are from the same institution"))
            conflicts.extend(adjudicator_conflicts_by_debate[debate])
            conflicts.extend(venue_conflicts_by_debate[debate])
            conflicts_by_debate.append(conflicts)

        conflicts_header = {'key': "Conflicts/Flags"}
        conflicts_data = [{
            'text': "".join(["<div class=\"text-{0}\">{1}</div>".format(*conflict) for conflict in conflicts]),
            'class': 'small'
        } for conflicts in conflicts_by_debate]
        self.add_column(conflicts_header, conflicts_data)

    def _standings_headers(self, info_list, side=None):
        side_abbr = get_position_name(self.tournament, side, 'initial') if side else ''
        side_possessive = get_position_name(self.tournament, side, 'possessive') if side else ''
        headers = []
        for info in info_list:
            # Translators: Put these in the right order,
            # e.g. side_abbr = "A", metric_abbr = "TSS" -> "ATSS" for "Affirmative's Total Speaker Score"
            key = _("%(side_abbr)s%(metric_abbr)s") % {
                'side_abbr': side_abbr,
                'metric_abbr': info['abbr'],
            }

            # Translators: Put these in the right order,
            # e.g. side_possessive = "affirmative's", metric_name = "total speaker score" -> "affirmative's total speaker score"
            tooltip = _("%(side_possessive)s %(metric_name)s") % {
                'side_possessive': side_possessive,
                'metric_name': info['name'],
            }
            tooltip = tooltip.strip().capitalize()
            header = {
                'key': key,
                'tooltip': tooltip,
                'glyphicon': info['glyphicon']
            }
            headers.append(header)
        return headers

    def add_ranking_columns(self, standings, subset=None, side=None):
        standings_list = standings.get_standings(subset) if subset is not None else standings
        headers = self._standings_headers(standings.rankings_info(), side)
        data = []
        for standing in standings_list:
            data.append([{
                'text': rankingformat(ranking),
                'sort': ranking[0] or "99999",
            } for ranking in standing.iterrankings()])
        self.add_columns(headers, data)

    def add_debate_ranking_columns(self, draw, standings):
        # First half (ie all aff metrics) then second (ie all neg metrics)
        self.add_ranking_columns(standings, subset=[d.aff_team for d in draw], side='aff')
        self.add_ranking_columns(standings, subset=[d.neg_team for d in draw], side='neg')

    def add_metric_columns(self, standings, subset=None, side=None):
        standings_list = standings.get_standings(subset) if subset is not None else standings
        headers = self._standings_headers(standings.metrics_info(), side)
        data = [list(map(metricformat, s.itermetrics())) for s in standings_list]
        self.add_columns(headers, data)

    def add_debate_metric_columns(self, draw, standings):
        # First half (ie all aff metrics) then second (ie all neg metrics)
        self.add_metric_columns(standings, subset=[d.aff_team for d in draw], side='aff')
        self.add_metric_columns(standings, subset=[d.neg_team for d in draw], side='neg')

    def highlight_rows_by_column_value(self, column):
        highlighted_rows = [i for i in range(1, len(self.data))
                if self.data[i][column] != self.data[i-1][column]]
        for i in highlighted_rows:
            self.data[i] = [self._convert_cell(cell) for cell in self.data[i]]
            for cell in self.data[i]:
                cell['class'] = cell.get('class', '') + ' highlight-row'

    def add_sides_count(self, teams, round, team_type):
        sides_counts = get_side_counts(teams, DebateTeam.POSITION_AFFIRMATIVE, round.seq)

        # Translators: e.g. team would be "negative team" or "affirmative team",
        # affirmative would be "affirmative team".
        side_label = _("Number of times this %(team)s has been the "
            "%(affirmative)s before") % {
            'team': get_position_name(self.tournament, team_type, "team"),
            'affirmative': aff_team(self.tournament),
        }

        # Translators: Abbreviation for "affirmative number of affirmatives".
        # side_abbr is e.g. "A"/"N"/"G"/"O" for affirmative/negative/government/opposition,
        # aff_abbr is "Aff"/"Gov" for affirmative/government, so "NAff" is the number of times
        # the negative team has affirmed, or equivalently "OGov".
        side_key = _("%(side_abbr)s%(aff_abbr)ss") % {
            'side_abbr': get_position_name(self.tournament, team_type, 'initial'),
            'aff_abbr': aff_abbr(self.tournament),
        }

        sides_header = {
            'key':  side_key,
            'tooltip': side_label,
        }
        sides_data = [{
            'text': str(sides_counts[t.id]),
        } for t in teams]
        self.add_column(sides_header, sides_data)

    def add_checkbox_columns(self, states, references, key):
        state_header = {'key': key}
        state_data = [{
            'sort': state,
            'class': 'checkbox-target',
            'text': '<input type="checkbox" class="vue-table-checkbox" data-target="%s" %s>' % (reference, 'checked' if state else ''),
        } for state, reference in zip(states, references)]
        self.add_column(state_header, state_data)

    def add_debate_ballot_link_column(self, debates):
        ballot_links_header = {'key': "Ballot", 'icon': 'glyphicon-search'}

        if self.admin:
            ballot_links_data = [{
                'text': "View/Edit Ballot",
                'link': reverse_tournament('results-ballotset-edit', self.tournament, kwargs={'pk': debate.confirmed_ballot.id})
            } if debate.confirmed_ballot else "" for debate in debates]
            self.add_column(ballot_links_header, ballot_links_data)

        elif self.tournament.pref('ballots_released'):
            ballot_links_header = {'key': "Ballot", 'icon': 'glyphicon-search'}
            ballot_links_data = [{
                'text': "View Ballot",
                'link': reverse_tournament('results-public-scoresheet-view', self.tournament, kwargs={'pk': debate.id})
            } if debate else "" for debate in debates]
            self.add_column(ballot_links_header, ballot_links_data)

    def add_debate_result_by_team_columns(self, teamscores):
        """Takes an iterable of TeamScore objects."""

        results_data = [self._result_cell(ts) for ts in teamscores]
        self.add_column("Result", results_data)
        self.add_column("Side", [ts.debate_team.get_position_name().capitalize() for ts in teamscores])

    def add_team_results_columns(self, teams, rounds):
        """ Takes an iterable of Teams, assumes their round_results match rounds"""
        for round_seq, round in enumerate(rounds):
            results = [self._result_cell(
                t.round_results[round_seq]) for t in teams]
            self.add_column(round.abbreviation, results)

    def add_debate_results_columns(self, debates):
        results_data = []
        for debate in debates:
            row = []
            for pos in ('aff', 'neg'):
                try:
                    debateteam = debate.get_dt(pos)
                    team = debate.get_team(pos)
                except ObjectDoesNotExist:
                    row.append("-")
                    continue
                except MultipleObjectsReturned:
                    row.append("<error>")
                    continue

                cell = self._team_cell(team, hide_emoji=True)

                if debateteam.win is True:
                    cell['popover']['title'] += "—won"
                    cell['icon'] = "glyphicon-arrow-up text-success"
                elif debateteam.win is False:
                    cell['popover']['title'] += "—lost"
                    cell['icon'] = "glyphicon-arrow-down text-danger"
                else: # None
                    cell['popover']['title'] += "—no result"
                    cell['icon'] = ""

                row.append(cell)
            results_data.append(row)

        self.add_columns([aff_name(self.tournament).capitalize(), neg_name(self.tournament).capitalize()], results_data)

    def add_standings_results_columns(self, standings, rounds, show_ballots):

        for round_seq, round in enumerate(rounds):
            results = [self._result_cell(
                s.round_results[round_seq],
                compress=True,
                show_score=True,
                show_ballots=show_ballots
            ) for s in standings]
            self.add_column(round.abbreviation, results)
