# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from django.contrib import admin
from django.contrib.admin.models import LogEntry
from coldfront.core.portal.models import Carousel, News, DocumentationArticle, AccountOnboardingRequest
from django.urls import path
from django.utils.html import format_html
from django.contrib import messages
from django.utils import timezone
from django.shortcuts import redirect


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
    list_display = ('username','role','course_project', 'status','expiration_date','submitted_at','approve_button','reject_button')
    list_filter = ('status','role')
    search_fields = ('user__username','user__email','given_name','surname','email')
    actions = ['approve_requests','reject_requests']

    def get_readonly_fields(self, request, obj=None):
        """If the request has already been provisioned to LDAP, lock all fields."""
        if obj and getattr(obj, 'processed_in_ldap', False):
            base_fields = [f.name for f in obj._meta.fields]
            # Include custom display-only buttons to avoid admin errors
            return base_fields + ['approve_button', 'reject_button']
        return super().get_readonly_fields(request, obj)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path('<int:pk>/approve/', self.admin_site.admin_view(self.process_approve), name='portal_accountonboardingrequest_approve'),
            path('<int:pk>/reject/', self.admin_site.admin_view(self.process_reject), name='portal_accountonboardingrequest_reject'),
        ]
        return custom + urls

    def approve_button(self, obj):
        if obj.status == AccountOnboardingRequest.STATUS_APPROVED:
            return format_html('<span>Approved</span>')
        if not obj.processed_in_ldap:
            return format_html('<a class="button" style="background-color: #28a745; color: white;" href="{}">Approve</a>', f"{obj.pk}/approve/")
        return '—'
    approve_button.short_description = "Approve"

    def reject_button(self, obj):
        if obj.status == AccountOnboardingRequest.STATUS_REJECTED:
            return format_html('<span>Rejected</span>')
        if not obj.processed_in_ldap:
            return format_html('<a class="button" style="background-color: #dc3545; color: white;" href="{}">Reject</a>', f"{obj.pk}/reject/")
        return '—'
    reject_button.short_description = "Reject"

    def process_approve(self, request, pk, *args, **kwargs):
        obj = self.get_object(request, pk)
        if obj and not obj.processed_in_ldap:
            obj.status = obj.STATUS_APPROVED
            obj.processed_at = timezone.now()
            obj.save(update_fields=['status','processed_at'])
            messages.success(request, f"Request {obj} approved.")
        return redirect('admin:portal_accountonboardingrequest_changelist')

    def process_reject(self, request, pk, *args, **kwargs):
        obj = self.get_object(request, pk)
        if obj and not obj.processed_in_ldap:
            obj.status = obj.STATUS_REJECTED
            obj.processed_at = timezone.now()
            obj.save(update_fields=['status','processed_at'])
            messages.success(request, f"Request {obj} rejected.")
        return redirect('admin:portal_accountonboardingrequest_changelist')

    def approve_requests(self, request, queryset):
        updated = queryset.exclude(status=AccountOnboardingRequest.STATUS_APPROVED).update(
            status=AccountOnboardingRequest.STATUS_APPROVED,
            processed_at=timezone.now()
        )
        self.message_user(request, f"{updated} request(s) approved.", messages.SUCCESS)
    approve_requests.short_description = "Approve selected requests"

    def reject_requests(self, request, queryset):
        updated = queryset.exclude(status=AccountOnboardingRequest.STATUS_REJECTED).update(
            status=AccountOnboardingRequest.STATUS_REJECTED,
            processed_at=timezone.now()
        )
        self.message_user(request, f"{updated} request(s) rejected.", messages.SUCCESS)
    reject_requests.short_description = "Reject selected requests"
