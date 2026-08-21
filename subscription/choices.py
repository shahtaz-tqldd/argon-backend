from django.db import models


class PlanFeature(models.TextChoices):
    KNOWLEDGE_BASE = "knowledge_base", "Knowledge base"
    HUMAN_HANDOFF = "human_handoff", "Human handoff"
    LEAD_CAPTURE = "lead_capture", "Lead capture"
    APPOINTMENT_BOOKING = "appointment_booking", "Appointment booking"
    QUOTATION_GENERATION = "quotation_generation", "Quotation generation"
    ORDER_TAKING = "order_taking", "Order taking"


class PlanType(models.TextChoices):
    STANDARD = "standard", "Standard"
    CUSTOM = "custom", "Custom"
    ENTERPRISE = "enterprise", "Enterprise"


class BillingInterval(models.TextChoices):
    MONTHLY = "monthly", "Monthly"
    ANNUAL = "annual", "Annual"
    CUSTOM = "custom", "Custom"


class PaymentProvider(models.TextChoices):
    STRIPE = "stripe", "Stripe"
    BKASH = "bkash", "bKash"
    MANUAL = "manual", "Manual"


class RenewalMode(models.TextChoices):
    PROVIDER_MANAGED = "provider_managed", "Provider managed"
    APP_MANAGED = "app_managed", "Application managed"
    MANUAL = "manual", "Manual"


class SubscriptionStatus(models.TextChoices):
    INCOMPLETE = "incomplete", "Incomplete"
    ACTIVE = "active", "Active"
    PAST_DUE = "past_due", "Past due"
    PAUSED = "paused", "Paused"
    CANCELED = "canceled", "Canceled"
    UNPAID = "unpaid", "Unpaid"


class PaymentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    REQUIRES_ACTION = "requires_action", "Requires action"
    PROCESSING = "processing", "Processing"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELED = "canceled", "Canceled"
    PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"
    REFUNDED = "refunded", "Refunded"


class PaymentType(models.TextChoices):
    SUBSCRIPTION = "subscription", "Subscription"
    OVERAGE = "overage", "Usage overage"
    ONE_TIME = "one_time", "One-time"


class WebhookProcessingStatus(models.TextChoices):
    RECEIVED = "received", "Received"
    PROCESSING = "processing", "Processing"
    PROCESSED = "processed", "Processed"
    FAILED = "failed", "Failed"
    IGNORED = "ignored", "Ignored"
