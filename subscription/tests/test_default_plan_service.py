from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from subscription.choices import BillingInterval, PaymentProvider
from subscription.models import PlanPrice
from subscription.services.subscriptions import (
    DefaultFreePlanNotConfigured,
    get_default_free_plan_price,
)


class DefaultFreePlanServiceTests(SimpleTestCase):
    def test_returns_the_canonical_free_price(self):
        price = Mock()
        query = Mock()
        query.get.return_value = price

        with patch.object(
            PlanPrice.objects,
            "select_related",
            return_value=query,
        ):
            result = get_default_free_plan_price()

        self.assertIs(result, price)
        query.get.assert_called_once_with(
            plan__slug="free",
            plan__is_free=True,
            plan__is_active=True,
            provider=PaymentProvider.MANUAL,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            amount=0,
            is_active=True,
        )

    def test_raises_a_configuration_error_when_free_price_is_missing(self):
        query = Mock()
        query.get.side_effect = PlanPrice.DoesNotExist

        with (
            patch.object(
                PlanPrice.objects,
                "select_related",
                return_value=query,
            ),
            self.assertRaises(DefaultFreePlanNotConfigured),
        ):
            get_default_free_plan_price()
