# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import operator
from collections import Counter

from django.conf import settings
from django.contrib.humanize.templatetags.humanize import intcomma
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import cache_page
from django.utils import timezone
from django.core.exceptions import ObjectDoesNotExist
from django.contrib import messages
from django.shortcuts import redirect
from django.core.mail import mail_admins, send_mail
from django.contrib.auth import get_user_model

from coldfront.core.portal.models import Carousel, News, DocumentationArticle
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
from .forms import OnboardingProcessForm
from .models import AccountOnboardingRequest
from django.http import Http404
from coldfront.core.utils.common import import_from_settings

ALLOCATION_EULA_ENABLE = import_from_settings("ALLOCATION_EULA_ENABLE", False)


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
    news = News.objects.filter(Q(expiry_date__gte=timezone.now()) | Q(expiry_date__isnull=True)).order_by('-publication_date')
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
    given_name = request.META.get('HTTP_X_REMOTE_FIRSTNAME')
    surname = request.META.get('HTTP_X_REMOTE_SURNAME')
    email = request.META.get('HTTP_X_REMOTE_EMAIL')
    unimore_id = request.META.get('HTTP_X_REMOTE_USER').split('@')[0]

    # Refuse if a user with this email already exists
    if email:
        UserModel = get_user_model()
        if UserModel.objects.filter(email__iexact=email).exists():
            messages.error(
                request,
                "An account with this email already exists. If you need changes or access issues resolved, open a support ticket."
            )
            return redirect('onboard')

    # Prevent multiple pending requests (by unimore_id)
    existing_pending = AccountOnboardingRequest.objects.filter(
        unimore_id=unimore_id,
        status=AccountOnboardingRequest.STATUS_PENDING
    ).first()

    course_projects = Project.objects.filter(project_type='F').order_by('title')

    if request.method == 'POST':
        if existing_pending:
            messages.warning(request, "You already have a pending request.")
            return redirect('onboard')
        form = OnboardingProcessForm(request.POST, course_queryset=course_projects)
        if form.is_valid():
            req_obj = form.save(commit=False)
            # Generate username base
            base_username = ''.join([n[0] for n in given_name.split() if n]) + surname.replace(' ', '')
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

    course_projects = Project.objects.filter(project_type='F').order_by('title')

    if request.method == 'POST':
        form = OnboardingProcessForm(request.POST, request.FILES, course_queryset=course_projects)
        # Flag for form validation that this is external
        form.initial['is_external'] = True
        if form.is_valid():
            data = form.cleaned_data
            given_name = request.POST.get('given_name', '').strip()
            surname = request.POST.get('surname', '').strip()
            email = request.POST.get('email', '').strip()
            if not given_name or not surname or not email:
                messages.error(request, 'Name, surname and email are required.')
            else:
                # Ensure no pending external request with same email
                if AccountOnboardingRequest.objects.filter(email__iexact=email, status=AccountOnboardingRequest.STATUS_PENDING, unimore_id__isnull=True).exists():
                    messages.warning(request, 'You already have a pending request with this email.')
                    return redirect('onboard')
                # Generate username from names
                base_username = (given_name.split()[0][0] + surname).lower().replace(' ', '')
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
