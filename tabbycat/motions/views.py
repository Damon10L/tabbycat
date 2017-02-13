from django.shortcuts import render
from django.contrib import messages
from django.db.models import Q
from django.forms import ModelForm
from django.forms.models import modelformset_factory
from django.forms.widgets import CheckboxSelectMultiple
from django.forms.models import ModelMultipleChoiceField
from django.views.generic.base import TemplateView

from actionlog.mixins import LogActionMixin
from actionlog.models import ActionLogEntry
from divisions.models import Division
from tournaments.mixins import PublicTournamentPageMixin, RoundMixin
from utils.misc import redirect_round
from utils.mixins import ModelFormSetView, PostOnlyRedirectView, SuperuserRequiredMixin
from utils.views import admin_required, round_view

from .models import Motion


class PublicMotionsView(PublicTournamentPageMixin, TemplateView):
    public_page_preference = 'public_motions'

    def using_division_motions(self):
        tournament = self.get_tournament()
        return tournament.pref('enable_divisions') and tournament.pref('enable_division_motions')

    def get_template_names(self):
        if self.using_division_motions():
            return ['public_division_motions.html']
        else:
            return ['public_motions.html']

    def get_context_data(self, **kwargs):
        tournament = self.get_tournament()
        order_by = 'seq' if tournament.pref('public_motions_order') == 'forward' else '-seq'

        # Include rounds whether *either* motions are released *or* it's this
        # round or a previous round. The template checks motion_released again
        # and displays a "not released" message if motions are not released.
        filter_q = Q(motions_released=True)
        if not self.using_division_motions():
            filter_q |= Q(seq__lte=tournament.current_round.seq)

        kwargs['rounds'] = tournament.round_set.filter(filter_q).order_by(
                order_by).prefetch_related('motion_set')
        return super().get_context_data(**kwargs)


class EditMotionsView(SuperuserRequiredMixin, LogActionMixin, RoundMixin, ModelFormSetView):
    # Django doesn't have a class-based view for formsets, so this implements
    # the form processing analogously to FormView, with less decomposition.
    # See also: participants.views.PublicConfirmShiftView.

    template_name = 'edit.html'
    action_log_type = ActionLogEntry.ACTION_TYPE_MOTION_EDIT
    formset_factory_kwargs = dict(can_delete=True, extra=3, exclude=['round'])
    formset_model = Motion

    def get_formset_queryset(self):
        return self.get_round().motion_set.all()

    def formset_valid(self, formset):
        motions = formset.save(commit=False)
        round = self.get_round()
        for motion in motions:
            motion.round = round
            motion.save()
            self.log_action(content_object=motion)
        for motion in formset.deleted_objects:
            motion.delete()
        messages.success(self.request, 'The motions have been saved.')
        return redirect_round('motions-edit', round)


@admin_required
@round_view
def motions_assign(request, round):

    class MyModelChoiceField(ModelMultipleChoiceField):
        def label_from_instance(self, obj):
            return "D%s @ %s" % (
                obj.name,
                obj.venue_group.short_name,
            )

    class ModelAssignForm(ModelForm):
        divisions = MyModelChoiceField(
            widget=CheckboxSelectMultiple,
            queryset=Division.objects.filter(tournament=round.tournament).order_by('venue_group'))

        class Meta:
            model = Motion
            fields = ("divisions",)

    motion_form_set = modelformset_factory(Motion, ModelAssignForm, extra=0, fields=['divisions'])

    if request.method == 'POST':
        formset = motion_form_set(request.POST)
        formset.save()  # Should be checking for validity but on a deadline and was buggy
        if 'submit' in request.POST:
            return redirect_round('draw-display', round)

    formset = motion_form_set(queryset=Motion.objects.filter(round=round))
    return render(request, "assign.html", dict(formset=formset))


class BaseReleaseMotionsView(SuperuserRequiredMixin, LogActionMixin, RoundMixin, PostOnlyRedirectView):

    round_redirect_pattern_name = 'motions-edit'

    def post(self, request, *args, **kwargs):
        round = self.get_round()
        round.motions_released = self.motions_released
        messages.success(request, self.message_text)
        round.save()
        self.log_action()
        return super().post(request, *args, **kwargs)


class ReleaseMotionsView(BaseReleaseMotionsView):

    action_log_type = ActionLogEntry.ACTION_TYPE_MOTIONS_RELEASE
    motions_released = True
    message_text = "Released the motions. They will now show on the public-facing pages of this website."


class UnreleaseMotionsView(BaseReleaseMotionsView):

    action_log_type = ActionLogEntry.ACTION_TYPE_MOTIONS_UNRELEASE
    motions_released = False
    message_text = "Unreleased the motions. They will no longer show on the public-facing pages of this website."


class DisplayMotionsView(SuperuserRequiredMixin, RoundMixin, TemplateView):

    template_name = 'show.html'

    def get_context_data(self, **kwargs):
        kwargs['motions'] = self.get_round().motion_set.all()
        return super().get_context_data(**kwargs)
