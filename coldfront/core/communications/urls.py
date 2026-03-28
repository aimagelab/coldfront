# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from django.urls import path

from coldfront.core.communications import views

urlpatterns = [
    path('', views.broadcast_list, name='broadcast-list'),
    path('create/', views.broadcast_create, name='broadcast-create'),
    path('confirm/', views.broadcast_confirm, name='broadcast-confirm'),
    path('<int:pk>/', views.broadcast_detail, name='broadcast-detail'),
    path('<int:pk>/cancel/', views.broadcast_cancel, name='broadcast-cancel'),
    path('<int:pk>/progress/', views.broadcast_progress, name='broadcast-progress'),
]
