# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView
from django.views.generic.edit import CreateView, FormView

from coldfront.core.project.forms import (
    NominateReviewerForm,
    ProjectProposalForm,
    ProposalDecisionForm,
    ReviewForm,
)
from coldfront.core.project.models import ProjectProposal, ProjectType, ProposalReview
from coldfront.core.utils.common import get_domain_url, import_from_settings

EMAIL_ENABLED = import_from_settings('EMAIL_ENABLED', False)
EMAIL_SENDER = import_from_settings('EMAIL_SENDER', '') if EMAIL_ENABLED else ''
EMAIL_ADMIN_LIST = import_from_settings('EMAIL_ADMIN_LIST', [])


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------

def _notify_admins(subject, message):
    if not EMAIL_ENABLED or not EMAIL_ADMIN_LIST:
        return
    send_mail(subject, message, EMAIL_SENDER, EMAIL_ADMIN_LIST, fail_silently=True)


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

        # Notify admins
        domain_url = get_domain_url(self.request)
        admin_url = domain_url + reverse('proposal-admin-detail', kwargs={'pk': proposal.pk})
        _notify_admins(
            f'New project proposal submitted: {proposal.title}',
            f'A new project proposal has been submitted and is awaiting reviewer nomination.\n\n'
            f'  Title    : {proposal.title}\n'
            f'  Type     : [{proposal.project_type.code}] {proposal.project_type.name}\n'
            f'  Applicant: {proposal.applicant.get_full_name() or proposal.applicant.username}\n\n'
            f'Review it here: {admin_url}',
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
    """List all proposals submitted by the current user."""
    model = ProjectProposal
    template_name = 'project/proposal_list.html'
    context_object_name = 'proposals'

    def get_queryset(self):
        return ProjectProposal.objects.filter(applicant=self.request.user)


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


class ProposalDecisionView(_StaffMixin, View):
    """Admin: take the final approve/reject decision after reviewing all reviews."""
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
        }

    def get(self, request, pk):
        proposal = get_object_or_404(ProjectProposal, pk=pk)
        form = ProposalDecisionForm(initial={'admin_notes': proposal.admin_notes})
        return render(request, self.template_name, self._get_context(proposal, form))

    def post(self, request, pk):
        proposal = get_object_or_404(ProjectProposal, pk=pk)
        form = ProposalDecisionForm(request.POST)
        if form.is_valid():
            proposal.status = form.cleaned_data['decision']
            proposal.admin_notes = form.cleaned_data.get('admin_notes', '')
            proposal.save(update_fields=['status', 'admin_notes'])
            messages.success(
                request,
                f'Proposal "{proposal.title}" marked as {proposal.get_status_display()}.',
            )
            return redirect(reverse('proposal-admin-detail', kwargs={'pk': proposal.pk}))
        return render(request, self.template_name, self._get_context(proposal, form))


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
