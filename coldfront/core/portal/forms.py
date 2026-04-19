from django import forms
from .models import AccountOnboardingRequest
from coldfront.core.project.models import Project
from coldfront.plugins.ldap_groups.utils import ROLE_GROUPS_MAP

EXPIRATION_REQUIRED_ROLES = {
    AccountOnboardingRequest.ROLE_PHD,
    AccountOnboardingRequest.ROLE_RESEARCH_GRANT,
    AccountOnboardingRequest.ROLE_RESEARCH_CONTRACT,
    AccountOnboardingRequest.ROLE_RESEARCH_ASSIGNMENT,
    AccountOnboardingRequest.ROLE_POSTDOC_ASSIGNMENT,
    AccountOnboardingRequest.ROLE_COLLAB_ASSIGNMENT,
    AccountOnboardingRequest.ROLE_RTD_A,
    AccountOnboardingRequest.ROLE_RTD_B,
    AccountOnboardingRequest.ROLE_RTT,
    AccountOnboardingRequest.ROLE_ASSOCIATE_PROF,
    AccountOnboardingRequest.ROLE_FULL_PROF,
}

class OnboardingRequestSearchForm(forms.Form):
    username = forms.CharField(max_length=150, required=False)
    name = forms.CharField(max_length=150, required=False, help_text='Search given name or surname')
    email = forms.CharField(max_length=254, required=False)
    role = forms.ChoiceField(
        choices=[('', 'Any')] + AccountOnboardingRequest.ROLE_CHOICES,
        required=False,
    )
    status = forms.ChoiceField(
        choices=[('', 'Any')] + AccountOnboardingRequest.STATUS_CHOICES,
        required=False,
        initial=AccountOnboardingRequest.STATUS_PENDING,
    )


class AccountRenewalRequestSearchForm(forms.Form):
    username = forms.CharField(max_length=150, required=False)
    status = forms.ChoiceField(
        choices=[('', 'Any')] + AccountOnboardingRequest.STATUS_CHOICES,
        required=False,
        initial=AccountOnboardingRequest.STATUS_PENDING,
    )


class CourseEnrollmentRequestSearchForm(forms.Form):
    username = forms.CharField(max_length=150, required=False)
    status = forms.ChoiceField(
        choices=[('', 'Any')] + AccountOnboardingRequest.STATUS_CHOICES,
        required=False,
        initial=AccountOnboardingRequest.STATUS_PENDING,
    )


class OnboardingProcessForm(forms.ModelForm):
    # Extra (non-model) fields used for external onboarding path
    given_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    surname = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={'class': 'form-control'}))
    codice_fiscale = forms.CharField(max_length=16, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))

    class Meta:
        model = AccountOnboardingRequest
        fields = ['role', 'expiration_date', 'identity_document', 'codice_fiscale']
        widgets = {
            'expiration_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        kwargs.pop('course_queryset', None)  # accepted but ignored for backwards compat
        super().__init__(*args, **kwargs)
        self.fields['codice_fiscale'].required = False
        self.fields['codice_fiscale'].label = "Codice Fiscale"
        self.fields['codice_fiscale'].help_text = "Enter your Codice Fiscale (required for external users)."

    def clean(self):
        data = super().clean()
        role = data.get('role')
        exp = data.get('expiration_date')
        identity_document = data.get('identity_document')
        external = bool(self.data.get('is_external'))
        if external:
            for fname in ['given_name', 'surname', 'email', 'codice_fiscale']:
                if not data.get(fname):
                    self.add_error(fname, 'This field is required.')
        if role in EXPIRATION_REQUIRED_ROLES and not exp:
            self.add_error('expiration_date', 'Expiration date required for this role.')
        if role not in EXPIRATION_REQUIRED_ROLES:
            data['expiration_date'] = None
        if external and not identity_document:
            self.add_error('identity_document', 'Identity document required for external users.')
        if external and data.get('email'):
            if AccountOnboardingRequest.objects.filter(email__iexact=data['email'], status=AccountOnboardingRequest.STATUS_PENDING, unimore_id__isnull=True).exists():
                self.add_error('email', 'There is already a pending request with this email.')
        if external and data.get('codice_fiscale'):
            if AccountOnboardingRequest.objects.filter(codice_fiscale__iexact=data['codice_fiscale'], status=AccountOnboardingRequest.STATUS_PENDING, unimore_id__isnull=True).exists():
                self.add_error('codice_fiscale', 'There is already a pending request with this Codice Fiscale.')


class OnboardingApproveForm(forms.Form):
    role = forms.ChoiceField(
        choices=AccountOnboardingRequest.ROLE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-control'}),
    )
    expiration_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
    )

    def clean(self):
        data = super().clean()
        role = data.get('role')
        exp = data.get('expiration_date')
        if role in EXPIRATION_REQUIRED_ROLES and not exp:
            self.add_error('expiration_date', 'Expiration date required for this role.')
        if role not in EXPIRATION_REQUIRED_ROLES:
            data['expiration_date'] = None
        return data


class CourseEnrollmentRequestForm(forms.Form):
    project = forms.ModelChoiceField(
        queryset=Project.objects.none(),
        label='Course project',
        empty_label='— select a course —',
        widget=forms.Select(attrs={'class': 'form-control'}),
    )
    motivation = forms.CharField(
        required=False,
        label='Motivation',
        widget=forms.Textarea(attrs={
            'class': 'form-control', 'rows': 3,
            'placeholder': 'Optional: why are you requesting enrollment in this course?',
        }),
    )

    def __init__(self, *args, available_projects=None, **kwargs):
        super().__init__(*args, **kwargs)
        if available_projects is not None:
            self.fields['project'].queryset = available_projects
        else:
            self.fields['project'].queryset = Project.objects.filter(project_type__code='F').order_by('title')


class AccountRenewalRequestForm(forms.Form):
    requested_expiration_date = forms.DateField(
        label='Requested expiration date',
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
    )
    role_changed = forms.BooleanField(
        required=False,
        label='My role has changed',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
    new_role = forms.ChoiceField(
        choices=[('', '— select new role —')] + AccountOnboardingRequest.ROLE_CHOICES,
        required=False,
        label='New role',
        widget=forms.Select(attrs={'class': 'form-control'}),
    )
    proof_document = forms.FileField(
        required=False,
        label='Proof of new role',
        help_text='Required when role has changed (e.g. contract, appointment letter).',
        widget=forms.FileInput(attrs={'class': 'form-control-file'}),
    )
    notes = forms.CharField(
        required=False,
        label='Notes',
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3,
                                     'placeholder': 'Any additional information for the administrators…'}),
    )

    def clean(self):
        data = super().clean()
        if data.get('role_changed'):
            if not data.get('new_role'):
                self.add_error('new_role', 'Please select your new role.')
            if not data.get('proof_document'):
                self.add_error('proof_document', 'A proof document is required when the role has changed.')
        return data


class AccountRenewalApproveForm(forms.Form):
    role = forms.ChoiceField(
        choices=AccountOnboardingRequest.ROLE_CHOICES,
        label='Role to assign',
        widget=forms.Select(attrs={'class': 'form-control'}),
    )
    expiration_date = forms.DateField(
        label='Expiration date to set',
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
    )


_LDAP_EDIT_ROLE_CHOICES = AccountOnboardingRequest.ROLE_CHOICES + [('Deactivated user', 'Deactivated user')]


class LdapUserSearchForm(forms.Form):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter username'}),
    )


class LdapUserEditForm(forms.Form):
    role = forms.ChoiceField(
        choices=_LDAP_EDIT_ROLE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-control'}),
    )
    expiration_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={'class': 'form-control'}),
    )
    mobile = forms.CharField(
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. +39 333 1234567'}),
    )
    is_unimore = forms.BooleanField(
        required=False,
        label='UNIMORE account (SASL authentication)',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
    unimore_id = forms.CharField(
        max_length=150,
        required=False,
        label='UNIMORE ID',
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. lobaraldi'}),
        help_text='Required when UNIMORE account is checked.',
    )

    def clean(self):
        data = super().clean()
        if data.get('is_unimore') and not (data.get('unimore_id') or '').strip():
            self.add_error('unimore_id', 'UNIMORE ID is required when UNIMORE account is checked.')
        return data