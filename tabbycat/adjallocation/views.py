import json
import logging

from django.db.utils import IntegrityError
from django.views.generic.base import TemplateView, View
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import render

from actionlog.mixins import LogActionMixin
from actionlog.models import ActionLogEntry
from breakqual.utils import categories_ordered
from draw.models import Debate, DebateTeam
from participants.models import Adjudicator, Region, Team
from participants.utils import regions_ordered
from tournaments.models import Round
from tournaments.mixins import RoundMixin
from utils.mixins import JsonDataResponseView, SuperuserRequiredMixin
from utils.views import admin_required, expect_post, round_view

from .allocator import allocate_adjudicators
from .allocation import AdjudicatorAllocation
from .hungarian import HungarianAllocator
from .models import AdjudicatorAdjudicatorConflict, AdjudicatorConflict, AdjudicatorInstitutionConflict, DebateAdjudicator
from .utils import adjs_to_json, debates_to_json, get_adjs, populate_conflicts, populate_histories, teams_to_json


logger = logging.getLogger(__name__)


@admin_required
@expect_post
@round_view
def create_adj_allocation(request, round):

    if round.draw_status == round.STATUS_RELEASED:
        return HttpResponseBadRequest("Draw is already released, unrelease draw to redo auto-allocations.")
    if round.draw_status != round.STATUS_CONFIRMED:
        return HttpResponseBadRequest("Draw is not confirmed, confirm draw to run auto-allocations.")

    allocate_adjudicators(round, HungarianAllocator)

    return _json_adj_allocation(round.get_draw(), round.unused_adjudicators())


@admin_required
@expect_post
@round_view
def update_debate_importance(request, round):
    id = int(request.POST.get('debate_id'))
    im = int(request.POST.get('value'))
    debate = Debate.objects.get(pk=id)
    debate.importance = im
    debate.save()
    ActionLogEntry.objects.log(type=ActionLogEntry.ACTION_TYPE_DEBATE_IMPORTANCE_EDIT,
                               user=request.user, content_object=debate, tournament=round.tournament)
    return HttpResponse(im)


@admin_required
@round_view
def draw_adjudicators_edit(request, round):
    context = dict()
    context['draw'] = draw = round.get_draw()
    context['adj0'] = Adjudicator.objects.first()
    context['duplicate_adjs'] = round.tournament.pref('duplicate_adjs')
    context['feedback_headings'] = [
        q.name for q in round.tournament.adj_feedback_questions]

    def calculate_prior_adj_genders(team):
        debates = team.get_debates(round.seq)
        adjs = DebateAdjudicator.objects.filter(
            debate__in=debates).count()
        male_adjs = DebateAdjudicator.objects.filter(
            debate__in=debates, adjudicator__gender="M").count()
        if male_adjs > 0:
            male_adj_percent = int(male_adjs / adjs * 100)
            return male_adj_percent
        else:
            return 0

    for debate in draw:
        aff_male_adj_percent = calculate_prior_adj_genders(debate.aff_team)
        debate.aff_team.male_adj_percent = aff_male_adj_percent

        neg_male_adj_percent = calculate_prior_adj_genders(debate.neg_team)
        debate.neg_team.male_adj_percent = neg_male_adj_percent

        if neg_male_adj_percent > aff_male_adj_percent:
            debate.gender_class = (neg_male_adj_percent // 5) - 10
        else:
            debate.gender_class = (aff_male_adj_percent // 5) - 10

    regions = Region.objects.filter(institution__team__tournament=round.tournament).order_by('name').distinct()
    break_categories = round.tournament.breakcategory_set.order_by(
        'seq').exclude(is_general=True)
    # TODO: colors below are redundant
    colors = ["#C70062", "#00C79B", "#B1E001", "#476C5E",
              "#777", "#FF2983", "#6A268C", "#00C0CF", "#0051CF"]
    context['regions'] = list(zip(regions, colors + ["black"] * (len(regions) -
                                                                 len(colors))))
    context['break_categories'] = list(zip(
        break_categories, colors + ["black"] * (len(break_categories) -
                                                len(colors))))

    return render(request, "draw_adjudicators_edit.html", context)


class SaveAdjudicatorsView(SuperuserRequiredMixin, RoundMixin, View):

    def post(self, request, *args, **kwargs):

        # Example request.POST:
        # {'debate_6': ['true'], 'chair_7': ['79'], 'panel_1[]': ['89', '94']}

        def _extract_id(s):
            return int(s.replace('[]', '').split('_')[1])

        debate_ids = [_extract_id(key) for key in request.POST if key.startswith("debate_")]
        debates = Debate.objects.in_bulk(debate_ids)
        allocations = {d_id: AdjudicatorAllocation(debate) for d_id, debate in debates.items()}

        for key, values in request.POST.lists():
            if key.startswith("debate_"):
                continue

            alloc = allocations[_extract_id(key)]
            adjs = [Adjudicator.objects.get(id=int(x)) for x in values]
            if key.startswith("chair_"):
                if len(adjs) > 1:
                    logger.warning("There was more than one chair for debate %s, only saving the first", alloc.debate)
                    continue
                alloc.chair = adjs[0]
            elif key.startswith("panel_"):
                alloc.panellists.extend(adjs)
            elif key.startswith("trainees_"):
                alloc.trainees.extend(adjs)

        changed = 0
        for d_id, debate in debates.items():
            existing = debate.adjudicators
            revised = allocations[d_id]
            if existing != revised:
                changed += 1
                logger.info("Saving adjudicators for debate %s", debate)
                logger.info("%s --> %s", existing, revised)
                try:
                    revised.save()
                except IntegrityError:
                    return HttpResponseBadRequest("An adjudicator was allocated to the same "
                        "to the same debate multiple times. Please remove them and re-save.")

        if not changed:
            logger.info("Didn't save any adjudicator allocations, nothing changed.")
            return HttpResponse("There aren't any changes to save.")

        ActionLogEntry.objects.log(type=ActionLogEntry.ACTION_TYPE_ADJUDICATORS_SAVE,
                                   user=request.user, round=self.get_round(),
                                   tournament=self.get_tournament(),
                                   content_object=self.get_round())

        return HttpResponse("Saved changes for {} debates!".format(changed))


def _json_adj_allocation(debates, unused_adj):

    obj = {}

    def _adj(a):

        if a.institution.region:
            region_name = "region-%s" % a.institution.region.id
        else:
            region_name = ""

        return {
            'id': a.id,
            'name': a.name,
            'institution': a.institution.short_code,
            'is_unaccredited': a.is_unaccredited,
            'gender': a.gender,
            'region': region_name
        }

    def _debate(d):
        r = {}
        if d.adjudicators.chair:
            r['chair'] = _adj(d.adjudicators.chair)
        r['panel'] = [_adj(a) for a in d.adjudicators.panel]
        r['trainees'] = [_adj(a) for a in d.adjudicators.trainees]
        return r

    obj['debates'] = dict((d.id, _debate(d)) for d in debates)
    obj['unused'] = [_adj(a) for a in unused_adj]

    return HttpResponse(json.dumps(obj))


@admin_required
@round_view
def draw_adjudicators_get(request, round):
    draw = round.get_draw()

    return _json_adj_allocation(draw, round.unused_adjudicators())


@admin_required
@round_view
def adj_conflicts(request, round):

    data = {
        'personal': {},
        'history': {},
        'institutional': {},
        'adjudicator': {},
    }

    def add(type, adj_id, target_id):
        if adj_id not in data[type]:
            data[type][adj_id] = []
        data[type][adj_id].append(target_id)

    for ac in AdjudicatorConflict.objects.all():
        add('personal', ac.adjudicator_id, ac.team_id)

    for ic in AdjudicatorInstitutionConflict.objects.all():
        for team in Team.objects.filter(institution=ic.institution):
            add('institutional', ic.adjudicator_id, team.id)

    for ac in AdjudicatorAdjudicatorConflict.objects.all():
        add('adjudicator', ac.adjudicator_id, ac.conflict_adjudicator.id)

    history = DebateAdjudicator.objects.filter(
        debate__round__seq__lt=round.seq,
    )

    for da in history:
        try:
            add('history', da.adjudicator_id, da.debate.aff_team.id)
        except DebateTeam.DoesNotExist:
            pass  # For when a Debate/DebateTeam may have been deleted
        try:
            add('history', da.adjudicator_id, da.debate.neg_team.id)
        except DebateTeam.DoesNotExist:
            pass  # For when a Debate/DebateTeam may have been deleted

    return HttpResponse(json.dumps(data), content_type="text/json")


# ==============================================================================
# New UI
# ==============================================================================

class EditAdjudicatorAllocationView(RoundMixin, SuperuserRequiredMixin, TemplateView):

    template_name = 'edit_adj_allocation.html'

    def get_context_data(self, **kwargs):
        t = self.get_tournament()
        r = self.get_round()

        draw = r.debate_set_with_prefetches(ordering=('room_rank',), speakers=False, divisions=False)

        teams = Team.objects.filter(debateteam__debate__round=r).prefetch_related('speaker_set')
        adjs = get_adjs(self.get_round())

        regions = regions_ordered(t)
        categories = categories_ordered(t)
        adjs, teams = populate_conflicts(adjs, teams)
        adjs, teams = populate_histories(adjs, teams, t, r)

        kwargs['allRegions'] = json.dumps(regions)
        kwargs['allCategories'] = json.dumps(categories)
        kwargs['allDebates'] = debates_to_json(draw, t, r)
        kwargs['allTeams'] = teams_to_json(teams, regions, categories, t, r)
        kwargs['allAdjudicators'] = adjs_to_json(adjs, regions, t)

        return super().get_context_data(**kwargs)


class CreateAutoAllocation(LogActionMixin, RoundMixin, SuperuserRequiredMixin, JsonDataResponseView):

    action_log_type = ActionLogEntry.ACTION_TYPE_ADJUDICATORS_AUTO

    def get_data(self):
        round = self.get_round()
        allocate_adjudicators(round, HungarianAllocator)
        return debates_to_json(round.debate_set_with_prefetches(), self.get_tournament(), round)

    def get(self, request, *args, **kwargs):
        round = self.get_round()
        if round.draw_status == Round.STATUS_RELEASED:
            return HttpResponseBadRequest("Draw is already released, unrelease draw to redo auto-allocations.")
        if round.draw_status != Round.STATUS_CONFIRMED:
            return HttpResponseBadRequest("Draw is not confirmed, confirm draw to run auto-allocations.")
        self.log_action()
        return super().get(request, *args, **kwargs)


class SaveDebateInfo(SuperuserRequiredMixin, RoundMixin, LogActionMixin, View):
    pass


class SaveDebateImportance(SaveDebateInfo):
    action_log_type = ActionLogEntry.ACTION_TYPE_DEBATE_IMPORTANCE_EDIT

    def post(self, request, *args, **kwargs):
        debate_id = request.POST.get('debate_id')
        debate_importance = request.POST.get('importance')

        debate = Debate.objects.get(pk=debate_id)
        debate.importance = debate_importance
        debate.save()

        return HttpResponse()


class SaveDebatePanel(SaveDebateInfo):
    action_log_type = ActionLogEntry.ACTION_TYPE_ADJUDICATORS_SAVE

    def post(self, request, *args, **kwargs):
        debate_id = request.POST.get('debate_id')
        debate_panel = json.loads(request.POST.get('panel'))

        to_delete = DebateAdjudicator.objects.filter(debate_id=debate_id).exclude(
                adjudicator_id__in=[da['id'] for da in debate_panel])
        for debateadj in to_delete:
            logger.info("deleted %s" % debateadj)
        to_delete.delete()

        for da in debate_panel:
            debateadj, created = DebateAdjudicator.objects.update_or_create(debate_id=debate_id,
                    adjudicator_id=da['id'], defaults={'type': da['position']})
            logger.info("%s %s" % ("created" if created else "updated", debateadj))

        return HttpResponse()
