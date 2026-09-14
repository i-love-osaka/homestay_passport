from django.urls import path

from passport import views

urlpatterns = [
    path(
        "continue-upload/",
        views.passport_continue_upload,
        name="passport_continue_upload",
    ),
    path("result/<int:id>/", views.passport_result, name="passport_result"),
    path("<str:house_code>/", views.passport_form, name="passport_form"),
]
