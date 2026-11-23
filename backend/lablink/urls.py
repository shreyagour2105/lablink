from django.urls import path
from portal import demo, views

urlpatterns = [
    path("", views.home, name="home"),
    path("demo/", demo.entry, name="demo"),
    path("demo/enter/", demo.enter, name="demo-enter"),
    path("demo/student/", demo.workspace, {"role": "student"}, name="demo-student"),
    path("demo/faculty/", demo.workspace, {"role": "faculty"}, name="demo-faculty"),
    path("demo/student/request/", demo.submit, name="demo-submit"),
    path(
        "demo/faculty/requests/<int:number>/decision/", demo.decide, name="demo-decide"
    ),
    path("sign-in/", views.sign_in, name="sign-in"),
    path("auth/session/", views.create_session, name="create-session"),
    path("sign-out/", views.sign_out, name="sign-out"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("equipment/", views.catalog, name="catalog"),
    path("equipment/<slug:slug>/", views.resource_detail, name="resource-detail"),
    path("faculty/", views.faculty, name="faculty"),
    path(
        "faculty/requests/<slug:request_id>/explanation/",
        views.faculty_explanation,
        name="faculty-explanation",
    ),
    path(
        "faculty/requests/<slug:request_id>/decision/",
        views.faculty_decision,
        name="faculty-decision",
    ),
    path("student/history/", views.student_history, name="student-history"),
]
