from django.urls import path

from portal import views

urlpatterns = [
    path("", views.home, name="home"),
    path("sign-in/", views.sign_in, name="sign-in"),
    path("auth/session/", views.create_session, name="create-session"),
    path("sign-out/", views.sign_out, name="sign-out"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("equipment/", views.catalog, name="catalog"),
    path("equipment/<slug:slug>/", views.resource_detail, name="resource-detail"),
    path("faculty/", views.faculty, name="faculty"),
    path("faculty/requests/<slug:request_id>/explanation/", views.faculty_explanation, name="faculty-explanation"),
    path("faculty/requests/<slug:request_id>/decision/", views.faculty_decision, name="faculty-decision"),
    path("student/history/", views.student_history, name="student-history"),
]
