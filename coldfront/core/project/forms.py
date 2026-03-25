# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import datetime

from django import forms
from django.contrib.auth.models import User
from django.db.models.functions import Lower
from django.shortcuts import get_object_or_404

from coldfront.core.project.models import Project, ProjectAttribute, ProjectProposal, ProjectReview, ProjectType, ProjectUserRoleChoice, ProposalReview
from coldfront.core.utils.common import import_from_settings

EMAIL_DIRECTOR_PENDING_PROJECT_REVIEW_EMAIL = import_from_settings("EMAIL_DIRECTOR_PENDING_PROJECT_REVIEW_EMAIL")
EMAIL_ADMIN_LIST = import_from_settings("EMAIL_ADMIN_LIST", [])
EMAIL_DIRECTOR_EMAIL_ADDRESS = import_from_settings("EMAIL_DIRECTOR_EMAIL_ADDRESS", "")


class ProjectSearchForm(forms.Form):
    """Search form for the Project list page."""

    LAST_NAME = "Last Name"
    USERNAME = "Username"
    FIELD_OF_SCIENCE = "Field of Science"

    last_name = forms.CharField(label=LAST_NAME, max_length=100, required=False)
    username = forms.CharField(label=USERNAME, max_length=100, required=False)
    field_of_science = forms.CharField(label=FIELD_OF_SCIENCE, max_length=100, required=False)
    show_all_projects = forms.BooleanField(initial=False, required=False)


class ProjectAddUserForm(forms.Form):
    username = forms.CharField(max_length=150, disabled=True)
    first_name = forms.CharField(max_length=150, required=False, disabled=True)
    last_name = forms.CharField(max_length=150, required=False, disabled=True)
    email = forms.EmailField(max_length=100, required=False, disabled=True)
    source = forms.CharField(max_length=16, disabled=True)
    role = forms.ModelChoiceField(queryset=ProjectUserRoleChoice.objects.all(), empty_label=None)
    selected = forms.BooleanField(initial=False, required=False)


class ProjectAddUsersToAllocationForm(forms.Form):
    allocation = forms.MultipleChoiceField(
        widget=forms.CheckboxSelectMultiple(attrs={"checked": "checked"}), required=False
    )

    def __init__(self, request_user, project_pk, *args, **kwargs):
        super().__init__(*args, **kwargs)
        project_obj = get_object_or_404(Project, pk=project_pk)

        allocation_query_set = project_obj.allocation_set.filter(
            resources__is_allocatable=True,
            is_locked=False,
            status__name__in=["Active", "New", "Renewal Requested", "Payment Pending", "Payment Requested", "Paid"],
        )
        allocation_choices = [
            (
                allocation.id,
                "%s (%s) %s"
                % (
                    allocation.get_parent_resource.name,
                    allocation.get_parent_resource.resource_type.name,
                    allocation.description if allocation.description else "",
                ),
            )
            for allocation in allocation_query_set
        ]
        allocation_choices_sorted = []
        allocation_choices_sorted = sorted(allocation_choices, key=lambda x: x[1][0].lower())
        allocation_choices.insert(0, ("__select_all__", "Select All"))
        if allocation_query_set:
            self.fields["allocation"].choices = allocation_choices_sorted
            self.fields["allocation"].help_text = "<br/>Select allocations to add selected users to."
        else:
            self.fields["allocation"].widget = forms.HiddenInput()


class ProjectRemoveUserForm(forms.Form):
    username = forms.CharField(max_length=150, disabled=True)
    first_name = forms.CharField(max_length=150, required=False, disabled=True)
    last_name = forms.CharField(max_length=150, required=False, disabled=True)
    email = forms.EmailField(max_length=100, required=False, disabled=True)
    role = forms.CharField(max_length=30, disabled=True)
    selected = forms.BooleanField(initial=False, required=False)


class ProjectUserUpdateForm(forms.Form):
    role = forms.ModelChoiceField(queryset=ProjectUserRoleChoice.objects.all(), empty_label=None)
    enable_notifications = forms.BooleanField(initial=False, required=False)


class ProjectReviewForm(forms.Form):
    reason = forms.CharField(
        label="Reason for not updating project information",
        widget=forms.Textarea(
            attrs={
                "placeholder": "If you have no new information to provide, you are required to provide a statement explaining this in this box. Thank you!"
            }
        ),
        required=False,
    )
    acknowledgement = forms.BooleanField(
        label="By checking this box I acknowledge that I have updated my project to the best of my knowledge",
        initial=False,
        required=True,
    )

    def __init__(self, project_pk, *args, **kwargs):
        super().__init__(*args, **kwargs)
        project_obj = get_object_or_404(Project, pk=project_pk)
        now = datetime.datetime.now(datetime.timezone.utc)

        if project_obj.grant_set.exists():
            latest_grant = project_obj.grant_set.order_by("-modified")[0]
            grant_updated_in_last_year = (now - latest_grant.created).days < 365
        else:
            grant_updated_in_last_year = None

        if project_obj.publication_set.exists():
            latest_publication = project_obj.publication_set.order_by("-created")[0]
            publication_updated_in_last_year = (now - latest_publication.created).days < 365
        else:
            publication_updated_in_last_year = None

        if grant_updated_in_last_year or publication_updated_in_last_year:
            self.fields["reason"].widget = forms.HiddenInput()
        else:
            self.fields["reason"].required = True


class ProjectReviewEmailForm(forms.Form):
    cc = forms.CharField(required=False)
    email_body = forms.CharField(required=True, widget=forms.Textarea)

    def __init__(self, pk, *args, **kwargs):
        super().__init__(*args, **kwargs)
        project_review_obj = get_object_or_404(ProjectReview, pk=int(pk))
        self.fields["email_body"].initial = "Dear {} {} \n{}".format(
            project_review_obj.project.pi.first_name,
            project_review_obj.project.pi.last_name,
            EMAIL_DIRECTOR_PENDING_PROJECT_REVIEW_EMAIL,
        )
        self.fields["cc"].initial = ", ".join([EMAIL_DIRECTOR_EMAIL_ADDRESS] + EMAIL_ADMIN_LIST)


class ProjectAttributeAddForm(forms.ModelForm):
    class Meta:
        fields = "__all__"
        model = ProjectAttribute
        labels = {
            "proj_attr_type": "Project Attribute Type",
        }

    def __init__(self, *args, **kwargs):
        super(ProjectAttributeAddForm, self).__init__(*args, **kwargs)
        user = (kwargs.get("initial")).get("user")
        self.fields["proj_attr_type"].queryset = self.fields["proj_attr_type"].queryset.order_by(Lower("name"))
        if not user.is_superuser:
            self.fields["proj_attr_type"].queryset = self.fields["proj_attr_type"].queryset.filter(is_private=False)


class ProjectAttributeDeleteForm(forms.Form):
    pk = forms.IntegerField(required=False, disabled=True)
    name = forms.CharField(max_length=150, required=False, disabled=True)
    attr_type = forms.CharField(max_length=150, required=False, disabled=True)
    value = forms.CharField(max_length=150, required=False, disabled=True)
    selected = forms.BooleanField(initial=False, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["pk"].widget = forms.HiddenInput()


# class ProjectAttributeChangeForm(forms.Form):
#     pk = forms.IntegerField(required=False, disabled=True)
#     name = forms.CharField(max_length=150, required=False, disabled=True)
#     value = forms.CharField(max_length=150, required=False, disabled=True)
#     new_value = forms.CharField(max_length=150, required=False, disabled=False)

#     def __init__(self, *args, **kwargs):
#         super().__init__(*args, **kwargs)
#         self.fields['pk'].widget = forms.HiddenInput()

#     def clean(self):
#         cleaned_data = super().clean()

#         if cleaned_data.get('new_value') != "":
#             proj_attr = ProjectAttribute.objects.get(pk=cleaned_data.get('pk'))
#             proj_attr.value = cleaned_data.get('new_value')
#             proj_attr.clean()


class ProjectAttributeUpdateForm(forms.Form):
    pk = forms.IntegerField(required=False, disabled=True)
    new_value = forms.CharField(max_length=150, required=True, disabled=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["pk"].widget = forms.HiddenInput()

    def clean(self):
        cleaned_data = super().clean()

        if cleaned_data.get("new_value") != "":
            proj_attr = ProjectAttribute.objects.get(pk=cleaned_data.get("pk"))
            proj_attr.value = cleaned_data.get("new_value")
            proj_attr.clean()


class ProjectCreationForm(forms.ModelForm):
    project_type = forms.ModelChoiceField(
        queryset=ProjectType.objects.filter(active=True).order_by("code"),
        required=False,
        empty_label="--- Select project type ---",
    )

    class Meta:
        model = Project
        fields = ["title", "description", "project_type", "field_of_science", "description_of_research", "computational_approach", "financed_project_text"]


class ProjectProposalForm(forms.ModelForm):
    """Form for submitting a project proposal. The project_type is fixed by the URL and not editable."""

    class Meta:
        model = ProjectProposal
        fields = [
            "title",
            "description",
            "field_of_science",
            "start_date",
            "end_date",
            "description_of_research",
            "computational_approach",
            "requested_gpu_hours",
            "requested_storage_gb",
            "financed_project_text",
            "funded_project_proof",
        ]
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, project_type=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.project_type = project_type

        if project_type:
            max_gpu = project_type.max_gpu_hours
            self.fields['requested_gpu_hours'].help_text = (
                f'How many GPU hours do you need? Maximum for this project type: {max_gpu:,} GPU hours '
                f'(equivalent to {project_type.annual_budget:,} standard budget hours; 1 std hour = 1/6 GPU hour).'
            )
            self.fields['requested_gpu_hours'].widget.attrs['max'] = max_gpu
            self.fields['requested_gpu_hours'].widget.attrs['min'] = 1

            months = project_type.max_duration_months
            years, rem = divmod(months, 12)
            duration_str = (
                f'{years} year{"s" if years != 1 else ""}' if rem == 0
                else f'{years} year{"s" if years != 1 else ""} and {rem} month{"s" if rem != 1 else ""}' if years
                else f'{months} month{"s" if months != 1 else ""}'
            )
            self.fields['end_date'].help_text = (
                f'Planned project end date. Maximum duration for this type: {duration_str}.'
            )

            if not project_type.requires_funded_project_proof:
                del self.fields['financed_project_text']
                del self.fields['funded_project_proof']
            else:
                self.fields['financed_project_text'].required = True
                self.fields['funded_project_proof'].required = True

        self.fields['requested_storage_gb'].help_text = 'How much WORK storage do you need, in GB?'
        self.fields['requested_storage_gb'].widget.attrs['min'] = 1
        self.fields['field_of_science'].queryset = (
            self.fields['field_of_science'].queryset.filter(is_selectable=True)
        )

    def clean_requested_gpu_hours(self):
        value = self.cleaned_data.get('requested_gpu_hours')
        if self.project_type and value is not None:
            max_gpu = self.project_type.max_gpu_hours
            if value > max_gpu:
                raise forms.ValidationError(
                    f'Cannot exceed {max_gpu:,} GPU hours for this project type.'
                )
            if value < 1:
                raise forms.ValidationError('Must request at least 1 GPU hour.')
        return value

    def clean_requested_storage_gb(self):
        value = self.cleaned_data.get('requested_storage_gb')
        if value is not None and value < 1:
            raise forms.ValidationError('Must request at least 1 GB of storage.')
        return value

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')

        if start_date and end_date:
            if end_date <= start_date:
                self.add_error('end_date', 'End date must be after start date.')
            elif self.project_type:
                # Compute duration in months (approximate: each month = 30.44 days)
                delta_days = (end_date - start_date).days
                delta_months = delta_days / 30.4375
                if delta_months > self.project_type.max_duration_months:
                    max_m = self.project_type.max_duration_months
                    years, rem = divmod(max_m, 12)
                    duration_str = (
                        f'{years} year{"s" if years != 1 else ""}' if rem == 0
                        else f'{years} year{"s" if years != 1 else ""} and {rem} month{"s" if rem != 1 else ""}' if years
                        else f'{max_m} month{"s" if max_m != 1 else ""}'
                    )
                    self.add_error(
                        'end_date',
                        f'Project duration exceeds the maximum of {duration_str} for this project type.',
                    )

        return cleaned_data


class NominateReviewerForm(forms.Form):
    """Form for nominating a single reviewer (scientific or technical) for a proposal."""

    reviewer = forms.ModelChoiceField(
        queryset=User.objects.none(),
        label='Reviewer',
    )
    review_type = forms.ChoiceField(
        choices=ProposalReview.REVIEW_TYPE_CHOICES,
        label='Review type',
    )

    def __init__(self, *args, proposal=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.proposal = proposal
        # Exclude the applicant; the view passes the filtered queryset via proposal
        qs = User.objects.filter(is_active=True).order_by('last_name', 'first_name', 'username')
        self.fields['reviewer'].queryset = qs
        self.fields['reviewer'].label_from_instance = lambda u: (
            f'{u.get_full_name()} ({u.username})' if u.get_full_name().strip() else u.username
        )

    def clean(self):
        cleaned_data = super().clean()
        reviewer = cleaned_data.get('reviewer')
        review_type = cleaned_data.get('review_type')
        if reviewer and review_type and self.proposal:
            if self.proposal.reviews.filter(reviewer=reviewer, review_type=review_type).exists():
                name = reviewer.get_full_name() or reviewer.username
                raise forms.ValidationError(
                    f'{name} is already nominated as {review_type} reviewer for this proposal.'
                )
        return cleaned_data


class ReviewForm(forms.ModelForm):
    """Form for a reviewer to submit their written review and score."""

    score = forms.TypedChoiceField(
        choices=[(i, str(i)) for i in range(1, 6)],
        coerce=int,
        widget=forms.RadioSelect,
        label='Score (1 = lowest, 5 = highest)',
    )

    class Meta:
        model = ProposalReview
        fields = ['score', 'review_text']
        widgets = {
            'review_text': forms.Textarea(attrs={'rows': 8}),
        }
        labels = {
            'review_text': 'Review',
        }

    def clean_review_text(self):
        value = self.cleaned_data.get('review_text', '').strip()
        if not value:
            raise forms.ValidationError('Please write your review before submitting.')
        return value


class ProposalDecisionForm(forms.Form):
    """Form for the admin to take a final approve/reject decision on a proposal."""

    decision = forms.ChoiceField(
        choices=[
            (ProjectProposal.STATUS_APPROVED, 'Approve'),
            (ProjectProposal.STATUS_REJECTED, 'Reject'),
        ],
        widget=forms.RadioSelect,
        label='Decision',
    )
    admin_notes = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 4}),
        required=False,
        label='Admin notes (internal)',
        help_text='For internal use only. Not shown to the applicant.',
    )

