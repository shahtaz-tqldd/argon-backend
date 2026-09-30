from django.urls import path

from coupon.api.v1.admin import views

urlpatterns = [
    path("", views.CouponListAPIView.as_view(), name="coupon-list"),
    path("create/", views.CouponCreateAPIView.as_view(), name="coupon-create"),
    path(
        "<uuid:coupon_id>/update/",
        views.CouponUpdateAPIView.as_view(),
        name="coupon-update",
    ),
    path(
        "<uuid:coupon_id>/delete/",
        views.CouponDeleteAPIView.as_view(),
        name="coupon-delete",
    ),
    path(
        "<uuid:coupon_id>/usage/",
        views.CouponUsageAPIView.as_view(),
        name="coupon-usage",
    ),
    path(
        "<uuid:coupon_id>/redemptions/",
        views.CouponRedemptionListAPIView.as_view(),
        name="coupon-redemptions",
    ),
]
