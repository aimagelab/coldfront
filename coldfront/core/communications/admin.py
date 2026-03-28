# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from django.contrib import admin

from coldfront.core.communications.models import BroadcastEmail, BroadcastEmailRecipient


class BroadcastEmailRecipientInline(admin.TabularInline):
    model = BroadcastEmailRecipient
    extra = 0
    readonly_fields = ('user', 'email', 'status', 'sent_at')
    can_delete = False
    max_num = 0


@admin.register(BroadcastEmail)
class BroadcastEmailAdmin(admin.ModelAdmin):
    list_display = ('subject', 'sender', 'status', 'total_recipients', 'sent_count', 'failed_count', 'created_at')
    list_filter = ('status',)
    readonly_fields = ('sender', 'status', 'total_recipients', 'sent_count', 'failed_count',
                       'created_at', 'started_at', 'completed_at', 'news')

    def sent_count(self, obj):
        return obj.sent_count
    sent_count.short_description = 'Sent'

    def failed_count(self, obj):
        return obj.failed_count
    failed_count.short_description = 'Failed'
    inlines = [BroadcastEmailRecipientInline]
