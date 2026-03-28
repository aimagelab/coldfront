# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import logging

from django.utils import timezone
from django_q.tasks import async_task

from coldfront.core.utils.common import import_from_settings
from coldfront.core.utils.mail import send_email

logger = logging.getLogger(__name__)

EMAIL_SENDER = import_from_settings("EMAIL_SENDER")
BROADCAST_BATCH_SIZE = 50


def send_broadcast_batch(broadcast_id):
    from coldfront.core.communications.models import BroadcastEmail, BroadcastEmailRecipient

    try:
        broadcast = BroadcastEmail.objects.get(pk=broadcast_id)
    except BroadcastEmail.DoesNotExist:
        logger.error("BroadcastEmail %s not found", broadcast_id)
        return

    if broadcast.status == BroadcastEmail.STATUS_CANCELLED:
        logger.info("BroadcastEmail %s was cancelled, aborting", broadcast_id)
        return

    if broadcast.status == BroadcastEmail.STATUS_PENDING:
        broadcast.status = BroadcastEmail.STATUS_SENDING
        broadcast.started_at = timezone.now()
        broadcast.save(update_fields=['status', 'started_at'])

    pending = broadcast.recipients.filter(
        status=BroadcastEmailRecipient.STATUS_PENDING
    ).select_related('user')[:BROADCAST_BATCH_SIZE]

    for recipient in pending:
        try:
            send_email(broadcast.subject, broadcast.body, EMAIL_SENDER, [recipient.email])
            recipient.status = BroadcastEmailRecipient.STATUS_SENT
            recipient.sent_at = timezone.now()
        except Exception:
            logger.exception("Failed to send broadcast email to %s", recipient.email)
            recipient.status = BroadcastEmailRecipient.STATUS_FAILED
        recipient.save(update_fields=['status', 'sent_at'])

    still_pending = broadcast.recipients.filter(
        status=BroadcastEmailRecipient.STATUS_PENDING
    ).exists()

    if still_pending:
        async_task('coldfront.core.communications.tasks.send_broadcast_batch', broadcast_id)
        logger.info(
            "BroadcastEmail %s: batch done, %d sent, %d failed so far. Queued next batch.",
            broadcast_id, broadcast.sent_count, broadcast.failed_count,
        )
    else:
        broadcast.status = BroadcastEmail.STATUS_SENT
        broadcast.completed_at = timezone.now()
        broadcast.save(update_fields=['status', 'completed_at'])

        if broadcast.create_news and broadcast.news is None:
            from coldfront.core.portal.models import News
            news = News.objects.create(title=broadcast.subject, body=broadcast.body)
            broadcast.news = news
            broadcast.save(update_fields=['news'])
            logger.info("BroadcastEmail %s: created News pk=%s", broadcast_id, news.pk)

        logger.info(
            "BroadcastEmail %s complete: %d sent, %d failed",
            broadcast_id, broadcast.sent_count, broadcast.failed_count,
        )
