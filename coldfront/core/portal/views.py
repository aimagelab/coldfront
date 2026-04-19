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

import datetime

from coldfront.core.portal.models import Carousel, News, DocumentationArticle, AccountOnboardingRequest, LdapUserEdit, AccountRenewalRequest, CourseEnrollmentRequest
from coldfront.core.allocation.models import Allocation, AllocationUser
from coldfront.core.grant.models import Grant
from coldfront.core.portal.utils import (
    generate_allocations_chart_data,
    generate_publication_by_year_chart_data,
    generate_resources_chart_data,
    generate_total_grants_by_agency_chart_data,
)
from coldfront.core.project.models import Project, ProjectUser, ProjectUserRoleChoice, ProjectUserStatusChoice
from coldfront.core.project.signals import project_activate_user
from coldfront.core.allocation.models import AllocationUserStatusChoice
from coldfront.core.allocation.signals import allocation_activate_user
from coldfront.core.publication.models import Publication
from coldfront.core.research_output.models import ResearchOutput
from .forms import (OnboardingProcessForm, OnboardingRequestSearchForm, OnboardingApproveForm,
                    LdapUserSearchForm, LdapUserEditForm,
                    AccountRenewalRequestForm, AccountRenewalApproveForm, AccountRenewalRequestSearchForm,
                    CourseEnrollmentRequestForm, CourseEnrollmentRequestSearchForm)
from .models import AccountOnboardingRequest
from django.http import Http404
from django.urls import reverse
from coldfront.core.utils.common import import_from_settings
from coldfront.plugins.ldap_groups.ldap_connector import LDAP
from coldfront.plugins.ldap_groups.utils import ROLE_GROUPS_MAP, make_sha_password, random_password
from django.core.mail import EmailMessage
import logging

logger = logging.getLogger(__name__)

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

        # LDAP account expiry banner
        try:
            _ldap = LDAP()
            _lu = _ldap.get_user(request.user.username)
            if _lu:
                _se = _lu.get('shadowExpire')
                if _se is not None:
                    _exp = datetime.date.fromtimestamp(int(_se) * 86400)
                    _days = (_exp - datetime.date.today()).days
                    if _days <= 30:
                        context['account_days_until_expiry'] = _days
                        context['account_expiry_date'] = _exp
        except Exception:
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
    query = request.GET.get('q', '').strip()
    news = News.objects.filter(
        Q(expiry_date__gte=now) | Q(expiry_date__isnull=True)
    ).order_by('-publication_date')
    if query:
        news = news.filter(Q(title__icontains=query) | Q(body__icontains=query))
    paginator = Paginator(news, 12)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'portal/news_list.html', {'page_obj': page_obj, 'query': query})


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

def team(request):
    return render(request, 'portal/team.html')

def documentation_search(request):
    query = request.GET.get('q', '').strip()
    root_articles = DocumentationArticle.objects.filter(parent=None, active=True).order_by('order')
    results = []
    if query:
        results = (
            DocumentationArticle.objects
            .filter(active=True)
            .filter(Q(title__icontains=query) | Q(body__icontains=query))
            .select_related('parent')
            .order_by('parent__order', 'order')
        )
    return render(request, 'portal/documentation_article.html', {
        'root_articles': root_articles,
        'search_query': query,
        'search_results': results,
    })

def onboard(request):
    if request.user.is_authenticated:
        messages.info(request, "You already have an account.")
        return redirect('home')
    return render(request, 'portal/onboard.html')

def onboard_process(request):
    if request.user.is_authenticated:
        messages.info(request, "You already have an account.")
        return redirect('home')
    
    # Shibboleth attribute keys
    given_name = request.META.get('HTTP_X_REMOTE_FIRSTNAME').title()
    surname = request.META.get('HTTP_X_REMOTE_SURNAME').title()
    email = request.META.get('HTTP_X_REMOTE_EMAIL')
    unimore_id = request.META.get('HTTP_X_REMOTE_USER')
    codice_fiscale = request.META.get('HTTP_X_REMOTE_CODICEFISCALE')

    # Check for an existing LDAP account via Codice Fiscale
    renewal_username = None
    if codice_fiscale:
        try:
            ldap_client = LDAP()
            ldap_result = ldap_client.find_user_by_codice_fiscale(codice_fiscale)
            if ldap_result:
                if not ldap_result['is_expired']:
                    messages.error(
                        request,
                        "An account with this Codice Fiscale is already active. "
                        "If you need changes or access issues resolved, open a support ticket."
                    )
                    return redirect('onboard')
                # Expired user — allow renewal and reuse existing username
                renewal_username = ldap_result['username']
        except Exception:
            logger.warning('LDAP lookup failed during onboard_process; proceeding without check.', exc_info=True)

    # Prevent multiple pending requests
    existing_pending = AccountOnboardingRequest.objects.filter(
        codice_fiscale=codice_fiscale,
        status=AccountOnboardingRequest.STATUS_PENDING
    ).first()

    if request.method == 'POST':
        if existing_pending:
            messages.warning(request, "You already have a pending request.")
            return redirect('onboard')
        form = OnboardingProcessForm(request.POST)
        if form.is_valid():
            req_obj = form.save(commit=False)
            if renewal_username:
                username = renewal_username
            else:
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

            if req_obj.role in [AccountOnboardingRequest.ROLE_THESIS, AccountOnboardingRequest.ROLE_COURSE]:
                req_obj.expiration_date = timezone.now() + timezone.timedelta(days=180)

            req_obj.save()
            center_name = getattr(settings, 'CENTER_NAME', 'HPC Center')
            subject = f"[{center_name}] New onboarding request"
            review_url = request.build_absolute_uri(reverse('onboarding-request-detail', args=[req_obj.pk]))
            body = f"""New onboarding request submitted and awaiting review.

  Username   : {username}
  Name       : {given_name} {surname}
  Email      : {email}
  UNIMORE ID : {unimore_id}
  Role       : {req_obj.role}
  Expiration : {req_obj.expiration_date or 'N/A'}
  CF         : {req_obj.codice_fiscale or 'N/A'}
  Submitted  : {req_obj.submitted_at:%Y-%m-%d %H:%M}

Review here: {review_url}""".strip()
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
        if renewal_username:
            messages.info(
                request,
                "Your account has expired. Submitting this form will renew it with a new role and expiration date."
            )
        form = OnboardingProcessForm()

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
            'is_renewal': bool(renewal_username),
        }
    )

def onboard_external(request):
    """Onboarding for external (non-UNIMORE) users: collects data manually plus identity document."""
    if request.user.is_authenticated:
        messages.info(request, "You already have an account.")
        return redirect('home')

    # No Shibboleth attributes; all fields collected manually
    given_name = ''
    surname = ''
    email = ''
    unimore_id = None  # explicit external

    if request.method == 'POST':
        form = OnboardingProcessForm(request.POST, request.FILES)
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
                if AccountOnboardingRequest.objects.filter(codice_fiscale=codice_fiscale, status=AccountOnboardingRequest.STATUS_PENDING).exists():
                    messages.warning(request, 'You already have a pending request.')
                    return redirect('onboard')
                renewal_username = None
                try:
                    ldap_client = LDAP()
                    ldap_result = ldap_client.find_user_by_codice_fiscale(codice_fiscale)
                    if ldap_result:
                        if not ldap_result['is_expired']:
                            messages.error(
                                request,
                                "An account with this Codice Fiscale is already active. "
                                "If you need changes or access issues resolved, open a support ticket."
                            )
                            return redirect('onboard')
                        renewal_username = ldap_result['username']
                except Exception:
                    logger.warning('LDAP lookup failed during onboard_external; proceeding without check.', exc_info=True)
                if renewal_username:
                    candidate = renewal_username
                else:
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
                if req_obj.role in [AccountOnboardingRequest.ROLE_THESIS, AccountOnboardingRequest.ROLE_COURSE] and not req_obj.expiration_date:
                    req_obj.expiration_date = timezone.now() + timezone.timedelta(days=180)
                req_obj.save()
                center_name = getattr(settings, 'CENTER_NAME', 'HPC Center')
                subject = f"[{center_name}] New external onboarding request"
                review_url = request.build_absolute_uri(reverse('onboarding-request-detail', args=[req_obj.pk]))
                body = f"""New external onboarding request submitted and awaiting review.

  Username   : {candidate}
  Name       : {given_name} {surname}
  Email      : {email}
  CF         : {codice_fiscale}
  Role       : {req_obj.role}
  Expiration : {req_obj.expiration_date or 'N/A'}
  Submitted  : {req_obj.submitted_at:%Y-%m-%d %H:%M}

Review here: {review_url}""".strip()
                notify_list = getattr(settings, 'ACCOUNT_REQUEST_NOTIFY', [])
                if notify_list:
                    try:
                        send_mail(subject, body, getattr(settings, 'DEFAULT_FROM_EMAIL', None), notify_list, fail_silently=True)
                    except Exception:
                        pass
                messages.success(request, 'External onboarding request submitted; you will be notified after review.')
                return redirect('onboard')
    else:
        form = OnboardingProcessForm()
        form.initial['is_external'] = True

    return render(request, 'portal/onboard_external.html', {'form': form})

def codice_fiscale(request):
    """Shibboleth-protected view to collect and store Codice Fiscale in LDAP.

    - Requires UNIMORE Shibboleth headers (uses HTTP_X_REMOTE_USER for the UNIMORE ID).
    - Finds the LDAP user by matching userPassword to "{SASL}<unimore_id>".
    - Stores the provided Codice Fiscale into the LDAP attribute employeeNumber.
    """
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
    approve_form = OnboardingApproveForm(initial={
        'role': onboarding_request.role,
        'expiration_date': onboarding_request.expiration_date,
    })
    return render(request, 'portal/onboarding_request_detail.html', {
        'onboarding_request': onboarding_request,
        'approve_form': approve_form,
    })


@login_required
def onboarding_request_approve(request, pk):
    if not request.user.is_superuser:
        return HttpResponseForbidden()
    if request.method != 'POST':
        return redirect('onboarding-request-list')
    onboarding_request = get_object_or_404(AccountOnboardingRequest, pk=pk)
    if onboarding_request.status == AccountOnboardingRequest.STATUS_PENDING:
        form = OnboardingApproveForm(request.POST)
        if not form.is_valid():
            approve_form = form
            return render(request, 'portal/onboarding_request_detail.html', {
                'onboarding_request': onboarding_request,
                'approve_form': approve_form,
            })
        onboarding_request.role = form.cleaned_data['role']
        onboarding_request.expiration_date = form.cleaned_data['expiration_date']
        onboarding_request.status = AccountOnboardingRequest.STATUS_APPROVED
        onboarding_request.processed_at = timezone.now()
        onboarding_request.save(update_fields=['role', 'expiration_date', 'status', 'processed_at'])
        messages.success(request, f'Request for {onboarding_request.username} approved.')
    else:
        messages.warning(request, f'Request for {onboarding_request.username} is not pending.')
    return redirect('onboarding-request-detail', pk=pk)


def _send_onboarding_rejection_email(onboarding_request):
    help_url = getattr(settings, 'CENTER_HELP_URL', '')
    helpdesk_address = getattr(settings, 'EMAIL_TICKET_SYSTEM_ADDRESS', '')
    center_name = getattr(settings, 'CENTER_NAME', 'HPC Center')
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', None)

    if help_url:
        helpdesk_line = f"If you have questions or believe this decision was made in error, please contact the Helpdesk: {help_url}"
    elif helpdesk_address:
        helpdesk_line = f"If you have questions or believe this decision was made in error, please contact the Helpdesk at {helpdesk_address}"
    else:
        helpdesk_line = "If you have questions or believe this decision was made in error, please contact the Helpdesk."

    reason_section = (
        f"\nReason:\n{onboarding_request.rejection_reason}\n"
        if onboarding_request.rejection_reason
        else "\nNo specific reason was provided.\n"
    )

    body = f"""\
Dear {onboarding_request.given_name} {onboarding_request.surname},

We regret to inform you that your account request at {center_name} has been rejected.
{reason_section}
{helpdesk_line}

The {center_name} Team
""".strip()

    subject = f"[{center_name}] Account request rejected"
    try:
        send_mail(subject, body, from_email, [onboarding_request.email], fail_silently=True)
    except Exception:
        pass


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
        _send_onboarding_rejection_email(onboarding_request)
        messages.success(request, f'Request for {onboarding_request.username} rejected.')
    else:
        messages.warning(request, f'Request for {onboarding_request.username} is not pending.')
    return redirect('onboarding-request-detail', pk=pk)


# ---------------------------------------------------------------------------
# Reverse-lookup: LDAP group name → role label
# ---------------------------------------------------------------------------
_GROUP_TO_ROLE = {v: k for k, v in ROLE_GROUPS_MAP.items()}


def _decode_password(raw) -> str:
    if isinstance(raw, (bytes, bytearray)):
        return raw.decode('utf-8', errors='ignore')
    if isinstance(raw, list) and raw:
        return _decode_password(raw[0])
    return str(raw) if raw else ''


def _send_ldap_edit_notification(username: str, email: str, changes: dict):
    """Notify the user when their role or expiration date has been changed."""
    notifiable = {k: v for k, v in changes.items() if k in ('role', 'expiration_date')}
    if not notifiable:
        return
    center_name = getattr(settings, 'CENTER_NAME', 'HPC Center')
    from_email = getattr(settings, 'EMAIL_SENDER', None) or getattr(settings, 'DEFAULT_FROM_EMAIL', None)
    help_url = getattr(settings, 'CENTER_HELP_URL', '') or getattr(settings, 'EMAIL_TICKET_SYSTEM_ADDRESS', '')

    lines = []
    if 'role' in notifiable:
        old = notifiable['role']['old'] or '\u2014'
        new = notifiable['role']['new']
        lines.append(f"  Role       : {new}  (previously: {old})")
    if 'expiration_date' in notifiable:
        old = notifiable['expiration_date']['old'] if notifiable['expiration_date']['old'] != 'None' else 'N/A'
        new = notifiable['expiration_date']['new'] if notifiable['expiration_date']['new'] != 'None' else 'N/A'
        lines.append(f"  Expiration : {new}  (previously: {old})")

    changes_block = '\n'.join(lines)
    support_line = (f"\nFor questions, please open a ticket:\n\n  {help_url}" if help_url else '')

    body = (
        f"Dear {username},\n\n"
        f"your account details at {center_name} have been updated.\n\n"
        f"{changes_block}\n"
        f"{support_line}\n\n"
        f"The {center_name} Team"
    )
    try:
        EmailMessage(
            subject=f'[{center_name}] Account details updated',
            body=body,
            from_email=from_email,
            to=[email],
        ).send(fail_silently=False)
        logger.info('Sent account-update notification to %s (%s)', username, email)
    except Exception as e:
        logger.error('Failed to send account-update notification to %s (%s): %s', username, email, e)


def _send_otp_email(username: str, email: str, raw_pw: str):
    center_name = getattr(settings, 'CENTER_NAME', 'HPC Center')
    from_email = getattr(settings, 'EMAIL_SENDER', None) or getattr(settings, 'DEFAULT_FROM_EMAIL', None)
    body = (
        f'Dear {username},\n\n'
        f'Your account at {center_name} has been converted to a local (non-UNIMORE) account.\n\n'
        f'Your one-time password is:\n\n'
        f'  {raw_pw}\n\n'
        f'Use this password at your first SSH login. You will be prompted to change it immediately.\n\n'
        f'The {center_name} Team'
    )
    try:
        EmailMessage(
            subject=f'Account password reset \u2014 {center_name}',
            body=body,
            from_email=from_email,
            to=[email],
        ).send(fail_silently=False)
        logger.info('Sent OTP reset email to %s (%s)', username, email)
    except Exception as e:
        logger.error('Failed to send OTP reset email to %s (%s): %s', username, email, e)


@login_required
def ldap_user_edit(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden()

    username = (
        request.POST.get('username', '') or request.GET.get('username', '')
    ).strip()

    ldap_user = None
    current_role = None
    current_is_unimore = False
    current_unimore_id = ''
    current_expiration = None
    edit_form = None

    if username:
        try:
            ldap_client = LDAP()
            ldap_user = ldap_client.get_user(username)
        except Exception as e:
            logger.exception('LDAP connection failed: %s', e)
            messages.error(request, f'LDAP connection failed: {e}')

        if ldap_user is None and username:
            messages.error(request, f'User "{username}" not found in LDAP.')
        elif ldap_user:
            # Detect current role from group membership
            try:
                user_groups = ldap_client.get_groups_of_user(username)
            except Exception:
                user_groups = []
            for g in user_groups:
                if g in _GROUP_TO_ROLE:
                    current_role = _GROUP_TO_ROLE[g]
                    break

            # Detect is_unimore from userPassword
            pw_str = _decode_password(ldap_user.get('userPassword', ''))
            current_is_unimore = pw_str.startswith('{SASL}')
            current_unimore_id = pw_str[6:] if current_is_unimore else ''

            # Parse shadowExpire → date
            shadow_expire = ldap_user.get('shadowExpire')
            if shadow_expire is not None:
                try:
                    current_expiration = datetime.date.fromtimestamp(int(shadow_expire) * 86400)
                except (ValueError, TypeError, OSError):
                    pass

            if request.method == 'POST':
                edit_form = LdapUserEditForm(request.POST)
                if edit_form.is_valid():
                    data = edit_form.cleaned_data
                    new_role = data['role']
                    new_expiration = data.get('expiration_date')
                    new_email = data['email']
                    new_mobile = (data.get('mobile') or '').strip() or None
                    new_is_unimore = data['is_unimore']
                    new_unimore_id = (data.get('unimore_id') or '').strip() or None

                    old_role_group = ROLE_GROUPS_MAP.get(current_role) if current_role else None
                    new_role_group = ROLE_GROUPS_MAP.get(new_role)

                    # Build audit changes dict
                    changes = {}
                    if current_role != new_role:
                        changes['role'] = {'old': current_role, 'new': new_role}
                    if str(current_expiration) != str(new_expiration):
                        changes['expiration_date'] = {'old': str(current_expiration), 'new': str(new_expiration)}
                    current_email = ldap_user.get('mail', '')
                    if isinstance(current_email, list):
                        current_email = current_email[0] if current_email else ''
                    if current_email != new_email:
                        changes['email'] = {'old': current_email, 'new': new_email}
                    current_mobile = ldap_user.get('mobile', '')
                    if isinstance(current_mobile, list):
                        current_mobile = current_mobile[0] if current_mobile else ''
                    if (current_mobile or None) != new_mobile:
                        changes['mobile'] = {'old': current_mobile or None, 'new': new_mobile}
                    if current_is_unimore != new_is_unimore:
                        changes['is_unimore'] = {'old': current_is_unimore, 'new': new_is_unimore}

                    # Password: generate OTP only when switching unimore → non-unimore
                    raw_pw = None
                    password_hash = None
                    if not new_is_unimore and current_is_unimore:
                        raw_pw = random_password()
                        password_hash = make_sha_password(raw_pw)

                    try:
                        ldap_client.update_user(
                            username=username,
                            email=new_email,
                            role=new_role_group,
                            expiration_date=new_expiration,
                            mobile=new_mobile,
                            is_unimore=new_is_unimore,
                            unimore_ldap_username=new_unimore_id if new_is_unimore else None,
                            password_hash=password_hash,
                            move_if_role_changed=True,
                        )

                        # Sync role-group membership
                        if old_role_group and old_role_group != new_role_group:
                            ldap_client.remove_user_from_groups(username, [old_role_group])
                        if new_role_group and new_role_group != old_role_group:
                            ldap_client.add_user_to_groups(username, [new_role_group])

                        if raw_pw:
                            _send_otp_email(username, new_email, raw_pw)

                        _send_ldap_edit_notification(username, new_email, changes)

                        if changes:
                            LdapUserEdit.objects.create(
                                editor=request.user,
                                target_username=username,
                                changes=changes,
                            )
                            messages.success(request, f'User "{username}" updated in LDAP.')
                        else:
                            messages.info(request, f'No changes detected for "{username}".')

                        return redirect(f'{reverse("ldap-user-edit")}?username={username}')
                    except Exception as e:
                        logger.exception('Failed to update LDAP user %s: %s', username, e)
                        messages.error(request, f'Failed to update user: {e}')
            else:
                _mail = ldap_user.get('mail', '')
                _mob = ldap_user.get('mobile', '')
                edit_form = LdapUserEditForm(initial={
                    'role': current_role or '',
                    'expiration_date': current_expiration,
                    'email': _mail[0] if isinstance(_mail, list) else _mail,
                    'mobile': _mob[0] if isinstance(_mob, list) else _mob,
                    'is_unimore': current_is_unimore,
                    'unimore_id': current_unimore_id,
                })

    search_form = LdapUserSearchForm(initial={'username': username} if username else None)
    recent_edits = LdapUserEdit.objects.select_related('editor').order_by('-timestamp')[:20]

    return render(request, 'portal/ldap_user_edit.html', {
        'search_form': search_form,
        'username': username,
        'ldap_user': ldap_user,
        'current_role': current_role,
        'current_is_unimore': current_is_unimore,
        'current_expiration': current_expiration,
        'edit_form': edit_form,
        'recent_edits': recent_edits,
    })


# ---------------------------------------------------------------------------
# Account renewal request — user-facing
# ---------------------------------------------------------------------------

def _send_renewal_admin_notification(request_obj, http_request):
    center_name = getattr(settings, 'CENTER_NAME', 'HPC Center')
    notify_list = getattr(settings, 'ACCOUNT_REQUEST_NOTIFY', [])
    if not notify_list:
        return
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', None)
    review_url = http_request.build_absolute_uri(
        reverse('account-renewal-request-detail', args=[request_obj.pk])
    )
    role_line = (
        f"  New role   : {request_obj.new_role}  (was: {request_obj.current_role or '—'})\n"
        if request_obj.role_changed else ''
    )
    body = (
        f"Account renewal request submitted and awaiting review.\n\n"
        f"  Username   : {request_obj.requester_username}\n"
        f"  Current exp: {request_obj.current_expiration_date or 'N/A'}\n"
        f"  Requested  : {request_obj.requested_expiration_date}\n"
        f"{role_line}"
        f"  Notes      : {request_obj.notes or '—'}\n\n"
        f"Review here: {review_url}"
    )
    try:
        send_mail(
            f'[{center_name}] Account renewal request — {request_obj.requester_username}',
            body, from_email, notify_list, fail_silently=True,
        )
    except Exception:
        pass


def _send_renewal_user_email(request_obj, approved: bool, new_expiration=None, new_role=None):
    center_name = getattr(settings, 'CENTER_NAME', 'HPC Center')
    from_email = getattr(settings, 'EMAIL_SENDER', None) or getattr(settings, 'DEFAULT_FROM_EMAIL', None)
    help_url = getattr(settings, 'CENTER_HELP_URL', '') or getattr(settings, 'EMAIL_TICKET_SYSTEM_ADDRESS', '')

    if approved:
        role_line = f"  Role       : {new_role}\n" if new_role else ''
        support_line = f"\nFor questions, please open a ticket:\n\n  {help_url}" if help_url else ''
        body = (
            f"Dear {request_obj.requester_username},\n\n"
            f"your account renewal request at {center_name} has been approved.\n\n"
            f"  Username   : {request_obj.requester_username}\n"
            f"{role_line}"
            f"  Expiration : {new_expiration}\n"
            f"{support_line}\n\n"
            f"The {center_name} Team"
        )
        subject = f'[{center_name}] Account renewal approved'
    else:
        reason_section = (
            f"\nReason:\n{request_obj.rejection_reason}\n"
            if request_obj.rejection_reason else "\nNo specific reason was provided.\n"
        )
        support_line = f"\nFor questions, please open a ticket:\n\n  {help_url}" if help_url else ''
        body = (
            f"Dear {request_obj.requester_username},\n\n"
            f"your account renewal request at {center_name} has been rejected.\n"
            f"{reason_section}"
            f"{support_line}\n\n"
            f"The {center_name} Team"
        )
        subject = f'[{center_name}] Account renewal rejected'

    # Fetch user email from LDAP for the notification
    try:
        ldap_client = LDAP()
        user_email = ldap_client.get_email(request_obj.requester_username)
    except Exception:
        user_email = None

    if not user_email:
        logger.warning('Could not fetch email for %s; renewal notification not sent.', request_obj.requester_username)
        return
    try:
        EmailMessage(subject=subject, body=body, from_email=from_email, to=[user_email]).send(fail_silently=False)
    except Exception as e:
        logger.error('Failed to send renewal notification to %s: %s', request_obj.requester_username, e)


@login_required
def account_renewal_request(request):
    username = request.user.username

    # Look up LDAP state
    ldap_user = None
    current_role = None
    current_expiration = None
    try:
        ldap_client = LDAP()
        ldap_user = ldap_client.get_user(username)
        if ldap_user:
            user_groups = ldap_client.get_groups_of_user(username)
            for g in user_groups:
                if g in _GROUP_TO_ROLE:
                    current_role = _GROUP_TO_ROLE[g]
                    break
            se = ldap_user.get('shadowExpire')
            if se is not None:
                try:
                    current_expiration = datetime.date.fromtimestamp(int(se) * 86400)
                except (ValueError, TypeError, OSError):
                    pass
    except Exception as e:
        logger.warning('LDAP lookup failed for renewal page (%s): %s', username, e)

    # Check for existing pending request
    pending = AccountRenewalRequest.objects.filter(
        requester_username=username,
        status=AccountRenewalRequest.STATUS_PENDING,
    ).first()

    form = None
    if not pending:
        if request.method == 'POST':
            form = AccountRenewalRequestForm(request.POST, request.FILES)
            if form.is_valid():
                data = form.cleaned_data
                renewal = AccountRenewalRequest(
                    requester_username=username,
                    current_role=current_role or '',
                    current_expiration_date=current_expiration,
                    requested_expiration_date=data['requested_expiration_date'],
                    role_changed=data['role_changed'],
                    new_role=data.get('new_role', '') if data['role_changed'] else '',
                    proof_document=data.get('proof_document') or None,
                    notes=data.get('notes', ''),
                )
                renewal.save()
                _send_renewal_admin_notification(renewal, request)
                messages.success(request, 'Your renewal request has been submitted and is awaiting review.')
                return redirect('account-renewal-request')
        else:
            form = AccountRenewalRequestForm()

    days_until_expiry = None
    if current_expiration:
        days_until_expiry = (current_expiration - datetime.date.today()).days

    return render(request, 'portal/account_renewal_request.html', {
        'ldap_user': ldap_user,
        'current_role': current_role,
        'current_expiration': current_expiration,
        'days_until_expiry': days_until_expiry,
        'pending': pending,
        'form': form,
    })


# ---------------------------------------------------------------------------
# Account renewal request — admin views
# ---------------------------------------------------------------------------

_RENEWAL_SORT_FIELDS = {'requester_username', 'status', 'submitted_at', 'requested_expiration_date'}


class AccountRenewalRequestListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = AccountRenewalRequest
    template_name = 'portal/account_renewal_request_list.html'
    context_object_name = 'renewal_requests'
    paginate_by = 25

    def test_func(self):
        return self.request.user.is_superuser

    def _get_sort_params(self):
        order_by = self.request.GET.get('order_by', 'submitted_at')
        direction = self.request.GET.get('direction', 'desc')
        if order_by not in _RENEWAL_SORT_FIELDS:
            order_by = 'submitted_at'
        if direction not in ('asc', 'desc'):
            direction = 'desc'
        return order_by, direction

    def get_queryset(self):
        qs = AccountRenewalRequest.objects.all()
        if not self.request.GET:
            return qs.filter(status=AccountRenewalRequest.STATUS_PENDING).order_by('-submitted_at')
        form = AccountRenewalRequestSearchForm(self.request.GET)
        if form.is_valid():
            data = form.cleaned_data
            if data.get('username'):
                qs = qs.filter(requester_username__icontains=data['username'])
            if data.get('status'):
                qs = qs.filter(status=data['status'])
        order_by, direction = self._get_sort_params()
        prefix = '' if direction == 'asc' else '-'
        return qs.order_by(f'{prefix}{order_by}')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = AccountRenewalRequestSearchForm(self.request.GET)
        ctx['search_form'] = form
        filter_parameters = ''
        if form.is_valid():
            for key, value in form.cleaned_data.items():
                if value:
                    filter_parameters += f'{key}={value}&'
        ctx['expand_accordion'] = 'show' if filter_parameters else ''
        order_by, direction = self._get_sort_params()
        ctx['current_order_by'] = order_by
        ctx['current_direction'] = direction
        ctx['filter_parameters'] = filter_parameters + f'order_by={order_by}&direction={direction}'
        ctx['filter_only_parameters'] = filter_parameters.rstrip('&')
        return ctx


@login_required
def account_renewal_request_detail(request, pk):
    if not request.user.is_superuser:
        return HttpResponseForbidden()
    renewal = get_object_or_404(AccountRenewalRequest, pk=pk)
    effective_role = renewal.new_role if renewal.role_changed else renewal.current_role
    approve_form = AccountRenewalApproveForm(initial={
        'role': effective_role,
        'expiration_date': renewal.requested_expiration_date,
    })
    return render(request, 'portal/account_renewal_request_detail.html', {
        'renewal': renewal,
        'approve_form': approve_form,
    })


@login_required
def account_renewal_request_approve(request, pk):
    if not request.user.is_superuser:
        return HttpResponseForbidden()
    if request.method != 'POST':
        return redirect('account-renewal-request-detail', pk=pk)
    renewal = get_object_or_404(AccountRenewalRequest, pk=pk)
    if renewal.status != AccountRenewalRequest.STATUS_PENDING:
        messages.warning(request, 'This request is not pending.')
        return redirect('account-renewal-request-detail', pk=pk)

    form = AccountRenewalApproveForm(request.POST)
    if not form.is_valid():
        effective_role = renewal.new_role if renewal.role_changed else renewal.current_role
        return render(request, 'portal/account_renewal_request_detail.html', {
            'renewal': renewal,
            'approve_form': form,
        })

    new_expiration = form.cleaned_data['expiration_date']
    approved_role = form.cleaned_data['role']

    try:
        ldap_client = LDAP()
        old_role_group = ROLE_GROUPS_MAP.get(renewal.current_role) if renewal.current_role else None
        new_role_group = ROLE_GROUPS_MAP.get(approved_role)

        ldap_client.update_user(
            username=renewal.requester_username,
            role=new_role_group,
            expiration_date=new_expiration,
            move_if_role_changed=True,
        )

        if old_role_group and old_role_group != new_role_group:
            ldap_client.remove_user_from_groups(renewal.requester_username, [old_role_group])
        if new_role_group and new_role_group != old_role_group:
            ldap_client.add_user_to_groups(renewal.requester_username, [new_role_group])

        # Audit log
        changes = {}
        if renewal.current_expiration_date != new_expiration:
            changes['expiration_date'] = {'old': str(renewal.current_expiration_date), 'new': str(new_expiration)}
        if renewal.current_role != approved_role:
            changes['role'] = {'old': renewal.current_role, 'new': approved_role}
        if changes:
            LdapUserEdit.objects.create(
                editor=request.user,
                target_username=renewal.requester_username,
                changes=changes,
            )
    except Exception as e:
        logger.exception('Failed to apply renewal for %s: %s', renewal.requester_username, e)
        messages.error(request, f'LDAP update failed: {e}')
        return redirect('account-renewal-request-detail', pk=pk)

    renewal.status = AccountRenewalRequest.STATUS_APPROVED
    renewal.processed_at = timezone.now()
    renewal.processed_by = request.user
    renewal.save(update_fields=['status', 'processed_at', 'processed_by'])

    _send_renewal_user_email(renewal, approved=True, new_expiration=new_expiration, new_role=approved_role)
    messages.success(request, f'Renewal for {renewal.requester_username} approved and LDAP updated.')
    return redirect('account-renewal-request-detail', pk=pk)


@login_required
def account_renewal_request_reject(request, pk):
    if not request.user.is_superuser:
        return HttpResponseForbidden()
    if request.method != 'POST':
        return redirect('account-renewal-request-detail', pk=pk)
    renewal = get_object_or_404(AccountRenewalRequest, pk=pk)
    if renewal.status != AccountRenewalRequest.STATUS_PENDING:
        messages.warning(request, 'This request is not pending.')
        return redirect('account-renewal-request-detail', pk=pk)

    renewal.status = AccountRenewalRequest.STATUS_REJECTED
    renewal.processed_at = timezone.now()
    renewal.processed_by = request.user
    renewal.rejection_reason = request.POST.get('rejection_reason', '').strip()
    renewal.save(update_fields=['status', 'processed_at', 'processed_by', 'rejection_reason'])

    _send_renewal_user_email(renewal, approved=False)
    messages.success(request, f'Renewal for {renewal.requester_username} rejected.')
    return redirect('account-renewal-request-detail', pk=pk)


# ---------------------------------------------------------------------------
# Course enrollment request
# ---------------------------------------------------------------------------

_STUDENT_GROUPS = {'tesisti', 'studenti'}

_ELIGIBLE_STUDENT_ROLES = {
    AccountOnboardingRequest.ROLE_THESIS,
    AccountOnboardingRequest.ROLE_COURSE,
}


def _enroll_user_in_project(username, project):
    """Add a user to a project and all its active allocations."""
    User = get_user_model()
    user_obj, _ = User.objects.get_or_create(username=username)
    user_role = ProjectUserRoleChoice.objects.get(name='User')
    active_proj_status = ProjectUserStatusChoice.objects.get(name='Active')
    active_alloc_status = AllocationUserStatusChoice.objects.get(name='Active')

    if project.projectuser_set.filter(user=user_obj).exists():
        pu = project.projectuser_set.get(user=user_obj)
        pu.role = user_role
        pu.status = active_proj_status
        pu.save()
    else:
        pu = ProjectUser.objects.create(
            user=user_obj, project=project, role=user_role, status=active_proj_status,
        )
    project_activate_user.send(sender=None, project_user_pk=pu.pk)

    for allocation in project.allocation_set.filter(status__name__in=['Active', 'Renewal Requested']):
        if allocation.allocationuser_set.filter(user=user_obj).exists():
            au = allocation.allocationuser_set.get(user=user_obj)
            au.status = active_alloc_status
            au.save()
        else:
            au = AllocationUser.objects.create(
                allocation=allocation, user=user_obj, status=active_alloc_status,
            )
        allocation_activate_user.send(sender=None, allocation_user_pk=au.pk)


def _send_enrollment_admin_notification(enrollment, http_request):
    center_name = getattr(settings, 'CENTER_NAME', 'HPC Center')
    notify_list = getattr(settings, 'ACCOUNT_REQUEST_NOTIFY', [])
    if not notify_list:
        return
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', None)
    review_url = http_request.build_absolute_uri(
        reverse('course-enrollment-request-detail', args=[enrollment.pk])
    )
    body = (
        f"Course enrollment request submitted and awaiting review.\n\n"
        f"  Username : {enrollment.requester_username}\n"
        f"  Course   : {enrollment.project.title}\n"
        f"  Notes    : {enrollment.motivation or '—'}\n\n"
        f"Review here: {review_url}"
    )
    try:
        send_mail(
            f'[{center_name}] Course enrollment request — {enrollment.requester_username}',
            body, from_email, notify_list, fail_silently=True,
        )
    except Exception:
        pass


def _send_enrollment_user_email(enrollment, approved: bool):
    center_name = getattr(settings, 'CENTER_NAME', 'HPC Center')
    from_email = getattr(settings, 'EMAIL_SENDER', None) or getattr(settings, 'DEFAULT_FROM_EMAIL', None)
    help_url = getattr(settings, 'CENTER_HELP_URL', '') or getattr(settings, 'EMAIL_TICKET_SYSTEM_ADDRESS', '')
    support_line = f"\nFor questions, please open a ticket:\n\n  {help_url}" if help_url else ''

    if approved:
        body = (
            f"Dear {enrollment.requester_username},\n\n"
            f"your enrollment request for the course project "
            f"\"{enrollment.project.title}\" at {center_name} has been approved.\n\n"
            f"You now have access to the project and its active allocations."
            f"{support_line}\n\n"
            f"The {center_name} Team"
        )
        subject = f'[{center_name}] Course enrollment approved — {enrollment.project.title}'
    else:
        reason_section = (
            f"\nReason:\n{enrollment.rejection_reason}\n"
            if enrollment.rejection_reason else "\nNo specific reason was provided.\n"
        )
        body = (
            f"Dear {enrollment.requester_username},\n\n"
            f"your enrollment request for the course project "
            f"\"{enrollment.project.title}\" at {center_name} has been rejected.\n"
            f"{reason_section}"
            f"{support_line}\n\n"
            f"The {center_name} Team"
        )
        subject = f'[{center_name}] Course enrollment rejected — {enrollment.project.title}'

    try:
        ldap_client = LDAP()
        user_email = ldap_client.get_email(enrollment.requester_username)
    except Exception:
        user_email = None

    if not user_email:
        logger.warning('Could not fetch email for %s; enrollment notification not sent.', enrollment.requester_username)
        return
    try:
        EmailMessage(subject=subject, body=body, from_email=from_email, to=[user_email]).send(fail_silently=False)
    except Exception as e:
        logger.error('Failed to send enrollment notification to %s: %s', enrollment.requester_username, e)


@login_required
def course_enrollment_request(request):
    username = request.user.username

    # Check eligibility via LDAP group membership
    current_role = None
    try:
        ldap_client = LDAP()
        if ldap_client.get_user(username):
            for g in ldap_client.get_groups_of_user(username):
                if g in _GROUP_TO_ROLE:
                    current_role = _GROUP_TO_ROLE[g]
                    break
    except Exception as e:
        logger.warning('LDAP lookup failed for enrollment page (%s): %s', username, e)

    if current_role not in _ELIGIBLE_STUDENT_ROLES:
        return render(request, 'portal/course_enrollment_request.html', {
            'not_eligible': True,
            'current_role': current_role,
        })

    all_course_projects = Project.objects.filter(project_type__code='F').order_by('title')

    User = get_user_model()
    try:
        user_obj = User.objects.get(username=username)
        enrolled_project_ids = set(
            ProjectUser.objects.filter(user=user_obj, status__name='Active')
            .values_list('project_id', flat=True)
        )
    except User.DoesNotExist:
        enrolled_project_ids = set()

    pending_project_ids = set(
        CourseEnrollmentRequest.objects.filter(
            requester_username=username,
            status=CourseEnrollmentRequest.STATUS_PENDING,
        ).values_list('project_id', flat=True)
    )

    available_projects = all_course_projects.exclude(pk__in=enrolled_project_ids | pending_project_ids)

    recent_requests = CourseEnrollmentRequest.objects.filter(
        requester_username=username,
    ).select_related('project').order_by('-submitted_at')[:20]

    form = None
    if available_projects.exists():
        if request.method == 'POST':
            form = CourseEnrollmentRequestForm(request.POST, available_projects=available_projects)
            if form.is_valid():
                enrollment = CourseEnrollmentRequest.objects.create(
                    requester_username=username,
                    project=form.cleaned_data['project'],
                    motivation=form.cleaned_data.get('motivation', ''),
                )
                _send_enrollment_admin_notification(enrollment, request)
                messages.success(request, f'Enrollment request for "{enrollment.project.title}" submitted.')
                return redirect('course-enrollment-request')
        else:
            form = CourseEnrollmentRequestForm(available_projects=available_projects)

    return render(request, 'portal/course_enrollment_request.html', {
        'current_role': current_role,
        'all_course_projects': all_course_projects,
        'enrolled_project_ids': enrolled_project_ids,
        'pending_project_ids': pending_project_ids,
        'available_projects': available_projects,
        'recent_requests': recent_requests,
        'form': form,
    })


_ENROLLMENT_SORT_FIELDS = {'requester_username', 'status', 'submitted_at'}


class CourseEnrollmentRequestListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = CourseEnrollmentRequest
    template_name = 'portal/course_enrollment_request_list.html'
    context_object_name = 'enrollment_requests'
    paginate_by = 25

    def test_func(self):
        return self.request.user.is_superuser

    def _get_sort_params(self):
        order_by = self.request.GET.get('order_by', 'submitted_at')
        direction = self.request.GET.get('direction', 'desc')
        if order_by not in _ENROLLMENT_SORT_FIELDS:
            order_by = 'submitted_at'
        if direction not in ('asc', 'desc'):
            direction = 'desc'
        return order_by, direction

    def get_queryset(self):
        qs = CourseEnrollmentRequest.objects.select_related('project', 'processed_by')
        if not self.request.GET:
            return qs.filter(status=CourseEnrollmentRequest.STATUS_PENDING).order_by('-submitted_at')
        form = CourseEnrollmentRequestSearchForm(self.request.GET)
        if form.is_valid():
            data = form.cleaned_data
            if data.get('username'):
                qs = qs.filter(requester_username__icontains=data['username'])
            if data.get('status'):
                qs = qs.filter(status=data['status'])
        order_by, direction = self._get_sort_params()
        prefix = '' if direction == 'asc' else '-'
        return qs.order_by(f'{prefix}{order_by}')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = CourseEnrollmentRequestSearchForm(self.request.GET)
        ctx['search_form'] = form
        filter_parameters = ''
        if form.is_valid():
            for key, value in form.cleaned_data.items():
                if value:
                    filter_parameters += f'{key}={value}&'
        ctx['expand_accordion'] = 'show' if filter_parameters else ''
        order_by, direction = self._get_sort_params()
        ctx['current_order_by'] = order_by
        ctx['current_direction'] = direction
        ctx['filter_parameters'] = filter_parameters + f'order_by={order_by}&direction={direction}'
        ctx['filter_only_parameters'] = filter_parameters.rstrip('&')
        return ctx


@login_required
def course_enrollment_request_detail(request, pk):
    if not request.user.is_superuser:
        return HttpResponseForbidden()
    enrollment = get_object_or_404(CourseEnrollmentRequest, pk=pk)
    return render(request, 'portal/course_enrollment_request_detail.html', {'enrollment': enrollment})


@login_required
def course_enrollment_request_approve(request, pk):
    if not request.user.is_superuser:
        return HttpResponseForbidden()
    if request.method != 'POST':
        return redirect('course-enrollment-request-detail', pk=pk)
    enrollment = get_object_or_404(CourseEnrollmentRequest, pk=pk)
    if enrollment.status != CourseEnrollmentRequest.STATUS_PENDING:
        messages.warning(request, 'This request is not pending.')
        return redirect('course-enrollment-request-detail', pk=pk)

    try:
        _enroll_user_in_project(enrollment.requester_username, enrollment.project)
    except Exception as e:
        logger.exception('Failed to enroll %s in %s: %s', enrollment.requester_username, enrollment.project, e)
        messages.error(request, f'Enrollment failed: {e}')
        return redirect('course-enrollment-request-detail', pk=pk)

    enrollment.status = CourseEnrollmentRequest.STATUS_APPROVED
    enrollment.processed_at = timezone.now()
    enrollment.processed_by = request.user
    enrollment.save(update_fields=['status', 'processed_at', 'processed_by'])

    _send_enrollment_user_email(enrollment, approved=True)
    messages.success(request, f'{enrollment.requester_username} enrolled in "{enrollment.project.title}".')
    return redirect('course-enrollment-request-detail', pk=pk)


@login_required
def course_enrollment_request_reject(request, pk):
    if not request.user.is_superuser:
        return HttpResponseForbidden()
    if request.method != 'POST':
        return redirect('course-enrollment-request-detail', pk=pk)
    enrollment = get_object_or_404(CourseEnrollmentRequest, pk=pk)
    if enrollment.status != CourseEnrollmentRequest.STATUS_PENDING:
        messages.warning(request, 'This request is not pending.')
        return redirect('course-enrollment-request-detail', pk=pk)

    enrollment.status = CourseEnrollmentRequest.STATUS_REJECTED
    enrollment.processed_at = timezone.now()
    enrollment.processed_by = request.user
    enrollment.rejection_reason = request.POST.get('rejection_reason', '').strip()
    enrollment.save(update_fields=['status', 'processed_at', 'processed_by', 'rejection_reason'])

    _send_enrollment_user_email(enrollment, approved=False)
    messages.success(request, f'Enrollment for {enrollment.requester_username} rejected.')
    return redirect('course-enrollment-request-detail', pk=pk)
