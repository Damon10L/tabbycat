import datetime
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.contrib.humanize.templatetags.humanize import naturaltime
from django.db import ProgrammingError
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import render
from django.views.generic import FormView, TemplateView, View

from actionlog.mixins import LogActionMixin
from actionlog.models import ActionLogEntry
from adjallocation.models import DebateAdjudicator
from draw.models import Debate, DebateTeam
from draw.prefetch import populate_opponents
from participants.models import Adjudicator
from tournaments.mixins import (PublicTournamentPageMixin, RoundMixin, SingleObjectByRandomisedUrlMixin,
                                SingleObjectFromTournamentMixin, TournamentMixin)
from tournaments.models import Round
from utils.misc import get_ip_address, redirect_round, reverse_round, reverse_tournament
from utils.mixins import (CacheMixin, JsonDataResponsePostView, JsonDataResponseView,
                          SuperuserOrTabroomAssistantTemplateResponseMixin,
                          SuperuserRequiredMixin, VueTableTemplateView)
from utils.tables import TabbycatTableBuilder
from venues.models import Venue

from .forms import BallotSetForm
from .models import BallotSubmission, TeamScore
from .tables import ResultsTableBuilder
from .prefetch import populate_confirmed_ballots
from .utils import ballot_checkin_number_left, get_result_status_stats, populate_identical_ballotsub_lists

logger = logging.getLogger(__name__)


class PublicResultsIndexView(PublicTournamentPageMixin, TemplateView):

    template_name = 'public_results_index.html'
    public_page_preference = 'public_results'

    def get_context_data(self, **kwargs):
        tournament = self.get_tournament()
        kwargs["rounds"] = tournament.round_set.filter(
            seq__lt=tournament.current_round.seq,
            silent=False).order_by('seq')
        return super().get_context_data(**kwargs)


# ==============================================================================
# Views that show the results for all rounds in a debate
# ==============================================================================

class ResultsEntryForRoundView(RoundMixin, LoginRequiredMixin, VueTableTemplateView):

    template_name = 'results.html'

    def _get_draw(self):
        if not hasattr(self, '_draw'):
            if self.request.user.is_superuser:
                filter_kwargs = None
            else:
                filter_kwargs = dict(result_status__in=[Debate.STATUS_NONE, Debate.STATUS_DRAFT])
            self._draw = self.get_round().debate_set_with_prefetches(
                    ordering=('room_rank',), ballotsets=True, wins=True,
                    filter_kwargs=filter_kwargs)
        return self._draw

    def get_table(self):
        draw = self._get_draw()
        table = ResultsTableBuilder(view=self,
            admin=self.request.user.is_superuser, sort_key="Status")
        table.add_ballot_status_columns(draw)
        table.add_ballot_entry_columns(draw)
        table.add_debate_venue_columns(draw)
        table.add_debate_results_columns(draw)
        table.add_debate_adjudicators_column(draw, show_splits=True)
        return table

    def get_context_data(self, **kwargs):
        round = self.get_round()
        result_status_stats = get_result_status_stats(round)

        kwargs["stats"] = {
            'none': result_status_stats[Debate.STATUS_NONE],
            'ballot_in': result_status_stats['B'],
            'draft': result_status_stats[Debate.STATUS_DRAFT],
            'confirmed': result_status_stats[Debate.STATUS_CONFIRMED],
            'postponed': result_status_stats[Debate.STATUS_POSTPONED],
            'total': len(self._get_draw())
        }

        kwargs["has_motions"] = round.motion_set.count() > 0
        return super().get_context_data(**kwargs)


class PublicResultsForRoundView(RoundMixin, PublicTournamentPageMixin, VueTableTemplateView):

    template_name = "public_results_for_round.html"
    public_page_preference = 'public_results'
    page_title = 'Results'
    page_emoji = '💥'
    default_view = 'team'

    def get_table(self):
        view_type = self.request.session.get('results_view', self.default_view)
        if view_type == 'debate':
            return self.get_table_by_debate()
        else:
            return self.get_table_by_team()

    def get_table_by_debate(self):
        round = self.get_round()
        tournament = self.get_tournament()
        debates = round.debate_set_with_prefetches(ballotsets=True, wins=True)

        table = TabbycatTableBuilder(view=self, sort_key="Venue")
        table.add_debate_venue_columns(debates)
        table.add_debate_results_columns(debates)
        table.add_debate_ballot_link_column(debates)
        table.add_debate_adjudicators_column(debates, show_splits=True)
        if tournament.pref('show_motions_in_results'):
            table.add_motion_column([d.confirmed_ballot.motion
                if d.confirmed_ballot else None for d in debates])

        return table

    def get_table_by_team(self):
        round = self.get_round()
        tournament = self.get_tournament()
        teamscores = TeamScore.objects.filter(debate_team__debate__round=round,
                ballot_submission__confirmed=True).prefetch_related(
                'debate_team__team__speaker_set', 'debate_team__team__institution',
                'debate_team__debate__debateadjudicator_set__adjudicator')
        debates = [ts.debate_team.debate for ts in teamscores]

        populate_opponents([ts.debate_team for ts in teamscores])

        for pos in [DebateTeam.POSITION_AFFIRMATIVE, DebateTeam.POSITION_NEGATIVE]:
            debates_for_pos = [ts.debate_team.debate for ts in teamscores if ts.debate_team.position == pos]
            populate_confirmed_ballots(debates_for_pos, motions=True)

        table = TabbycatTableBuilder(view=self, sort_key="Team")
        table.add_team_columns([ts.debate_team.team for ts in teamscores])
        table.add_debate_result_by_team_columns(teamscores)
        table.add_debate_ballot_link_column(debates)
        table.add_debate_adjudicators_column(debates, show_splits=True)
        if tournament.pref('show_motions_in_results'):
            table.add_motion_column([debate.confirmed_ballot.motion
                if debate.confirmed_ballot else None for debate in debates])

        return table

    def get(self, request, *args, **kwargs):
        tournament = self.get_tournament()
        round = self.get_round()
        if round.silent and not tournament.pref('all_results_released'):
            logger.warning("Refused results for %s: silent", round.name)
            return render(request, 'public_results_silent.html')
        if round.seq >= tournament.current_round.seq and not tournament.pref('all_results_released'):
            logger.warning("Refused results for %s: not yet available", round.name)
            return render(request, 'public_results_not_available.html')

        # If there's a query string, store the session setting
        if request.GET.get('view') in ['team', 'debate']:
            request.session['results_view'] = request.GET['view']

            # Test saving it explicitly, if it doesn't work then prevent middlware
            # from saving it. This can happen if write permissions to the database
            # are revoked because the database has reached its row limit.
            try:
                request.session.save()
            except ProgrammingError as e:
                logger.error("Could not save session: " + str(e))
                request.session.modified = False

        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs['view_type'] = self.request.session.get('results_view', self.default_view)
        return super().get_context_data(**kwargs)


# ==============================================================================
# Views that update the debate status (only)
# ==============================================================================

class BaseUpdateDebateStatusView(SuperuserRequiredMixin, RoundMixin, View):

    def post(self, request, *args, **kwargs):
        debate_id = request.POST['debate_id']
        try:
            debate = Debate.objects.get(round=self.get_round(), id=debate_id)
        except Debate.DoesNotExist:
            return HttpResponseBadRequest("Error: There isn't a debate in {} with id {}.".format(self.get_round().name, debate_id))
        debate.result_status = self.new_status
        debate.save()
        return redirect_round('results-round-list', debate.round)


class PostponeDebateView(BaseUpdateDebateStatusView):
    new_status = Debate.STATUS_POSTPONED


class UnpostponeDebateView(BaseUpdateDebateStatusView):
    new_status = Debate.STATUS_NONE


# ==============================================================================
# Ballot entry form views
# ==============================================================================

class BaseBallotSetView(LogActionMixin, FormView):
    """Base class for views displaying ballot set entry forms."""

    form_class = BallotSetForm
    action_log_content_object_attr = 'ballotsub'

    def get_context_data(self, **kwargs):
        kwargs['ballotsub'] = self.ballotsub
        kwargs['debate'] = self.debate
        kwargs['all_ballotsubs'] = self.get_all_ballotsubs()
        kwargs['new'] = self.relates_to_new_ballotsub
        return super().get_context_data(**kwargs)

    def get_all_ballotsubs(self):
        all_ballotsubs = self.debate.ballotsubmission_set.order_by('version').select_related('submitter', 'confirmer', 'motion')
        if not self.request.user.is_superuser:
            all_ballotsubs = all_ballotsubs.exclude(discarded=True)
        populate_identical_ballotsub_lists(all_ballotsubs)
        return all_ballotsubs

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['ballotsub'] = self.ballotsub
        return kwargs

    def add_success_message(self):
        # Default implementation does nothing.
        pass

    def form_valid(self, form):
        self.ballotsub = form.save()
        if self.ballotsub.confirmed:
            self.ballotsub.confirmer = self.request.user
            self.ballotsub.confirm_timestamp = datetime.datetime.now()
            self.ballotsub.save()
        self.add_success_message()
        self.round = self.ballotsub.debate.round  # for LogActionMixin
        return super().form_valid(form)

    def populate_objects(self):
        """Subclasses must implement this method to set `self.ballotsub` and
        `self.debate`. If it returns something other than None, its return
        value will be used as the response, bypassing ordinary template
        rendering."""
        raise NotImplementedError

    def get(self, request, *args, **kwargs):
        error_response = self.populate_objects()
        if error_response:
            return error_response
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        error_response = self.populate_objects()
        if error_response:
            return error_response
        return super().post(request, *args, **kwargs)


class BaseAdminBallotSetView(SuperuserOrTabroomAssistantTemplateResponseMixin, BaseBallotSetView):
    superuser_template_name = 'enter_results.html'
    assistant_template_name = 'assistant_enter_results.html'

    def get_success_url(self):
        return reverse_round('results-round-list', self.ballotsub.debate.round)


class NewBallotSetView(SingleObjectFromTournamentMixin, BaseAdminBallotSetView):

    model = Debate
    tournament_field_name = 'round__tournament'
    relates_to_new_ballotsub = True
    action_log_type = ActionLogEntry.ACTION_TYPE_BALLOT_CREATE
    pk_url_kwarg = 'debate_id'

    def add_success_message(self):
        messages.success(self.request, "Ballot set for %s added." % self.debate.matchup)

    def populate_objects(self):
        self.debate = self.object = self.get_object()
        self.ballotsub = BallotSubmission(debate=self.debate, submitter=self.request.user,
            submitter_type=BallotSubmission.SUBMITTER_TABROOM,
            ip_address=get_ip_address(self.request))

        if not self.debate.adjudicators.has_chair:
            messages.error(self.request, "Whoops! The debate %s doesn't have a chair, "
                "so you can't enter results for it." % self.debate.matchup)
            return redirect_round('results-round-list', self.ballotsub.debate.round)


class EditBallotSetView(SingleObjectFromTournamentMixin, BaseAdminBallotSetView):

    model = BallotSubmission
    tournament_field_name = 'debate__round__tournament'
    relates_to_new_ballotsub = False

    def get_action_log_type(self):
        if self.ballotsub.discarded:
            return ActionLogEntry.ACTION_TYPE_BALLOT_DISCARD
        elif self.ballotsub.confirmed:
            return ActionLogEntry.ACTION_TYPE_BALLOT_CONFIRM
        else:
            return ActionLogEntry.ACTION_TYPE_BALLOT_EDIT

    def add_success_message(self):
        if self.ballotsub.discarded:
            messages.success(self.request, "Ballot set for %s discarded." % self.debate.matchup)
        elif self.ballotsub.confirmed:
            messages.success(self.request, "Ballot set for %s confirmed." % self.debate.matchup)
        else:
            messages.success(self.request, "Edits to ballot set for %s saved." % self.debate.matchup)

    def populate_objects(self):
        self.ballotsub = self.object = self.get_object()
        self.debate = self.ballotsub.debate


class BasePublicNewBallotSetView(PublicTournamentPageMixin, BaseBallotSetView):

    template_name = 'public_enter_results.html'
    relates_to_new_ballotsub = True
    action_log_type = ActionLogEntry.ACTION_TYPE_BALLOT_SUBMIT

    def get_success_url(self):
        return reverse_tournament('tournament-public-index', self.get_tournament())

    def add_success_message(self):
        messages.success(self.request, "Thanks, %s! Your ballot for %s has been recorded." % (
                self.object.name, self.debate.matchup))

    def populate_objects(self):
        self.object = self.get_object() # must be populated before self.error_page() called

        round = self.get_tournament().current_round
        if round.draw_status != Round.STATUS_RELEASED or not round.motions_released:
            return self.error_page("The draw and/or motions for the round haven't been released yet.")

        try:
            self.debateadj = DebateAdjudicator.objects.get(adjudicator=self.object, debate__round=round)
        except DebateAdjudicator.DoesNotExist:
            return self.error_page("It looks like you don't have a debate this round.")
        except DebateAdjudicator.MultipleObjectsReturned:
            return self.error_page("It looks like you're assigned to two or more debates this round. "
                    "Please contact a tab room official.")

        self.debate = self.debateadj.debate
        self.ballotsub = BallotSubmission(debate=self.debate, ip_address=get_ip_address(self.request),
            submitter_type=BallotSubmission.SUBMITTER_PUBLIC)

        if not self.debate.adjudicators.has_chair:
            return self.error_page("Your debate doesn't have a chair, so you can't enter results for it. "
                    "Please contact a tab room official.")

    def error_page(self, message):
        # This bypasses the normal TemplateResponseMixin and ContextMixin
        # machinery, to avoid loading the error page with potentially
        # confidentiality-compromising context.
        context = {'adjudicator': self.object, 'message': message}
        return self.response_class(
            request=self.request,
            template='public_enter_results_error.html',
            context=context,
            using=self.template_engine
        )


class PublicNewBallotSetByIdUrlView(SingleObjectFromTournamentMixin, BasePublicNewBallotSetView):
    model = Adjudicator
    pk_url_kwarg = 'adj_id'
    allow_null_tournament = True
    public_page_preference = 'public_ballots'


class PublicNewBallotSetByRandomisedUrlView(SingleObjectByRandomisedUrlMixin, BasePublicNewBallotSetView):
    model = Adjudicator
    allow_null_tournament = True
    public_page_preference = 'public_ballots_randomised'


# ==============================================================================
# JSON views for tournament overview page
# ==============================================================================

class BallotsStatusJsonView(LoginRequiredMixin, RoundMixin, JsonDataResponseView):

    def get_data(self):

        rd = self.get_round()
        ballots = BallotSubmission.objects.filter(debate__round=rd, discarded=False)

        # For each debate, find (a) the first non-discarded submission time, and
        # (b) the last confirmed confirmation time. (Note that this means when
        # a ballot is discarded, the graph will change retrospectively.)
        first_drafts = {}   # keys: debate IDs, values: timestamps
        confirmations = {}  # keys: debate IDs, values: timestamps
        for ballot in ballots:
            did = ballot.debate_id
            if ballot.timestamp and (did not in first_drafts or first_drafts[did] > ballot.timestamp):
                first_drafts[did] = ballot.timestamp
            if ballot.confirmed and ballot.confirm_timestamp and (did not in confirmations or
                    confirmations[did] < ballot.confirm_timestamp):
                confirmations[did] = ballot.confirm_timestamp

        # Collate timestamps into a single list. Tuples are (time, none_change, draft_change, confirmed_change)
        first_draft_timestamps = [(time, -1, +1, 0) for time in first_drafts.values()]
        confirmation_timestamps = [(time, 0, -1, +1) for time in confirmations.values()]
        timestamps = sorted(first_draft_timestamps + confirmation_timestamps)

        if len(timestamps) == 0:
            return []

        # Generate the timeline, including one-minute margins on either side
        margin = datetime.timedelta(minutes=1)
        none = rd.debate_set.count()
        draft = 0
        confirmed = 0
        stats = [[(timestamps[0][0] - margin).isoformat(), none, draft, confirmed]]
        for time, none_change, draft_change, confirmed_change in timestamps:
            time_iso = time.isoformat()
            stats.append([time_iso, none, draft, confirmed])
            none += none_change
            draft += draft_change
            confirmed += confirmed_change
            stats.append([time_iso, none, draft, confirmed])
        stats.append([(timestamps[-1][0] + margin).isoformat(), none, draft, confirmed])

        return stats


class LatestResultsJsonView(LoginRequiredMixin, TournamentMixin, JsonDataResponseView):

    def get_data(self):

        ballotsubs = BallotSubmission.objects.filter(
            debate__round__tournament=self.get_tournament(), confirmed=True
        ).prefetch_related(
            'teamscore_set__debate_team', 'teamscore_set__debate_team__team'
        ).order_by('-timestamp')[:15]

        results_objects = []
        for ballotsub in ballotsubs:
            winner = '?'
            loser = '?'
            for teamscore in ballotsub.teamscore_set.all():
                team_str = "{:s} ({:s})".format(teamscore.debate_team.team.short_name,
                        teamscore.debate_team.get_position_name(self.get_tournament()))
                if teamscore.win:
                    winner = team_str
                else:
                    loser = team_str

            results_objects.append({
                'user': winner + ' beat ' + loser,
                'timestamp': naturaltime(ballotsub.timestamp),
            })

        return results_objects


# ==============================================================================
# Ballot check-in views
# ==============================================================================

class DebateBallotCheckinError(Exception):
    pass


class BallotCheckinView(LoginRequiredMixin, RoundMixin, TemplateView):
    template_name = 'ballot_checkin.html'

    def get_page_subtitle(self):
        """Override RoundMixin to allow template subtitle to take precedence."""
        return ""

    def get_context_data(self, **kwargs):
        kwargs['ballots_left'] = ballot_checkin_number_left(self.get_round())

        if self.get_tournament().pref('enable_venue_groups'):
            ordering = ('group__short_name', 'name')
        else:
            ordering = ('name',)
        kwargs['venue_options'] = Venue.objects.filter(debate__round=self.get_round(),
                debate__ballot_in=False).order_by(*ordering)

        return super().get_context_data(**kwargs)


class BaseBallotCheckinJsonResponseView(LoginRequiredMixin, RoundMixin, JsonDataResponsePostView):

    def get_debate(self):

        venue_id = self.request.POST.get('venue')

        if venue_id is None:
            raise DebateBallotCheckinError('There aren\'t any venues with that name.')

        # TODO: The below errors are all hangovers from when searches were by
        # name only. They can still in theory happen, if an administrator
        # changes things (e.g. deletes or reassigns a venue) and the client
        # doesn't reload the page, so that the client is working on outdated
        # information. Nonetheless, this workflow needs to be reworked for the
        # new paradigm of selecting venues from a predefined list, while keeping
        # the UI textbox-centric.

        try:
            venue = Venue.objects.get(id=venue_id)
        except Venue.DoesNotExist:
            raise DebateBallotCheckinError("There aren't any venues with that name.")

        try:
            debate = Debate.objects.get(round=self.get_round(), venue=venue)
        except Debate.DoesNotExist:
            raise DebateBallotCheckinError("There wasn't a debate in venue %s this round." % (venue.name,))
        except Debate.MultipleObjectsReturned:
            raise DebateBallotCheckinError("There appear to be multiple debates in venue %s this round." % (venue.name,))

        if debate.ballot_in:
            raise DebateBallotCheckinError("The ballot for venue %s has already been checked in." % (venue.name,))

        return debate


class BallotCheckinGetDetailsView(BaseBallotCheckinJsonResponseView):

    def post_data(self):
        try:
            debate = self.get_debate()
        except DebateBallotCheckinError as e:
            return {'exists': False, 'message': str(e)}

        return {
            'exists': True,
            'venue': debate.venue.name,
            'venue_id': debate.venue.id,
            'aff_team': debate.aff_team.short_name,
            'neg_team': debate.neg_team.short_name,
            'num_adjs': len(debate.adjudicators),
            'adjudicators': [adj.name for adj in debate.adjudicators.voting()],
            'ballots_left': ballot_checkin_number_left(self.get_round()),
        }


class PostBallotCheckinView(LogActionMixin, BaseBallotCheckinJsonResponseView):

    action_log_type = ActionLogEntry.ACTION_TYPE_BALLOT_CHECKIN
    action_log_content_object_attr = 'debate'

    def post_data(self):
        try:
            self.debate = self.get_debate()
        except DebateBallotCheckinError as e:
            return {'success': False, 'message': str(e)}

        self.debate.ballot_in = True
        self.debate.save()

        self.log_action()

        return {
            'success': True,
            'venue': self.debate.venue.name,
            'matchup': self.debate.matchup,
            'ballots_left': ballot_checkin_number_left(self.get_round()),
        }


# ==============================================================================
# Other public views
# ==============================================================================

class PublicBallotScoresheetsView(CacheMixin, PublicTournamentPageMixin, SingleObjectFromTournamentMixin, TemplateView):
    """Public view showing the confirmed ballots for a debate as scoresheets."""

    model = Debate
    public_page_preference = 'ballots_released'
    tournament_field_name = 'round__tournament'
    template_name = 'public_ballot_set.html'

    def get_object(self):
        debate = super().get_object()

        round = debate.round
        if round.silent and not round.tournament.pref('all_results_released'):
            logger.warning("Refused public view of ballots for %s: %s is silent", debate, round.name)
            raise Http404("This debate is in %s, which is a silent round." % round.name)
        if round.seq >= round.tournament.current_round.seq and not round.tournament.pref('all_results_released'):
            logger.warning("Refused public view of ballots for %s: %s results not yet available", debate, round.name)
            raise Http404("This debate is in %s, the results for which aren't available yet." % round.name)

        if debate.result_status != Debate.STATUS_CONFIRMED:
            logger.warning("Refused public view of ballots for %s: not confirmed", debate)
            raise Http404("The result for debate %s is not confirmed." % debate.matchup)
        if debate.confirmed_ballot is None:
            logger.warning("Refused public view of ballots for %s: no confirmed ballot", debate)
            raise Http404("The debate %s does not have a confirmed ballot." % debate.matchup)

        return debate

    def get_context_data(self, **kwargs):
        kwargs['ballot_set'] = self.object.confirmed_ballot.ballot_set
        return super().get_context_data(**kwargs)

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        return super().get(self, request, *args, **kwargs)


class PublicBallotSubmissionIndexView(CacheMixin, PublicTournamentPageMixin, TemplateView):
    """Public view listing all debate-adjudicators for the current round, as
    links for them to enter their ballots."""

    public_page_preference = 'public_ballots'

    def is_draw_released(self):
        round = self.get_tournament().current_round
        return round.draw_status == Round.STATUS_RELEASED and round.motions_good_for_public

    def get_template_names(self):
        if self.is_draw_released():
            return ['public_add_ballot.html']
        else:
            return ['public_add_ballot_unreleased.html']

    def get_context_data(self, **kwargs):
        if self.is_draw_released():
            kwargs['das'] = DebateAdjudicator.objects.filter(
                debate__round=self.get_tournament().current_round).select_related('adjudicator', 'debate')
        return super().get_context_data(**kwargs)
