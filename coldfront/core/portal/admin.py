from django.contrib import admin
from django.contrib.admin.models import LogEntry
from coldfront.core.portal.models import Carousel, News


@admin.register(LogEntry)
class LogEntryAdmin(admin.ModelAdmin):
    list_display = ('content_type',
        'user',
        'action_time',
        'object_id',
        'object_repr',
        'action_flag',
        'change_message',)

    search_fields = ['user__username', 'user__first_name', 'user__last_name']

admin.site.register(Carousel)
admin.site.register(News)