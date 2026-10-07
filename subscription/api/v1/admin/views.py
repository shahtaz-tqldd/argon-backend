from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated

from app.utils.pagination import CustomPagination
from app.utils.permission import IsSuperAdmin
from app.utils.response import APIResponse
from subscription.api.v1.admin.serializers import (
    EnterprisePlanRequestApproveSerializer,
    EnterprisePlanRequestFilterSerializer,
    EnterprisePlanRequestSubscriptionSummarySerializer,
    SubscriptionPlanSerializer,
)
from subscription.api.v1.client.serializers import (
    EnterprisePlanRequestSerializer,
)
from subscription.models import EnterprisePlanRequest, SubscriptionPlan
from subscription.services.enterprise_plans import (
    EnterprisePlanRequestError,
    approve_enterprise_plan_request,
)


class SubscriptionPlanObjectMixin:
    def get_plan(self):
        return get_object_or_404(SubscriptionPlan, pk=self.kwargs["plan_id"])


class SubscriptionPlanCreateAPIView(GenericAPIView):
    """Create a subscription plan as a superadmin."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = SubscriptionPlanSerializer

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan = serializer.save(
            created_by=request.user,
            updated_by=request.user,
        )
        return APIResponse.success(
            data=self.get_serializer(plan).data,
            message="Subscription plan created successfully.",
            status=status.HTTP_201_CREATED,
        )


class SubscriptionPlanUpdateAPIView(SubscriptionPlanObjectMixin, GenericAPIView):
    """Partially or fully update a subscription plan as a superadmin."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = SubscriptionPlanSerializer

    def put(self, request, *args, **kwargs):
        return self._update(request, partial=False)

    def patch(self, request, *args, **kwargs):
        return self._update(request, partial=True)

    @transaction.atomic
    def _update(self, request, *, partial):
        plan = self.get_plan()
        serializer = self.get_serializer(
            plan,
            data=request.data,
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)
        plan = serializer.save(updated_by=request.user)
        return APIResponse.success(
            data=self.get_serializer(plan).data,
            message="Subscription plan updated successfully.",
        )


class SubscriptionPlanDeleteAPIView(SubscriptionPlanObjectMixin, GenericAPIView):
    """Delete a plan unless protected billing records still reference it."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]

    @transaction.atomic
    def delete(self, request, *args, **kwargs):
        plan = self.get_plan()
        plan_id = str(plan.id)
        try:
            plan.delete()
        except ProtectedError:
            return APIResponse.error(
                errors={
                    "plan": [
                        "This plan cannot be deleted because subscription or "
                        "payment records reference it. Deactivate it instead."
                    ]
                },
                message="Subscription plan is in use.",
                status=status.HTTP_409_CONFLICT,
            )

        return APIResponse.success(
            data={"id": plan_id},
            message="Subscription plan deleted successfully.",
        )


class EnterprisePlanRequestListAPIView(GenericAPIView):
    """List enterprise plan requests for superadmin review."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = EnterprisePlanRequestSerializer
    pagination_class = CustomPagination

    def get_queryset(self):
        queryset = EnterprisePlanRequest.objects.select_related(
            "chatbot",
            "chatbot__workspace",
            "reviewed_by",
            "subscription",
            "subscription__plan_price__plan",
        )
        filters = EnterprisePlanRequestFilterSerializer(
            data=self.request.query_params
        )
        filters.is_valid(raise_exception=True)
        if filters.validated_data.get("status"):
            queryset = queryset.filter(
                status=filters.validated_data["status"]
            )
        if filters.validated_data.get("chatbot"):
            queryset = queryset.filter(
                chatbot__slug=filters.validated_data["chatbot"]
            )
        return queryset.order_by("-created_at")

    def get(self, request, *args, **kwargs):
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(
            self.get_queryset(), request, view=self
        )
        serializer = self.get_serializer(page, many=True)
        return APIResponse.success(
            data=serializer.data,
            meta={
                "count": paginator.page.paginator.count,
                "page": paginator.page.number,
                "page_size": paginator.get_page_size(request),
                "num_pages": paginator.page.paginator.num_pages,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
            },
            message="Enterprise plan requests fetched successfully.",
        )


class EnterprisePlanRequestApproveAPIView(GenericAPIView):
    """Approve a request and grant a custom subscription contract."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = EnterprisePlanRequestApproveSerializer

    def post(self, request, *args, **kwargs):
        plan_request = get_object_or_404(
            EnterprisePlanRequest,
            pk=self.kwargs["request_id"],
        )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            plan_request, subscription = approve_enterprise_plan_request(
                request=plan_request,
                user=request.user,
                **serializer.validated_data,
            )
        except EnterprisePlanRequestError as exc:
            return APIResponse.error(
                message=str(exc),
                status=status.HTTP_409_CONFLICT,
            )
        return APIResponse.success(
            data={
                "request": EnterprisePlanRequestSerializer(plan_request).data,
                "subscription": (
                    EnterprisePlanRequestSubscriptionSummarySerializer(
                        subscription
                    ).data
                ),
            },
            message="Enterprise plan request approved successfully.",
            status=status.HTTP_200_OK,
        )
