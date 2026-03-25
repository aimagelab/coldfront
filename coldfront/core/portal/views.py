# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import operator
import re
import unicodedata
from collections import Counter

from django.conf import settings
from django.contrib.humanize.templatetags.humanize import intcomma
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import render, get_object_or_404
from django.views.decorators.cache import cache_page
from django.utils import timezone
from django.core.exceptions import ObjectDoesNotExist
from django.contrib import messages
from django.shortcuts import redirect
from django.core.mail import mail_admins, send_mail
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.views.generic import ListView

from coldfront.core.portal.models import Carousel, News, DocumentationArticle, AccountOnboardingRequest
from coldfront.core.allocation.models import Allocation, AllocationUser
from coldfront.core.grant.models import Grant
from coldfront.core.portal.utils import (
    generate_allocations_chart_data,
    generate_publication_by_year_chart_data,
    generate_resources_chart_data,
    generate_total_grants_by_agency_chart_data,
)
from coldfront.core.project.models import Project
from coldfront.core.publication.models import Publication
from coldfront.core.research_output.models import ResearchOutput
from .forms import OnboardingProcessForm, OnboardingRequestSearchForm
from .models import AccountOnboardingRequest
from django.http import Http404
from coldfront.core.utils.common import import_from_settings
from coldfront.plugins.ldap_groups.ldap_connector import LDAP
import logging

try:
    import ldap.filter as ldap_filter
except Exception:  # pragma: no cover
    ldap_filter = None

ALLOCATION_EULA_ENABLE = import_from_settings("ALLOCATION_EULA_ENABLE", False)


def _sanitize_name_for_username(text):
    """Strip accents and special characters from a name, preserving spaces and alphanumerics."""
    text = unicodedata.normalize('NFD', text)
    text = text.encode('ascii', 'ignore').decode('ascii')
    text = re.sub(r'[^a-zA-Z0-9 ]', '', text)
    return text


def home(request):
    context = {}
    if request.user.is_authenticated:
        template_name = "portal/authorized_home.html"
        project_list = (
            Project.objects.filter(
                (
                    Q(pi=request.user)
                    & Q(
                        status__name__in=[
                            "New",
                            "Active",
                        ]
                    )
                )
                | (
                    Q(
                        status__name__in=[
                            "New",
                            "Active",
                        ]
                    )
                    & Q(projectuser__user=request.user)
                    & Q(
                        projectuser__status__name__in=[
                            "Active",
                        ]
                    )
                )
            )
            .distinct()
            .order_by("-created")
        )

        allocation_list = (
            Allocation.objects.filter(
                Q(
                    status__name__in=[
                        "Active",
                        "New",
                        "Renewal Requested",
                    ]
                )
                & Q(project__status__name__in=["Active", "New"])
                & Q(project__projectuser__user=request.user)
                & Q(
                    project__projectuser__status__name__in=[
                        "Active",
                    ]
                )
                & Q(allocationuser__user=request.user)
                & Q(allocationuser__status__name__in=["Active", "PendingEULA"])
            )
            .distinct()
            .order_by("-created")
        )

        if ALLOCATION_EULA_ENABLE:
            user_status = []
            for allocation in allocation_list:
                if allocation.allocationuser_set.filter(user=request.user).exists():
                    user_status.append(allocation.allocationuser_set.get(user=request.user).status.name)
            context["user_status"] = user_status

        context["project_list"] = project_list
        context["allocation_list"] = allocation_list

        try:
            context["ondemand_url"] = settings.ONDEMAND_URL
        except AttributeError:
            pass
    else:
        template_name = "portal/nonauthorized_home.html"
        # Filter active carousels (with expiry date in the future or no expiry date)
        context['carousel_list'] = Carousel.objects.filter(
            Q(expiry_date__gte=timezone.now()) | Q(expiry_date=None)).order_by('-id')

    context["EXTRA_APPS"] = settings.INSTALLED_APPS

    if "coldfront.plugins.system_monitor" in settings.INSTALLED_APPS:
        from coldfront.plugins.system_monitor.utils import get_system_monitor_context

        context.update(get_system_monitor_context())

    return render(request, template_name, context)


def center_summary(request):
    context = {}

    # Publications Card
    publications_by_year = list(
        Publication.objects.filter(year__gte=1999)
        .values("unique_id", "year")
        .distinct()
        .values("year")
        .annotate(num_pub=Count("year"))
        .order_by("-year")
    )

    publications_by_year = [(ele["year"], ele["num_pub"]) for ele in publications_by_year]

    publication_by_year_bar_chart_data = generate_publication_by_year_chart_data(publications_by_year)
    context["publication_by_year_bar_chart_data"] = publication_by_year_bar_chart_data
    context["total_publications_count"] = (
        Publication.objects.filter(year__gte=1999).values("unique_id", "year").distinct().count()
    )

    # Research Outputs card
    context["total_research_outputs_count"] = ResearchOutput.objects.all().distinct().count()

    # Grants Card
    total_grants_by_agency_sum = list(
        Grant.objects.values("funding_agency__name").annotate(total_amount=Sum("total_amount_awarded"))
    )

    total_grants_by_agency_count = list(
        Grant.objects.values("funding_agency__name").annotate(count=Count("total_amount_awarded"))
    )

    total_grants_by_agency_count = {ele["funding_agency__name"]: ele["count"] for ele in total_grants_by_agency_count}

    total_grants_by_agency = [
        [
            "{}: ${} ({})".format(
                ele["funding_agency__name"],
                intcomma(int(ele["total_amount"])),
                total_grants_by_agency_count[ele["funding_agency__name"]],
            ),
            ele["total_amount"],
        ]
        for ele in total_grants_by_agency_sum
    ]

    total_grants_by_agency = sorted(total_grants_by_agency, key=operator.itemgetter(1), reverse=True)
    grants_agency_chart_data = generate_total_grants_by_agency_chart_data(total_grants_by_agency)
    context["grants_agency_chart_data"] = grants_agency_chart_data
    context["grants_total"] = intcomma(int(sum([float(x) for x in list(Grant.objects.values_list("total_amount_awarded", flat=True))])))
    context["grants_total_pi_only"] = intcomma(
        int(sum([float(x) for x in list(Grant.objects.filter(role="PI").values_list("total_amount_awarded", flat=True))]))
    )
    context["grants_total_copi_only"] = intcomma(
        int(sum([float(x) for x in list(Grant.objects.filter(role="CoPI").values_list("total_amount_awarded", flat=True))]))
    )
    context["grants_total_sp_only"] = intcomma(
        int(sum([float(x) for x in list(Grant.objects.filter(role="SP").values_list("total_amount_awarded", flat=True))]))
    )

    return render(request, "portal/center_summary.html", context)


@cache_page(60 * 15)
def allocation_by_fos(request):
    allocations_by_fos = Counter(
        list(
            Allocation.objects.filter(status__name="Active").values_list(
                "project__field_of_science__description", flat=True
            )
        )
    )

    user_allocations = AllocationUser.objects.filter(status__name="Active", allocation__status__name="Active")

    active_users_by_fos = Counter(
        list(user_allocations.values_list("allocation__project__field_of_science__description", flat=True))
    )
    total_allocations_users = user_allocations.values("user").distinct().count()

    active_pi_count = (
        Project.objects.filter(status__name__in=["Active", "New"])
        .values_list("pi__username", flat=True)
        .distinct()
        .count()
    )
    context = {}
    context["allocations_by_fos"] = dict(allocations_by_fos)
    context["active_users_by_fos"] = dict(active_users_by_fos)
    context["total_allocations_users"] = total_allocations_users
    context["active_pi_count"] = active_pi_count
    return render(request, "portal/allocation_by_fos.html", context)


@cache_page(60 * 15)
def allocation_summary(request):
    allocation_resources = [
        allocation.get_parent_resource.parent_resource
        if allocation.get_parent_resource.parent_resource
        else allocation.get_parent_resource
        for allocation in Allocation.objects.filter(status__name="Active")
    ]

    allocations_count_by_resource = dict(Counter(allocation_resources))

    allocation_count_by_resource_type = dict(Counter([ele.resource_type.name for ele in allocation_resources]))

    allocations_chart_data = generate_allocations_chart_data()
    resources_chart_data = generate_resources_chart_data(allocation_count_by_resource_type)

    context = {}
    context["allocations_chart_data"] = allocations_chart_data
    context["allocations_count_by_resource"] = allocations_count_by_resource
    context["resources_chart_data"] = resources_chart_data

    return render(request, "portal/allocation_summary.html", context)


def news(request, hash):
    try:
        news = News.objects.get(hash=hash)
    except News.DoesNotExist:
        raise Http404("News not found.")
    return render(request, 'portal/news.html', {'news': news})

def news_list(request):
    now = timezone.now()
    one_year_ago = now - timezone.timedelta(days=365)
    news = News.objects.filter(
        (Q(expiry_date__gte=now) | Q(expiry_date__isnull=True)) & Q(publication_date__gte=one_year_ago)
    ).order_by('-publication_date')
    return render(request, 'portal/news_list.html', {'news_list': news})


def documentation_article(request, hash):
    try:
        article = DocumentationArticle.objects.get(hash=hash)
    except DocumentationArticle.DoesNotExist:
        raise Http404("Article not found.")
    if not article.active:
        return ObjectDoesNotExist()
    root_articles = DocumentationArticle.objects.filter(parent=None, active=True).order_by('order')
    return render(request, 'portal/documentation_article.html', {'article': article, 'root_articles': root_articles})

def documentation(request):
    article = DocumentationArticle.objects.get(title='Home')
    root_articles = DocumentationArticle.objects.filter(parent=None, active=True).order_by('order')
    return render(request, 'portal/documentation_article.html', {'article': article, 'root_articles': root_articles})

def onboard(request):
    return render(request, 'portal/onboard.html')

def onboard_process(request):
    if request.user.is_authenticated:
        raise PermissionError("This view is only for unauthenticated users.")
    
    # Shibboleth attribute keys
    given_name = request.META.get('HTTP_X_REMOTE_FIRSTNAME').title()
    surname = request.META.get('HTTP_X_REMOTE_SURNAME').title()
    email = request.META.get('HTTP_X_REMOTE_EMAIL')
    unimore_id = request.META.get('HTTP_X_REMOTE_USER')
    codice_fiscale = request.META.get('HTTP_X_REMOTE_CODICEFISCALE')

    # Refuse if a user with this email already exists
    # TODO this should be done with Codice Fiscale
    if email:
        UserModel = get_user_model()
        if UserModel.objects.filter(email__iexact=email, is_active=True).exists():
            messages.error(
                request,
                "An account with this email already exists. If you need changes or access issues resolved, open a support ticket."
            )
            return redirect('onboard')

    # Prevent multiple pending requests
    existing_pending = AccountOnboardingRequest.objects.filter(
        codice_fiscale=codice_fiscale,
        status=AccountOnboardingRequest.STATUS_PENDING
    ).first()

    course_projects = Project.objects.filter(project_type__code='F').order_by('title')

    if request.method == 'POST':
        if existing_pending:
            messages.warning(request, "You already have a pending request.")
            return redirect('onboard')
        form = OnboardingProcessForm(request.POST, course_queryset=course_projects)
        if form.is_valid():
            req_obj = form.save(commit=False)
            # Generate username base
            clean_given = _sanitize_name_for_username(given_name)
            clean_surname = _sanitize_name_for_username(surname)
            base_username = ''.join([n[0] for n in clean_given.split() if n]) + clean_surname.replace(' ', '')
            base_username = base_username.lower()
            username = base_username
            User = get_user_model()
            suffix = 1
            while User.objects.filter(username=username).exists():
                username = f"{base_username}{suffix:02d}"
                suffix += 1
            req_obj.username = username
            req_obj.given_name = given_name
            req_obj.surname = surname
            req_obj.email = email
            req_obj.unimore_id = unimore_id
            req_obj.codice_fiscale = codice_fiscale

            # If role is thesis or course, assign a default expiration date in 6 months
            if req_obj.role in [AccountOnboardingRequest.ROLE_THESIS, AccountOnboardingRequest.ROLE_COURSE]:
                req_obj.expiration_date = timezone.now() + timezone.timedelta(days=180)

            req_obj.save()
            # Notify admins
            subject = "[Onboarding] New account request"
            body = f"""
New onboarding request:

Generated Username: {username}
Name: {given_name} {surname}
Email: {email}
UNIMORE ID: {unimore_id}
Role: {req_obj.role}
Course: {req_obj.course_project.title if req_obj.course_project else 'N/A'}
Expiration: {req_obj.expiration_date or 'N/A'}
Codice Fiscale: {req_obj.codice_fiscale or 'N/A'}
Submitted at: {req_obj.submitted_at}

Review in admin.
""".strip()
            notify_list = getattr(settings, 'ACCOUNT_REQUEST_NOTIFY', [])
            if notify_list:
                try:
                    send_mail(subject, body, getattr(settings, 'DEFAULT_FROM_EMAIL', None), notify_list, fail_silently=True)
                except Exception:
                    pass
            messages.success(request, "Request submitted. You will be notified after review.")
            return redirect('onboard')
    else:
        if existing_pending:
            messages.info(request, "You already have a pending request.")
            return redirect('onboard')
        form = OnboardingProcessForm(course_queryset=course_projects)

    return render(
        request,
        'portal/onboard_process.html',
        {
            'form': form,
            'given_name': given_name,
            'surname': surname,
            'email': email,
            'unimore_id': unimore_id,
            'codice_fiscale': codice_fiscale,
            'has_courses': course_projects.exists(),
        }
    )

def onboard_external(request):
    """Onboarding for external (non-UNIMORE) users: collects data manually plus identity document."""
    if request.user.is_authenticated:
        raise PermissionError("This view is only for unauthenticated users.")

    # No Shibboleth attributes; all fields collected manually
    given_name = ''
    surname = ''
    email = ''
    unimore_id = None  # explicit external

    course_projects = Project.objects.filter(project_type__code='F').order_by('title')

    if request.method == 'POST':
        form = OnboardingProcessForm(request.POST, request.FILES, course_queryset=course_projects)
        # Flag for form validation that this is external
        form.initial['is_external'] = True
        if form.is_valid():
            data = form.cleaned_data
            given_name = request.POST.get('given_name', '').strip()
            surname = request.POST.get('surname', '').strip()
            email = request.POST.get('email', '').strip()
            codice_fiscale = request.POST.get('codice_fiscale', '').strip()
            if not given_name or not surname or not email or not codice_fiscale:
                messages.error(request, 'Name, surname, email, and Codice Fiscale are required.')
            else:
                # Ensure no pending external request with same email
                if AccountOnboardingRequest.objects.filter(email__iexact=email, status=AccountOnboardingRequest.STATUS_PENDING, unimore_id__isnull=True).exists():
                    messages.warning(request, 'You already have a pending request with this email.')
                    return redirect('onboard')
                # Generate username from names
                clean_given = _sanitize_name_for_username(given_name)
                clean_surname = _sanitize_name_for_username(surname)
                base_username = (clean_given.split()[0][0] + clean_surname).lower().replace(' ', '')
                User = get_user_model()
                candidate = base_username
                i = 1
                while User.objects.filter(username=candidate).exists():
                    candidate = f"{base_username}{i:02d}"
                    i += 1
                req_obj: AccountOnboardingRequest = form.save(commit=False)
                req_obj.username = candidate
                req_obj.given_name = given_name
                req_obj.surname = surname
                req_obj.email = email
                req_obj.unimore_id = None
                req_obj.codice_fiscale = codice_fiscale
                # External must have expiration if role demands; thesis/course default 6 months if empty
                if req_obj.role in [AccountOnboardingRequest.ROLE_THESIS, AccountOnboardingRequest.ROLE_COURSE] and not req_obj.expiration_date:
                    req_obj.expiration_date = timezone.now() + timezone.timedelta(days=180)
                req_obj.save()
                # Notify admins
                subject = "[Onboarding] New EXTERNAL account request"
                body = f"""
New external onboarding request:

Generated Username: {candidate}
Name: {given_name} {surname}
Email: {email}
Codice Fiscale: {codice_fiscale}
Role: {req_obj.role}
Course: {req_obj.course_project.title if req_obj.course_project else 'N/A'}
Expiration: {req_obj.expiration_date or 'N/A'}
Submitted at: {req_obj.submitted_at}
""".strip()
                notify_list = getattr(settings, 'ACCOUNT_REQUEST_NOTIFY', [])
                if notify_list:
                    try:
                        send_mail(subject, body, getattr(settings, 'DEFAULT_FROM_EMAIL', None), notify_list, fail_silently=True)
                    except Exception:
                        pass
                messages.success(request, 'External onboarding request submitted; you will be notified after review.')
                return redirect('onboard')
    else:
        form = OnboardingProcessForm(course_queryset=course_projects)
        form.initial['is_external'] = True

    return render(
        request,
        'portal/onboard_external.html',
        {
            'form': form,
            'has_courses': course_projects.exists(),
        }
    )

def codice_fiscale(request):
    """Shibboleth-protected view to collect and store Codice Fiscale in LDAP.

    - Requires UNIMORE Shibboleth headers (uses HTTP_X_REMOTE_USER for the UNIMORE ID).
    - Finds the LDAP user by matching userPassword to "{SASL}<unimore_id>".
    - Stores the provided Codice Fiscale into the LDAP attribute employeeNumber.
    """
    logger = logging.getLogger(__name__)

    # Require Shibboleth-provided identity
    unimore_id = request.META.get('HTTP_X_REMOTE_USER')
    cf_from_header = request.META.get('HTTP_X_REMOTE_CODICEFISCALE')

    if not unimore_id:
        messages.error(request, 'Access requires UNIMORE Shibboleth. Please sign in via the university SSO and retry.')
        return redirect('onboard')

    if request.method == 'POST':
        # Do not accept manual input; rely on Shibboleth-provided value
        cf = (cf_from_header or '').strip().upper()
        if not cf:
            messages.error(request, 'Codice Fiscale was not provided by Shibboleth. Please contact support via the ticket system.')
            return redirect('codice-fiscale')

        try:
            ldap_client = LDAP()
            sasl_val = '{SASL}' + unimore_id
            # Build filter safely
            if ldap_filter:
                flt = f'(userPassword={ldap_filter.escape_filter_chars(sasl_val)})'
            else:
                flt = f'(userPassword={sasl_val})'

            ldap_client.conn.search(ldap_client.LDAP_USER_SEARCH_BASE, flt, attributes=['uid'])
            if not ldap_client.conn.entries:
                messages.error(request, 'Your directory entry was not found. Please contact support.')
                return redirect('codice-fiscale')

            entry = ldap_client.conn.entries[0]
            username = entry['uid'].value if 'uid' in entry else None
            if not username:
                messages.error(request, 'Directory entry is missing uid; cannot proceed. Contact support.')
                return redirect('codice-fiscale')

            # Update employeeNumber via helper
            ldap_client.update_user(username=username, codice_fiscale=cf)
            return redirect('codice-fiscale-thanks')
        except Exception as e:
            logger.exception('Failed updating Codice Fiscale for %s: %s', unimore_id, e)
            messages.error(request, 'An error occurred while saving your Codice Fiscale. Please try again later or contact support.' + str(e))
            return redirect('codice-fiscale')

    # GET: render simple informative form
    ldap_username = None
    try:
        ldap_client = LDAP()
        sasl_val = '{SASL}' + unimore_id
        flt = f'(userPassword={ldap_filter.escape_filter_chars(sasl_val)})' if ldap_filter else f'(userPassword={sasl_val})'
        ldap_client.conn.search(ldap_client.LDAP_USER_SEARCH_BASE, flt, attributes=['uid'])
        if ldap_client.conn.entries:
            entry = ldap_client.conn.entries[0]
            ldap_username = entry['uid'].value if 'uid' in entry else None
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.warning('LDAP lookup failed for %s: %s', unimore_id, e)

    return render(
        request,
        'portal/codice_fiscale.html',
        {
            'unimore_id': unimore_id,
            'prefill_cf': cf_from_header or '',
            'ldap_username': ldap_username,
        }
    )


_ONBOARDING_SORT_FIELDS = {'username', 'given_name', 'role', 'status', 'submitted_at'}


class OnboardingRequestListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = AccountOnboardingRequest
    template_name = 'portal/onboarding_request_list.html'
    context_object_name = 'onboarding_requests'
    paginate_by = 25

    def test_func(self):
        return self.request.user.is_superuser

    def _get_sort_params(self):
        order_by = self.request.GET.get('order_by', 'submitted_at')
        direction = self.request.GET.get('direction', 'desc')
        if order_by not in _ONBOARDING_SORT_FIELDS:
            order_by = 'submitted_at'
        if direction not in ('asc', 'desc'):
            direction = 'desc'
        return order_by, direction

    def get_queryset(self):
        qs = AccountOnboardingRequest.objects.select_related('course_project')
        if not self.request.GET:
            return qs.filter(status=AccountOnboardingRequest.STATUS_PENDING).order_by('-submitted_at')
        form = OnboardingRequestSearchForm(self.request.GET)
        if form.is_valid():
            data = form.cleaned_data
            if data.get('username'):
                qs = qs.filter(username__icontains=data['username'])
            if data.get('name'):
                qs = qs.filter(
                    Q(given_name__icontains=data['name']) | Q(surname__icontains=data['name'])
                )
            if data.get('email'):
                qs = qs.filter(email__icontains=data['email'])
            if data.get('role'):
                qs = qs.filter(role=data['role'])
            if data.get('status'):
                qs = qs.filter(status=data['status'])
        order_by, direction = self._get_sort_params()
        prefix = '' if direction == 'asc' else '-'
        return qs.order_by(f'{prefix}{order_by}')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = OnboardingRequestSearchForm(self.request.GET)
        context['search_form'] = form

        # Build filter_parameters string (without sort) to preserve filters across sort/pagination links
        filter_parameters = ''
        if form.is_valid():
            for key, value in form.cleaned_data.items():
                if value:
                    filter_parameters += f'{key}={value}&'
        context['expand_accordion'] = 'show' if filter_parameters else ''

        order_by, direction = self._get_sort_params()
        context['current_order_by'] = order_by
        context['current_direction'] = direction
        # filter_parameters includes sort so pagination preserves both filters and sort order
        context['filter_parameters'] = filter_parameters + f'order_by={order_by}&direction={direction}'
        # filter_only_parameters is used by sort links so they don't double-include sort params
        context['filter_only_parameters'] = filter_parameters.rstrip('&')
        return context


@login_required
def onboarding_request_detail(request, pk):
    if not request.user.is_superuser:
        return HttpResponseForbidden()
    onboarding_request = get_object_or_404(AccountOnboardingRequest, pk=pk)
    return render(request, 'portal/onboarding_request_detail.html', {'onboarding_request': onboarding_request})


@login_required
def onboarding_request_approve(request, pk):
    if not request.user.is_superuser:
        return HttpResponseForbidden()
    if request.method != 'POST':
        return redirect('onboarding-request-list')
    onboarding_request = get_object_or_404(AccountOnboardingRequest, pk=pk)
    if onboarding_request.status == AccountOnboardingRequest.STATUS_PENDING:
        onboarding_request.status = AccountOnboardingRequest.STATUS_APPROVED
        onboarding_request.processed_at = timezone.now()
        onboarding_request.save(update_fields=['status', 'processed_at'])
        messages.success(request, f'Request for {onboarding_request.username} approved.')
    else:
        messages.warning(request, f'Request for {onboarding_request.username} is not pending.')
    return redirect('onboarding-request-detail', pk=pk)


@login_required
def onboarding_request_reject(request, pk):
    if not request.user.is_superuser:
        return HttpResponseForbidden()
    if request.method != 'POST':
        return redirect('onboarding-request-list')
    onboarding_request = get_object_or_404(AccountOnboardingRequest, pk=pk)
    if onboarding_request.status == AccountOnboardingRequest.STATUS_PENDING:
        onboarding_request.status = AccountOnboardingRequest.STATUS_REJECTED
        onboarding_request.processed_at = timezone.now()
        onboarding_request.rejection_reason = request.POST.get('rejection_reason', '').strip() or None
        onboarding_request.save(update_fields=['status', 'processed_at', 'rejection_reason'])
        messages.success(request, f'Request for {onboarding_request.username} rejected.')
    else:
        messages.warning(request, f'Request for {onboarding_request.username} is not pending.')
    return redirect('onboarding-request-detail', pk=pk)
