# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from martor.models import MartorField


class BroadcastEmail(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_SENDING = 'sending'
    STATUS_SENT = 'sent'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_SENDING, 'Sending'),
        (STATUS_SENT, 'Sent'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    subject = models.CharField(max_length=255)
    body = MartorField()
    sender = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='sent_broadcasts')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    create_news = models.BooleanField(default=False)
    news = models.ForeignKey(
        'portal.News', on_delete=models.SET_NULL, null=True, blank=True, related_name='broadcast_emails'
    )
    total_recipients = models.IntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.subject} ({self.get_status_display()})"

    @property
    def sent_count(self):
        return self.recipients.filter(status=BroadcastEmailRecipient.STATUS_SENT).count()

    @property
    def failed_count(self):
        return self.recipients.filter(status=BroadcastEmailRecipient.STATUS_FAILED).count()

    @property
    def pending_count(self):
        return self.recipients.filter(status=BroadcastEmailRecipient.STATUS_PENDING).count()

    @property
    def progress_percent(self):
        if self.total_recipients == 0:
            return 0
        return int((self.sent_count + self.failed_count) / self.total_recipients * 100)


class BroadcastEmailRecipient(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_SENT, 'Sent'),
        (STATUS_FAILED, 'Failed'),
    ]

    broadcast = models.ForeignKey(BroadcastEmail, on_delete=models.CASCADE, related_name='recipients')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    email = models.EmailField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"{self.email} — {self.get_status_display()}"
