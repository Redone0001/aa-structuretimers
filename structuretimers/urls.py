"""Routes."""

from django.urls import path

from . import views, campaigns

app_name = "structuretimers"

urlpatterns = [
    path(
        "campaigns/<int:pk>/map/",
        campaigns.CampaignMapDataView.as_view(),
        name="campaign_map_data",
    ),
    path(
        "campaigns/<int:pk>/status/",
        campaigns.CampaignStatusView.as_view(),
        name="campaign_status",
    ),
    path("campaigns/", campaigns.CampaignListView.as_view(), name="campaign_list"),
    path(
        "campaigns/create/",
        campaigns.CampaignCreateView.as_view(),
        name="campaign_create",
    ),
    path(
        "campaigns/<int:pk>/",
        campaigns.CampaignDetailView.as_view(),
        name="campaign_detail",
    ),
    path(
        "campaigns/<int:pk>/systems/<int:entry_pk>/add/",
        campaigns.CampaignReconView.as_view(),
        name="campaign_recon_add",
    ),
    path(
        "campaigns/<int:pk>/systems/<int:entry_pk>/timers/<int:timer_pk>/",
        campaigns.CampaignReconView.as_view(),
        name="campaign_recon_edit",
    ),
    path("", views.TimerListView.as_view(), name="timer_list"),
    path("recon/data/", views.ManageReconDataView.as_view(), name="recon_data"),
    path(
        "recon/<int:pk>/<str:action>/",
        views.ReconActionView.as_view(),
        name="recon_action",
    ),
    path("add_recon/", views.CreateReconView.as_view(), name="add_recon"),
    path("add/", views.CreateTimerView.as_view(), name="add"),
    path("add_fast/", views.FastCreateTimerView.as_view(), name="add_fast"),
    path("remove/<int:pk>", views.RemoveTimerView.as_view(), name="delete"),
    path("edit/<int:pk>", views.EditTimerView.as_view(), name="edit"),
    path("copy/<int:pk>", views.CopyTimerView.as_view(), name="copy"),
    path(
        "list_data/<str:tab_name>",
        views.TimerListDataView.as_view(),
        name="timer_list_data",
    ),
    path("detail/<str:pk>", views.TimerDetailDataView.as_view(), name="detail"),
    path(
        "select2_solar_systems/",
        views.Select2SolarSystemsView.as_view(),
        name="select2_solar_systems",
    ),
    path(
        "select2_structure_types/",
        views.Select2StructureTypesView.as_view(),
        name="select2_structure_types",
    ),
]
