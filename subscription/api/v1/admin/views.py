from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated

from app.utils.permission import IsSuperAdmin
from app.utils.response import APIResponse
from subscription.api.v1.admin.serializers import SubscriptionPlanSerializer
from subscription.models import SubscriptionPlan


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
