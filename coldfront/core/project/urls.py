# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from django.urls import path

import coldfront.core.project.views as project_views
import coldfront.core.project.proposal_views as proposal_views

urlpatterns = [
    path("<int:pk>/", project_views.ProjectDetailView.as_view(), name="project-detail"),
    path("<int:pk>/archive", project_views.ProjectArchiveProjectView.as_view(), name="project-archive"),
    path("", project_views.ProjectListView.as_view(), name="project-list"),
    path(
        "project-user-update-email-notification/",
        project_views.project_update_email_notification,
        name="project-user-update-email-notification",
    ),
    path("archived/", project_views.ProjectArchivedListView.as_view(), name="project-archived-list"),
    path("create/", project_views.ProjectCreateView.as_view(), name="project-create"),
    path("<int:pk>/update/", project_views.ProjectUpdateView.as_view(), name="project-update"),
    path(
        "<int:pk>/add-users-search/", project_views.ProjectAddUsersSearchView.as_view(), name="project-add-users-search"
    ),
    path(
        "<int:pk>/add-users-search-results/",
        project_views.ProjectAddUsersSearchResultsView.as_view(),
        name="project-add-users-search-results",
    ),
    path("<int:pk>/add-users/", project_views.ProjectAddUsersView.as_view(), name="project-add-users"),
    path("<int:pk>/remove-users/", project_views.ProjectRemoveUsersView.as_view(), name="project-remove-users"),
    path(
        "<int:pk>/user-detail/<int:project_user_pk>",
        project_views.ProjectUserDetail.as_view(),
        name="project-user-detail",
    ),
    path("<int:pk>/review/", project_views.ProjectReviewView.as_view(), name="project-review"),
    path("project-review-list", project_views.ProjectReviewListView.as_view(), name="project-review-list"),
    path(
        "project-review-complete/<int:project_review_pk>/",
        project_views.ProjectReviewCompleteView.as_view(),
        name="project-review-complete",
    ),
    path("project-review/<int:pk>/email", project_views.ProjectReviewEmailView.as_view(), name="project-review-email"),
    path("<int:pk>/projectnote/add", project_views.ProjectNoteCreateView.as_view(), name="project-note-add"),
    path(
        "<int:pk>/project-attribute-create/",
        project_views.ProjectAttributeCreateView.as_view(),
        name="project-attribute-create",
    ),
    path(
        "<int:pk>/project-attribute-delete/",
        project_views.ProjectAttributeDeleteView.as_view(),
        name="project-attribute-delete",
    ),
    path(
        "<int:pk>/project-attribute-update/<int:project_attribute_pk>",
        project_views.ProjectAttributeUpdateView.as_view(),
        name="project-attribute-update",
    ),
    # --- Project proposal flow (applicant) ---
    path("proposals/", proposal_views.ProposalListView.as_view(), name="proposal-list"),
    path("proposals/apply/", proposal_views.ProposalTypeSelectView.as_view(), name="proposal-type-select"),
    path("proposals/apply/<str:type_code>/", proposal_views.ProposalCreateView.as_view(), name="proposal-create"),
    path("proposals/<int:pk>/submitted/", proposal_views.ProposalSubmittedView.as_view(), name="proposal-submitted"),
    # --- Proposal review flow (admin) ---
    path("proposals/admin/", proposal_views.ProposalAdminListView.as_view(), name="proposal-admin-list"),
    path("proposals/<int:pk>/admin/", proposal_views.ProposalAdminDetailView.as_view(), name="proposal-admin-detail"),
    path("proposals/<int:pk>/nominate/", proposal_views.ProposalNominateView.as_view(), name="proposal-nominate"),
    path("proposals/<int:pk>/decision/", proposal_views.ProposalDecisionView.as_view(), name="proposal-decision"),
    # --- Proposal review flow (reviewer) ---
    path("proposals/review/<uuid:token>/", proposal_views.ReviewInvitationView.as_view(), name="review-invitation"),
    path("proposals/review/<uuid:token>/write/", proposal_views.ReviewWriteView.as_view(), name="review-write"),
]
