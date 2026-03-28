# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django_q.tasks import async_task

from coldfront.core.communications.forms import BroadcastEmailForm
from coldfront.core.communications.models import BroadcastEmail, BroadcastEmailRecipient
from coldfront.core.utils.common import import_from_settings

logger = logging.getLogger(__name__)

BROADCAST_TEST_USER = import_from_settings('BROADCAST_TEST_USER', '')


def _get_broadcast_recipients():
    qs = User.objects.filter(is_active=True, email__gt='')
    if BROADCAST_TEST_USER:
        qs = qs.filter(username=BROADCAST_TEST_USER)
    return qs


def _admin_check(user):
    return user.is_superuser


@login_required
@user_passes_test(_admin_check)
def broadcast_list(request):
    broadcasts = BroadcastEmail.objects.all().select_related('sender', 'news')
    paginator = Paginator(broadcasts, 20)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'communications/broadcast_list.html', {'page_obj': page})


@login_required
@user_passes_test(_admin_check)
def broadcast_create(request):
    active_user_count = _get_broadcast_recipients().count()

    if request.method == 'POST':
        form = BroadcastEmailForm(request.POST)
        if form.is_valid():
            # Store form data in session for the confirmation step
            request.session['broadcast_draft'] = {
                'subject': form.cleaned_data['subject'],
                'body': form.cleaned_data['body'],
                'create_news': form.cleaned_data['create_news'],
            }
            return redirect('broadcast-confirm')
    else:
        form = BroadcastEmailForm()

    return render(request, 'communications/broadcast_create.html', {
        'form': form,
        'active_user_count': active_user_count,
    })


@login_required
@user_passes_test(_admin_check)
def broadcast_confirm(request):
    draft = request.session.get('broadcast_draft')
    if not draft:
        return redirect('broadcast-create')

    active_users = _get_broadcast_recipients()
    recipient_count = active_users.count()

    if request.method == 'POST':
        if 'cancel' in request.POST:
            del request.session['broadcast_draft']
            return redirect('broadcast-create')

        broadcast = BroadcastEmail.objects.create(
            subject=draft['subject'],
            body=draft['body'],
            sender=request.user,
            status=BroadcastEmail.STATUS_PENDING,
            create_news=draft['create_news'],
            total_recipients=recipient_count,
        )

        if draft['create_news']:
            from coldfront.core.portal.models import News
            news = News.objects.create(title=draft['subject'], body=draft['body'])
            broadcast.news = news
            broadcast.save(update_fields=['news'])

        recipients = [
            BroadcastEmailRecipient(broadcast=broadcast, user=u, email=u.email)
            for u in active_users.iterator()
        ]
        BroadcastEmailRecipient.objects.bulk_create(recipients, batch_size=500)

        async_task('coldfront.core.communications.tasks.send_broadcast_batch', broadcast.pk)

        del request.session['broadcast_draft']
        messages.success(request, f'Broadcast "{broadcast.subject}" queued for {recipient_count} recipients.')
        return redirect('broadcast-detail', pk=broadcast.pk)

    return render(request, 'communications/broadcast_confirm.html', {
        'draft': draft,
        'recipient_count': recipient_count,
    })


@login_required
@user_passes_test(_admin_check)
def broadcast_detail(request, pk):
    broadcast = get_object_or_404(BroadcastEmail, pk=pk)
    recipients = broadcast.recipients.select_related('user').order_by('status', 'id')
    paginator = Paginator(recipients, 50)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'communications/broadcast_detail.html', {
        'broadcast': broadcast,
        'page_obj': page,
    })


@login_required
@user_passes_test(_admin_check)
def broadcast_progress(request, pk):
    from django.http import JsonResponse
    broadcast = get_object_or_404(BroadcastEmail, pk=pk)
    return JsonResponse({
        'status': broadcast.status,
        'total_recipients': broadcast.total_recipients,
        'sent_count': broadcast.sent_count,
        'failed_count': broadcast.failed_count,
        'progress_percent': broadcast.progress_percent,
    })


@login_required
@user_passes_test(_admin_check)
def broadcast_cancel(request, pk):
    broadcast = get_object_or_404(BroadcastEmail, pk=pk)
    if request.method == 'POST':
        if broadcast.status in (BroadcastEmail.STATUS_PENDING, BroadcastEmail.STATUS_SENDING):
            broadcast.recipients.filter(
                status=BroadcastEmailRecipient.STATUS_PENDING
            ).update(status=BroadcastEmailRecipient.STATUS_FAILED)
            broadcast.status = BroadcastEmail.STATUS_CANCELLED
            broadcast.completed_at = timezone.now()
            broadcast.save(update_fields=['status', 'completed_at'])
            messages.warning(request, f'Broadcast "{broadcast.subject}" cancelled.')
        else:
            messages.error(request, 'This broadcast cannot be cancelled.')
    return redirect('broadcast-detail', pk=pk)
