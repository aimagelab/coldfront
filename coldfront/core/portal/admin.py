# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from django.contrib import admin
from django.contrib.admin.models import LogEntry
from coldfront.core.portal.models import Carousel, News, DocumentationArticle, AccountOnboardingRequest


@admin.register(LogEntry)
class LogEntryAdmin(admin.ModelAdmin):
    list_display = (
        "content_type",
        "user",
        "action_time",
        "object_id",
        "object_repr",
        "action_flag",
        "change_message",
    )

    search_fields = ["user__username", "user__first_name", "user__last_name"]

admin.site.register(Carousel)
admin.site.register(News)
admin.site.register(DocumentationArticle)


@admin.register(AccountOnboardingRequest)
class AccountOnboardingRequestAdmin(admin.ModelAdmin):
    list_display = ('username', 'role', 'course_project', 'status', 'expiration_date', 'submitted_at')
    list_filter = ('status', 'role')
    search_fields = ('user__username', 'user__email', 'given_name', 'surname', 'email')

    def get_readonly_fields(self, request, obj=None):
        """If the request has already been provisioned to LDAP, lock all fields."""
        if obj and getattr(obj, 'processed_in_ldap', False):
            return [f.name for f in obj._meta.fields]
        return super().get_readonly_fields(request, obj)
