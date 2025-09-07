from django import forms
from .models import AccountOnboardingRequest
from coldfront.core.project.models import Project

EXPIRATION_REQUIRED_ROLES = {
    AccountOnboardingRequest.ROLE_PHD,
    AccountOnboardingRequest.ROLE_RESEARCH_GRANT,
    AccountOnboardingRequest.ROLE_RESEARCH_CONTRACT,
    AccountOnboardingRequest.ROLE_GUEST,
    AccountOnboardingRequest.ROLE_STRUCTURED,
}

class OnboardingProcessForm(forms.ModelForm):
    # Extra (non-model) fields used for external onboarding path
    given_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    surname = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={'class': 'form-control'}))
    class Meta:
        model = AccountOnboardingRequest
        fields = ['role', 'expiration_date', 'course_project', 'identity_document']
        widgets = {
            'expiration_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        course_queryset = kwargs.pop('course_queryset', None)
        super().__init__(*args, **kwargs)
        # Limit course choices to teaching-support projects (type 'F')
        if course_queryset is None:
            course_queryset = Project.objects.filter(project_type='F').order_by('title')
        self.fields['course_project'].queryset = course_queryset
        self.fields['course_project'].required = False
        self.fields['course_project'].label = "Course"
        self.fields['course_project'].help_text = "Select the course you are attending (required if role is 'Student from a course')."

    def clean(self):
        data = super().clean()
        role = data.get('role')
        exp = data.get('expiration_date')
        course = data.get('course_project')
        identity_document = data.get('identity_document')
        external = bool(self.data.get('is_external'))  # explicit hidden flag in external form
        # External required personal fields
        if external:
            for fname in ['given_name', 'surname', 'email']:
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
        return data