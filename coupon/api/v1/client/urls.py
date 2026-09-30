from django.urls import path

from coupon.api.v1.client import views

urlpatterns = [
    path("apply/", views.CouponApplyAPIView.as_view(), name="coupon-apply"),
    path("current/", views.CouponCurrentAPIView.as_view(), name="coupon-current"),
    path("remove/", views.CouponRemoveAPIView.as_view(), name="coupon-remove"),
]
