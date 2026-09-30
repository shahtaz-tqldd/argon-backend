from django.db import transaction
from django.db.models import Count, Max, Q
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated

from app.utils.pagination import CustomPagination
from app.utils.permission import IsSuperAdmin
from app.utils.response import APIResponse
from coupon.api.v1.admin.serializers import (
    CouponRedemptionAdminSerializer,
    CouponSerializer,
)
from coupon.models import Coupon
from coupon.services import (
    coupon_redemptions_queryset,
    coupon_usage_summary,
)


class CouponObjectMixin:
    def get_coupon(self):
        return get_object_or_404(Coupon, pk=self.kwargs["coupon_id"])


class CouponListAPIView(GenericAPIView):
    """List coupons with redemption counts and simple filters."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = CouponSerializer
    pagination_class = CustomPagination

    def get_queryset(self):
        now = timezone.now()
        queryset = (
            Coupon.objects.select_related("discount", "created_by", "updated_by")
            .prefetch_related("eligible_plans")
            .annotate(
                redemptions_count=Count("redemptions"),
                active_redemptions_count=Count(
                    "redemptions", filter=Q(redemptions__is_active=True)
                ),
                last_redeemed_at=Max("redemptions__redeemed_at"),
            )
        )
        params = self.request.query_params
        search = params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(code__icontains=search) | Q(name__icontains=search)
            )
        is_active = params.get("is_active")
        if is_active is not None and is_active != "":
            queryset = queryset.filter(
                is_active=str(is_active).strip().lower() in ("true", "1", "yes")
            )
        discount_type = params.get("discount_type", "").strip()
        if discount_type:
            queryset = queryset.filter(discount__discount_type=discount_type)
        validity = params.get("validity", "").strip()
        if validity == "current":
            queryset = queryset.filter(
                is_active=True,
                valid_from__lte=now,
            ).filter(Q(valid_until__isnull=True) | Q(valid_until__gte=now))
        elif validity == "scheduled":
            queryset = queryset.filter(valid_from__gt=now)
        elif validity == "expired":
            queryset = queryset.filter(valid_until__lt=now)
        return queryset

    def get(self, request, *args, **kwargs):
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(self.get_queryset(), request, view=self)
        return APIResponse.success(
            data=self.get_serializer(page, many=True).data,
            meta={
                "count": paginator.page.paginator.count,
                "page": paginator.page.number,
                "page_size": paginator.get_page_size(request),
                "num_pages": paginator.page.paginator.num_pages,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
            },
            message="Coupons fetched successfully.",
        )


class CouponCreateAPIView(GenericAPIView):
    """Create a coupon (with a nested or existing discount) as a superadmin."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = CouponSerializer

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        coupon = serializer.save(
            created_by=request.user,
            updated_by=request.user,
        )
        return APIResponse.success(
            data=self.get_serializer(coupon).data,
            message="Coupon created successfully.",
            status=status.HTTP_201_CREATED,
        )


class CouponUpdateAPIView(CouponObjectMixin, GenericAPIView):
    """Partially or fully update a coupon as a superadmin."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = CouponSerializer

    def put(self, request, *args, **kwargs):
        return self._update(request, partial=False)

    def patch(self, request, *args, **kwargs):
        return self._update(request, partial=True)

    @transaction.atomic
    def _update(self, request, *, partial):
        coupon = self.get_coupon()
        serializer = self.get_serializer(coupon, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        coupon = serializer.save(updated_by=request.user)
        return APIResponse.success(
            data=self.get_serializer(coupon).data,
            message="Coupon updated successfully.",
        )


class CouponDeleteAPIView(CouponObjectMixin, GenericAPIView):
    """Delete a coupon unless redemption history still references it."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]

    @transaction.atomic
    def delete(self, request, *args, **kwargs):
        coupon = self.get_coupon()
        coupon_id = str(coupon.id)
        try:
            coupon.delete()
        except ProtectedError:
            return APIResponse.error(
                errors={
                    "coupon": [
                        "This coupon cannot be deleted because redemption "
                        "records reference it. Deactivate it instead."
                    ]
                },
                message="Coupon is in use.",
                status=status.HTTP_409_CONFLICT,
            )

        return APIResponse.success(
            data={"id": coupon_id},
            message="Coupon deleted successfully.",
        )


class CouponUsageAPIView(CouponObjectMixin, GenericAPIView):
    """Aggregated redemption usage for one coupon."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def get(self, request, *args, **kwargs):
        coupon = self.get_coupon()
        return APIResponse.success(
            data={
                "coupon": CouponSerializer(coupon).data,
                "usage": coupon_usage_summary(coupon),
            },
            message="Coupon usage fetched successfully.",
        )


class CouponRedemptionListAPIView(CouponObjectMixin, GenericAPIView):
    """Paginated redemption history for one coupon."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = CouponRedemptionAdminSerializer
    pagination_class = CustomPagination

    def get(self, request, *args, **kwargs):
        coupon = self.get_coupon()
        params = request.query_params
        is_active = params.get("is_active")
        is_active_value = None
        if is_active is not None and is_active != "":
            is_active_value = str(is_active).lower() in ("true", "1", "yes")
        queryset = coupon_redemptions_queryset(
            coupon,
            is_active=is_active_value,
            currency=params.get("currency", ""),
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return APIResponse.success(
            data=self.get_serializer(page, many=True).data,
            meta={
                "count": paginator.page.paginator.count,
                "page": paginator.page.number,
                "page_size": paginator.get_page_size(request),
                "num_pages": paginator.page.paginator.num_pages,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
            },
            message="Coupon redemptions fetched successfully.",
        )
