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


class OnboardingProcessForm(forms.ModelForm):
    # Extra (non-model) fields used for external onboarding path
    given_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    surname = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={'class': 'form-control'}))
    codice_fiscale = forms.CharField(max_length=16, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    class Meta:
        model = AccountOnboardingRequest
        fields = ['role', 'expiration_date', 'course_project', 'identity_document', 'codice_fiscale']
        widgets = {
            'expiration_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        course_queryset = kwargs.pop('course_queryset', None)
        super().__init__(*args, **kwargs)
        # Limit course choices to teaching-support projects (type 'F')
        if course_queryset is None:
            course_queryset = Project.objects.filter(project_type__code='F').order_by('title')
        self.fields['course_project'].queryset = course_queryset
        self.fields['course_project'].required = False
        self.fields['course_project'].label = "Course"
        self.fields['course_project'].help_text = "Select the course you are attending (required if role is 'Student from a course')."
        self.fields['codice_fiscale'].required = False
        self.fields['codice_fiscale'].label = "Codice Fiscale"
        self.fields['codice_fiscale'].help_text = "Enter your Codice Fiscale (required for external users)."

    def clean(self):
        data = super().clean()
        role = data.get('role')
        exp = data.get('expiration_date')
        course = data.get('course_project')
        identity_document = data.get('identity_document')
        external = bool(self.data.get('is_external'))  # explicit hidden flag in external form
        # External required personal fields
        if external:
            for fname in ['given_name', 'surname', 'email', 'codice_fiscale']:
                if not data.get(fname):
                    self.add_error(fname, 'This field is required.')
        # Expiration logic
        if role in EXPIRATION_REQUIRED_ROLES and not exp:
            self.add_error('expiration_date', 'Expiration date required for this role.')
        if role not in EXPIRATION_REQUIRED_ROLES:
            data['expiration_date'] = None
        if role == AccountOnboardingRequest.ROLE_COURSE and not course:
            self.add_error('course_project', 'Course selection is required for this role.')
        if role != AccountOnboardingRequest.ROLE_COURSE:
            data['course_project'] = None
        # External users (no UNIMORE SSO) must upload identity document.
        if external and not identity_document:
            self.add_error('identity_document', 'Identity document required for external users.')
        # External email uniqueness (pending requests)
        if external and data.get('email'):
            if AccountOnboardingRequest.objects.filter(email__iexact=data['email'], status=AccountOnboardingRequest.STATUS_PENDING, unimore_id__isnull=True).exists():
                self.add_error('email', 'There is already a pending request with this email.')
        # External codice_fiscale uniqueness (pending requests)
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
    course_project = forms.ModelChoiceField(
        queryset=Project.objects.none(),
        required=False,
        label='Course',
        empty_label='— none —',
        widget=forms.Select(attrs={'class': 'form-control'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['course_project'].queryset = Project.objects.filter(project_type__code='F').order_by('title')

    def clean(self):
        data = super().clean()
        role = data.get('role')
        exp = data.get('expiration_date')
        course = data.get('course_project')
        if role in EXPIRATION_REQUIRED_ROLES and not exp:
            self.add_error('expiration_date', 'Expiration date required for this role.')
        if role not in EXPIRATION_REQUIRED_ROLES:
            data['expiration_date'] = None
        if role == AccountOnboardingRequest.ROLE_COURSE and not course:
            self.add_error('course_project', 'Course selection is required for this role.')
        if role != AccountOnboardingRequest.ROLE_COURSE:
            data['course_project'] = None
        return data


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