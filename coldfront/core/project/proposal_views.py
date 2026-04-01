# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView
from django.views.generic.edit import CreateView, FormView

from coldfront.core.project.forms import (
    NominateReviewerForm,
    ProjectProposalForm,
    ProposalChangePIForm,
    ProposalChangeTeamForm,
    ProposalDecisionForm,
    ProposalMetaReviewForm,
    ReviewForm,
)
from coldfront.core.allocation.models import (
    Allocation,
    AllocationAttribute,
    AllocationAttributeType,
    AllocationStatusChoice,
    AllocationUser,
    AllocationUserStatusChoice,
)
from coldfront.core.project.models import (
    Project,
    ProjectProposal,
    ProjectStatusChoice,
    ProjectType,
    ProjectUser,
    ProjectUserRoleChoice,
    ProjectUserStatusChoice,
    ProposalReview,
)
from coldfront.core.resource.models import Resource
from coldfront.core.utils.common import get_domain_url, import_from_settings

EMAIL_ENABLED = import_from_settings('EMAIL_ENABLED', False)
EMAIL_SENDER = import_from_settings('EMAIL_SENDER', '') if EMAIL_ENABLED else ''
EMAIL_ADMIN_LIST = import_from_settings('EMAIL_ADMIN_LIST', [])
CENTER_HELP_URL = import_from_settings('CENTER_HELP_URL', '')


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------

def _notify_admins(subject, message):
    if not EMAIL_ENABLED or not EMAIL_ADMIN_LIST:
        return
    send_mail(subject, message, EMAIL_SENDER, EMAIL_ADMIN_LIST, fail_silently=True)


def _notify_proposer_and_pi(proposal, subject, message):
    """Send an email to the applicant and the PI (deduplicated)."""
    if not EMAIL_ENABLED:
        return
    recipients = {proposal.applicant.email} if proposal.applicant.email else set()
    pi = proposal.pi or proposal.applicant
    if pi.email:
        recipients.add(pi.email)
    if recipients:
        send_mail(subject, message, EMAIL_SENDER, list(recipients), fail_silently=True)


def _send_invitation_email(request, review):
    if not EMAIL_ENABLED:
        return
    if not review.reviewer.email:
        return
    reviewer = review.reviewer
    proposal = review.proposal
    domain_url = get_domain_url(request)
    invitation_url = domain_url + reverse('review-invitation', kwargs={'token': review.token})
    subject = f'Invitation to review project proposal: {proposal.title}'
    message = (
        f'Dear {reviewer.get_full_name() or reviewer.username},\n\n'
        f'You have been invited to provide a {review.get_review_type_display().lower()} review '
        f'for the following project proposal:\n\n'
        f'  Title    : {proposal.title}\n'
        f'  Type     : [{proposal.project_type.code}] {proposal.project_type.name}\n'
        f'  Applicant: {proposal.applicant.get_full_name() or proposal.applicant.username}\n\n'
        f'Please follow the link below to accept or decline the invitation:\n'
        f'{invitation_url}\n\n'
        f'Thank you,\nThe HPC Allocation Committee'
    )
    send_mail(subject, message, EMAIL_SENDER, [reviewer.email], fail_silently=True)


# ---------------------------------------------------------------------------
# Applicant views
# ---------------------------------------------------------------------------

class ProposalTypeSelectView(LoginRequiredMixin, TemplateView):
    """Step 1: display all active project types graphically so the user can pick one."""
    template_name = 'project/proposal_type_select.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project_types'] = ProjectType.objects.filter(active=True).order_by('code')
        return context


class ProposalCreateView(LoginRequiredMixin, CreateView):
    """Step 2: fill in the proposal details for the chosen project type."""
    model = ProjectProposal
    form_class = ProjectProposalForm
    template_name = 'project/proposal_form.html'

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.project_type = get_object_or_404(ProjectType, code=kwargs['type_code'], active=True)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['project_type'] = self.project_type
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project_type'] = self.project_type
        return context

    def form_valid(self, form):
        proposal = form.save(commit=False)
        proposal.applicant = self.request.user
        proposal.project_type = self.project_type
        proposal.save()
        form.save_m2m()

        domain_url = get_domain_url(self.request)
        detail_url = domain_url + reverse('proposal-detail', kwargs={'pk': proposal.pk})
        admin_url = domain_url + reverse('proposal-admin-detail', kwargs={'pk': proposal.pk})

        # Notify admins
        _notify_admins(
            f'New project proposal submitted: {proposal.title}',
            f'A new project proposal has been submitted and is awaiting reviewer nomination.\n\n'
            f'  Title    : {proposal.title}\n'
            f'  Type     : [{proposal.project_type.code}] {proposal.project_type.name}\n'
            f'  Applicant: {proposal.applicant.get_full_name() or proposal.applicant.username}\n\n'
            f'Review it here: {admin_url}',
        )

        # Notify proposer and PI
        pi = proposal.pi or proposal.applicant
        pi_name = pi.get_full_name() or pi.username
        _notify_proposer_and_pi(
            proposal,
            f'Your project proposal has been received: {proposal.title}',
            f'Dear {pi_name},\n\n'
            f'Your project proposal has been successfully submitted and is now under evaluation.\n\n'
            f'  Title   : {proposal.title}\n'
            f'  Type    : [{proposal.project_type.code}] {proposal.project_type.name}\n\n'
            f'You will be notified when a decision is made.\n\n'
            f'You can follow the status of your proposal here:\n{detail_url}\n\n'
            f'Thank you,\nThe HPC Allocation Committee',
        )

        return redirect(reverse('proposal-submitted', kwargs={'pk': proposal.pk}))

    def get_success_url(self):
        return reverse('proposal-list')


class ProposalSubmittedView(LoginRequiredMixin, DetailView):
    """Confirmation page shown after a proposal is submitted."""
    model = ProjectProposal
    template_name = 'project/proposal_submitted.html'

    def get_queryset(self):
        return ProjectProposal.objects.filter(applicant=self.request.user)


class ProposalListView(LoginRequiredMixin, ListView):
    """List proposals where the current user is the applicant, PI, or a team member."""
    model = ProjectProposal
    template_name = 'project/proposal_list.html'
    context_object_name = 'proposals'

    def get_queryset(self):
        user = self.request.user
        return ProjectProposal.objects.filter(
            Q(applicant=user) | Q(pi=user) | Q(team_members=user)
        ).distinct().order_by('-created')


class ProposalStakeholderDetailView(LoginRequiredMixin, DetailView):
    """Proposal detail for the proposer, PI, and team members.

    Shows the meta-review and individual reviews only after a decision has been taken.
    """
    model = ProjectProposal
    template_name = 'project/proposal_stakeholder_detail.html'

    def get_queryset(self):
        user = self.request.user
        return ProjectProposal.objects.filter(
            Q(applicant=user) | Q(pi=user) | Q(team_members=user)
        ).distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        decided = self.object.status in (
            ProjectProposal.STATUS_APPROVED, ProjectProposal.STATUS_REJECTED
        )
        context['decided'] = decided
        if decided:
            context['reviews'] = (
                self.object.reviews
                .filter(status=ProposalReview.STATUS_COMPLETED)
                .select_related('reviewer')
                .order_by('review_type', 'created')
            )
        return context


# ---------------------------------------------------------------------------
# Admin views
# ---------------------------------------------------------------------------

class _StaffMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_staff or self.request.user.is_superuser


class ProposalAdminListView(_StaffMixin, ListView):
    """Admin: list all proposals, filterable by status."""
    model = ProjectProposal
    template_name = 'project/proposal_admin_list.html'
    context_object_name = 'proposals'

    def get_queryset(self):
        qs = ProjectProposal.objects.select_related(
            'applicant', 'project_type'
        ).prefetch_related('reviews')
        status = self.request.GET.get('status')
        if status:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status_choices'] = ProjectProposal.STATUS_CHOICES
        context['current_status'] = self.request.GET.get('status', '')
        return context


class ProposalAdminDetailView(_StaffMixin, DetailView):
    """Admin: full proposal detail with all reviews."""
    model = ProjectProposal
    template_name = 'project/proposal_admin_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['reviews'] = (
            self.object.reviews.select_related('reviewer').order_by('review_type', 'created')
        )
        completed = self.object.reviews.filter(status=ProposalReview.STATUS_COMPLETED)
        context['avg_score'] = (
            round(sum(r.score for r in completed) / completed.count(), 1)
            if completed.exists() else None
        )
        try:
            context['provisioned_project'] = self.object.provisioned_project
        except Project.DoesNotExist:
            context['provisioned_project'] = None
        return context


class ProposalNominateView(_StaffMixin, FormView):
    """Admin: nominate one reviewer (scientific or technical) for a proposal."""
    template_name = 'project/proposal_nominate.html'
    form_class = NominateReviewerForm

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.proposal = get_object_or_404(ProjectProposal, pk=kwargs['pk'])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['proposal'] = self.proposal
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['proposal'] = self.proposal
        context['existing_reviews'] = (
            self.proposal.reviews.select_related('reviewer').order_by('review_type', 'created')
        )
        return context

    def form_valid(self, form):
        reviewer = form.cleaned_data['reviewer']
        review_type = form.cleaned_data['review_type']
        review = ProposalReview.objects.create(
            proposal=self.proposal,
            reviewer=reviewer,
            review_type=review_type,
        )

        # Move proposal to under_review on first nomination
        if self.proposal.status == ProjectProposal.STATUS_PENDING:
            self.proposal.status = ProjectProposal.STATUS_UNDER_REVIEW
            self.proposal.save(update_fields=['status'])

        _send_invitation_email(self.request, review)

        name = reviewer.get_full_name() or reviewer.username
        messages.success(
            self.request,
            f'{name} has been invited as {review.get_review_type_display().lower()} reviewer.',
        )
        return redirect(reverse('proposal-nominate', kwargs={'pk': self.proposal.pk}))


class ProposalMetaReviewView(_StaffMixin, View):
    """Admin: write or edit the committee meta-review (required before taking a decision)."""
    template_name = 'project/proposal_meta_review.html'

    def get(self, request, pk):
        proposal = get_object_or_404(ProjectProposal, pk=pk)
        form = ProposalMetaReviewForm(instance=proposal)
        return render(request, self.template_name, {'proposal': proposal, 'form': form})

    def post(self, request, pk):
        proposal = get_object_or_404(ProjectProposal, pk=pk)
        form = ProposalMetaReviewForm(request.POST, instance=proposal)
        if form.is_valid():
            form.save()
            messages.success(request, 'Meta-review saved.')
            return redirect(reverse('proposal-admin-detail', kwargs={'pk': proposal.pk}))
        return render(request, self.template_name, {'proposal': proposal, 'form': form})


class ProposalDecisionView(_StaffMixin, View):
    """Admin: take the final approve/reject decision (requires a meta-review to exist)."""
    template_name = 'project/proposal_decision.html'

    def _get_context(self, proposal, form):
        reviews = proposal.reviews.select_related('reviewer').order_by('review_type', 'created')
        completed = reviews.filter(status=ProposalReview.STATUS_COMPLETED)
        avg_score = (
            round(sum(r.score for r in completed) / completed.count(), 1)
            if completed.exists() else None
        )
        return {
            'proposal': proposal,
            'reviews': reviews,
            'avg_score': avg_score,
            'form': form,
            'has_meta_review': bool(proposal.meta_review.strip()),
        }

    def get(self, request, pk):
        proposal = get_object_or_404(ProjectProposal, pk=pk)
        form = ProposalDecisionForm(initial={
            'admin_notes': proposal.admin_notes,
            'project_code': proposal.project_code,
            'approved_gpu_hours': proposal.approved_gpu_hours,
            'approved_storage_gb': proposal.approved_storage_gb,
        })
        return render(request, self.template_name, self._get_context(proposal, form))

    def post(self, request, pk):
        proposal = get_object_or_404(ProjectProposal, pk=pk)
        if not proposal.meta_review.strip():
            messages.error(request, 'A meta-review must be written before taking a decision.')
            form = ProposalDecisionForm(request.POST)
            return render(request, self.template_name, self._get_context(proposal, form))
        form = ProposalDecisionForm(request.POST)
        if form.is_valid():
            proposal.status = form.cleaned_data['decision']
            proposal.admin_notes = form.cleaned_data.get('admin_notes', '')
            proposal.project_code = form.cleaned_data.get('project_code', '').strip()
            approved_gpu = form.cleaned_data.get('approved_gpu_hours')
            approved_storage = form.cleaned_data.get('approved_storage_gb')
            proposal.approved_gpu_hours = approved_gpu
            proposal.approved_storage_gb = approved_storage
            proposal.save(update_fields=[
                'status', 'admin_notes', 'project_code',
                'approved_gpu_hours', 'approved_storage_gb',
            ])

            # Notify proposer and PI
            domain_url = get_domain_url(request)
            detail_url = domain_url + reverse('proposal-detail', kwargs={'pk': proposal.pk})
            decision_label = proposal.get_status_display()
            pi = proposal.pi or proposal.applicant
            pi_name = pi.get_full_name() or pi.username
            if proposal.status == ProjectProposal.STATUS_APPROVED:
                next_steps = (
                    f'Your project will be provisioned shortly. Once ready, you will be able to '
                    f'submit jobs under the assigned project account. You can monitor the status '
                    f'of your allocation on the portal:\n'
                    f'{domain_url}/'
                )
            else:
                helpdesk = CENTER_HELP_URL or f'{domain_url}/'
                next_steps = (
                    f'If you have questions about this decision or would like to discuss it '
                    f'further, please open a ticket at:\n'
                    f'{helpdesk}'
                )
            _notify_proposer_and_pi(
                proposal,
                f'Decision on your project proposal: {proposal.title}',
                f'Dear {pi_name},\n\n'
                f'A decision has been made regarding the following project proposal:\n\n'
                f'  Title   : {proposal.title}\n'
                f'  Type    : [{proposal.project_type.code}] {proposal.project_type.name}\n'
                f'  Decision: {decision_label}\n\n'
                f'{next_steps}\n\n'
                f'The full reviews and meta-review from the committee are available here:\n'
                f'{detail_url}\n\n'
                f'The HPC Allocation Committee',
            )

            messages.success(
                request,
                f'Proposal "{proposal.title}" marked as {proposal.get_status_display()}.',
            )
            return redirect(reverse('proposal-admin-detail', kwargs={'pk': proposal.pk}))
        return render(request, self.template_name, self._get_context(proposal, form))


class ProposalChangePIView(_StaffMixin, View):
    """Admin: change (or clear) the PI on a proposal."""
    template_name = 'project/proposal_change_pi.html'

    def get(self, request, pk):
        proposal = get_object_or_404(ProjectProposal, pk=pk)
        form = ProposalChangePIForm(initial={'pi': proposal.pi})
        return render(request, self.template_name, {'proposal': proposal, 'form': form})

    def post(self, request, pk):
        proposal = get_object_or_404(ProjectProposal, pk=pk)
        form = ProposalChangePIForm(request.POST)
        if form.is_valid():
            proposal.pi = form.cleaned_data['pi']
            proposal.save(update_fields=['pi'])
            pi_display = (
                proposal.pi.get_full_name() or proposal.pi.username
                if proposal.pi else 'applicant'
            )
            messages.success(request, f'PI updated to {pi_display}.')
            return redirect(reverse('proposal-admin-detail', kwargs={'pk': proposal.pk}))
        return render(request, self.template_name, {'proposal': proposal, 'form': form})


class ProposalChangeTeamView(_StaffMixin, View):
    """Admin: inspect and change the research team on a proposal."""
    template_name = 'project/proposal_change_team.html'

    def get(self, request, pk):
        proposal = get_object_or_404(ProjectProposal, pk=pk)
        form = ProposalChangeTeamForm(initial={'team_members': proposal.team_members.all()})
        return render(request, self.template_name, {'proposal': proposal, 'form': form})

    def post(self, request, pk):
        proposal = get_object_or_404(ProjectProposal, pk=pk)
        form = ProposalChangeTeamForm(request.POST)
        if form.is_valid():
            proposal.team_members.set(form.cleaned_data['team_members'])
            messages.success(request, 'Research team updated.')
            return redirect(reverse('proposal-admin-detail', kwargs={'pk': proposal.pk}))
        return render(request, self.template_name, {'proposal': proposal, 'form': form})


# ---------------------------------------------------------------------------
# Provisioning view
# ---------------------------------------------------------------------------

class ProposalProvisionView(_StaffMixin, View):
    """Admin: provision a project, its allocations, and its users from an approved proposal."""

    def post(self, request, pk):
        from django.db import transaction
        proposal = get_object_or_404(
            ProjectProposal, pk=pk, status=ProjectProposal.STATUS_APPROVED
        )

        # Guard: already provisioned
        try:
            existing = proposal.provisioned_project
            messages.warning(
                request,
                f'This proposal has already been provisioned as project "{existing.title}".',
            )
            return redirect(reverse('proposal-admin-detail', kwargs={'pk': proposal.pk}))
        except Project.DoesNotExist:
            pass

        if not proposal.project_code.strip():
            messages.error(request, 'Cannot provision: no project code set on the proposal.')
            return redirect(reverse('proposal-admin-detail', kwargs={'pk': proposal.pk}))

        pi = proposal.pi or proposal.applicant
        gpu_hours = proposal.get_effective_gpu_hours()
        storage_gb = proposal.get_effective_storage_gb()

        try:
            slurm_resource = Resource.objects.get(name='SLURM account')
        except Resource.DoesNotExist:
            messages.error(request, 'Resource "SLURM account" not found. Cannot provision.')
            return redirect(reverse('proposal-admin-detail', kwargs={'pk': proposal.pk}))
        try:
            work_resource = Resource.objects.get(name='Storage in WORK area')
        except Resource.DoesNotExist:
            messages.error(request, 'Resource "Storage in WORK area" not found. Cannot provision.')
            return redirect(reverse('proposal-admin-detail', kwargs={'pk': proposal.pk}))

        active_project_status = ProjectStatusChoice.objects.get(name='Active')
        active_alloc_status = AllocationStatusChoice.objects.get(name='Active')
        active_proj_user_status = ProjectUserStatusChoice.objects.get(name='Active')
        active_alloc_user_status = AllocationUserStatusChoice.objects.get(name='Active')
        manager_role = ProjectUserRoleChoice.objects.get(name='Manager')
        user_role = ProjectUserRoleChoice.objects.get(name='User')

        slurm_account_attr = AllocationAttributeType.objects.get(name='slurm_account_name')
        slurm_budget_attr = AllocationAttributeType.objects.get(name='slurm_budget')
        storage_quota_attr = AllocationAttributeType.objects.get(name='Storage Quota (GB)')
        storage_group_attr = AllocationAttributeType.objects.get(name='Storage_Group_Name')

        with transaction.atomic():
            # --- Project ---
            project = Project.objects.create(
                title=proposal.project_code,
                pi=pi,
                description=proposal.description,
                field_of_science=proposal.field_of_science,
                status=active_project_status,
                project_type=proposal.project_type,
                requires_review=False,
                proposal=proposal,
            )

            # --- Project users ---
            ProjectUser.objects.create(
                user=pi,
                project=project,
                role=manager_role,
                status=active_proj_user_status,
            )
            team_members = list(proposal.team_members.exclude(pk=pi.pk))
            for member in team_members:
                ProjectUser.objects.create(
                    user=member,
                    project=project,
                    role=user_role,
                    status=active_proj_user_status,
                )

            all_users = [pi] + team_members

            # --- SLURM allocation ---
            slurm_alloc = Allocation.objects.create(
                project=project,
                status=active_alloc_status,
                start_date=proposal.start_date,
                end_date=proposal.end_date,
                is_changeable=True,
                justification=f'Provisioned from proposal #{proposal.pk}.',
            )
            slurm_alloc.resources.add(slurm_resource)
            AllocationAttribute.objects.create(
                allocation_attribute_type=slurm_account_attr,
                allocation=slurm_alloc,
                value=proposal.project_code,
            )
            AllocationAttribute.objects.create(
                allocation_attribute_type=slurm_budget_attr,
                allocation=slurm_alloc,
                value=str(gpu_hours),
            )
            for user in all_users:
                AllocationUser.objects.create(
                    allocation=slurm_alloc,
                    user=user,
                    status=active_alloc_user_status,
                )

            # --- WORK storage allocation ---
            work_alloc = Allocation.objects.create(
                project=project,
                status=active_alloc_status,
                start_date=proposal.start_date,
                end_date=proposal.end_date,
                is_changeable=True,
                justification=f'Provisioned from proposal #{proposal.pk}.',
            )
            work_alloc.resources.add(work_resource)
            AllocationAttribute.objects.create(
                allocation_attribute_type=storage_quota_attr,
                allocation=work_alloc,
                value=str(storage_gb),
            )
            AllocationAttribute.objects.create(
                allocation_attribute_type=storage_group_attr,
                allocation=work_alloc,
                value=proposal.project_code,
            )
            for user in all_users:
                AllocationUser.objects.create(
                    allocation=work_alloc,
                    user=user,
                    status=active_alloc_user_status,
                )

        messages.success(
            request,
            f'Project "{project.title}" provisioned successfully '
            f'with SLURM account and WORK storage allocations.',
        )
        return redirect(reverse('project-detail', kwargs={'pk': project.pk}))


# ---------------------------------------------------------------------------
# Reviewer views
# ---------------------------------------------------------------------------

class ReviewInvitationView(LoginRequiredMixin, View):
    """Reviewer: accept or decline an invitation. Accessible only to the invited reviewer."""
    template_name = 'project/review_invitation.html'

    def _get_review(self, token):
        review = get_object_or_404(ProposalReview, token=token)
        if review.reviewer != self.request.user:
            raise PermissionDenied
        return review

    def get(self, request, token):
        review = self._get_review(token)
        return render(request, self.template_name, {'review': review})

    def post(self, request, token):
        review = self._get_review(token)
        if review.status != ProposalReview.STATUS_INVITED:
            messages.warning(request, 'This invitation has already been processed.')
            return redirect(reverse('review-invitation', kwargs={'token': token}))

        action = request.POST.get('action')
        if action == 'accept':
            review.status = ProposalReview.STATUS_ACCEPTED
            review.save(update_fields=['status', 'modified'])
            _notify_admins(
                f'Review invitation accepted: {review.proposal.title}',
                f'{review.reviewer.get_full_name() or review.reviewer.username} accepted the '
                f'{review.get_review_type_display().lower()} review invitation for '
                f'"{review.proposal.title}".',
            )
            messages.success(request, 'You have accepted the invitation. Please write your review below.')
            return redirect(reverse('review-write', kwargs={'token': token}))

        elif action == 'decline':
            review.status = ProposalReview.STATUS_DECLINED
            review.save(update_fields=['status', 'modified'])
            _notify_admins(
                f'Review invitation declined: {review.proposal.title}',
                f'{review.reviewer.get_full_name() or review.reviewer.username} declined the '
                f'{review.get_review_type_display().lower()} review invitation for '
                f'"{review.proposal.title}". A replacement reviewer may need to be nominated.',
            )
            messages.info(request, 'You have declined the invitation.')

        return redirect(reverse('review-invitation', kwargs={'token': token}))


class ReviewWriteView(LoginRequiredMixin, View):
    """Reviewer: write and submit a review after accepting the invitation."""
    template_name = 'project/review_write.html'

    def _get_review(self, token):
        review = get_object_or_404(ProposalReview, token=token)
        if review.reviewer != self.request.user:
            raise PermissionDenied
        return review

    def get(self, request, token):
        review = self._get_review(token)
        if review.status == ProposalReview.STATUS_COMPLETED:
            form = ReviewForm(instance=review)
            return render(request, self.template_name, {'review': review, 'form': form, 'readonly': True})
        if review.status != ProposalReview.STATUS_ACCEPTED:
            messages.warning(request, 'You must accept the invitation before writing your review.')
            return redirect(reverse('review-invitation', kwargs={'token': token}))
        return render(request, self.template_name, {'review': review, 'form': ReviewForm(instance=review)})

    def post(self, request, token):
        review = self._get_review(token)
        if review.status != ProposalReview.STATUS_ACCEPTED:
            messages.warning(request, 'You must accept the invitation before submitting a review.')
            return redirect(reverse('review-invitation', kwargs={'token': token}))

        form = ReviewForm(request.POST, instance=review)
        if form.is_valid():
            completed_review = form.save(commit=False)
            completed_review.status = ProposalReview.STATUS_COMPLETED
            completed_review.save()

            # Check if all non-declined reviews are now completed
            proposal = completed_review.proposal
            still_pending = proposal.reviews.exclude(
                status__in=[ProposalReview.STATUS_DECLINED, ProposalReview.STATUS_COMPLETED]
            )
            if still_pending.exists():
                _notify_admins(
                    f'Review submitted: {proposal.title}',
                    f'{completed_review.reviewer.get_full_name() or completed_review.reviewer.username} '
                    f'submitted a {completed_review.get_review_type_display().lower()} review for '
                    f'"{proposal.title}" (score: {completed_review.score}/5).',
                )
            else:
                domain_url = get_domain_url(request)
                decision_url = domain_url + reverse('proposal-decision', kwargs={'pk': proposal.pk})
                _notify_admins(
                    f'All reviews complete — ready for decision: {proposal.title}',
                    f'All reviews for "{proposal.title}" have been submitted.\n\n'
                    f'You can now take the final decision here:\n{decision_url}',
                )

            messages.success(request, 'Your review has been submitted. Thank you.')
            return redirect(reverse('review-write', kwargs={'token': token}))

        return render(request, self.template_name, {'review': review, 'form': form})
