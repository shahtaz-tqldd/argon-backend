from django.db import models


class DiscountType(models.TextChoices):
    PERCENTAGE = "percentage", "Percentage"
    FIXED_AMOUNT = "fixed_amount", "Fixed amount"


class DiscountDuration(models.TextChoices):
    ONCE = "once", "Once"
    REPEATING = "repeating", "Repeating"
    FOREVER = "forever", "Forever"
